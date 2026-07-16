"""Settings + role → LLM resolution.

Mirrors the TS profile fallback chain:
    explicit binding → role-specific env → global default → hard fallback

Provider routing:
    `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` switch any "anthropic-shaped"
    model (Claude family, or Anthropic-compatible gateways like Volcano Ark) to
    a custom endpoint. Native Anthropic just uses `ANTHROPIC_API_KEY`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from agent_room.budget import Budget
from agent_room.context import ContextEngine, NoOpContextEngine
from agent_room.guardrail import Guardrail, GuardrailMode
from agent_room.memory import (
    EmbeddingBackend,
    HashingEmbeddingBackend,
    MemoryProvider,
    NoOpMemoryProvider,
    VectorIndex,
)
from agent_room.schemas import Role

_DEFAULT_FALLBACK = "claude-sonnet-4-6"


@dataclass
class Settings:
    db_path: str = "./agent_room.db"
    host: str = "127.0.0.1"
    port: int = 8765
    default_model: str = _DEFAULT_FALLBACK
    role_models: dict[Role, str] = field(default_factory=dict)

    anthropic_base_url: str | None = None
    anthropic_auth_token: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Server's tool-using developer (ReAct). The default graph grants the
    # developer the built-in tools, confined to `workspace_dir`. Shell and
    # mutation are disabled unless the operator explicitly opts in: an empty
    # allowlist drops ShellTool and read_only filters write_text/MCP mutations.
    # See agent_room/server/react_runtime.py.
    graph_preset: str = "full_react"
    workspace_dir: str = "./workspace"
    shell_allowlist: tuple[str, ...] = ()
    # Direct subprocess execution happens in the credential-bearing worker and
    # is therefore unsafe for untrusted model input. It remains available only
    # as an explicit local-development escape hatch until sandbox-runner owns it.
    allow_direct_shell: bool = False
    tool_mode: str = "read_only"
    sandbox_runner_url: str | None = None
    sandbox_runner_token: str | None = None

    # Cross-session memory (v0.5). When on, the server binds a
    # FileFtsMemoryProvider (curated MEMORY.md/USER.md + SQLite/FTS5 transcript)
    # and gives the developer the `memory` tool. Off → NoOp (no persistence).
    memory_enabled: bool = True

    # Hybrid semantic recall (v1.x §6.9-4 / PLAN.md §6.14). Off by default —
    # `TranscriptStore` stays FTS5/LIKE-only (v0.5 behavior, byte-for-byte).
    # On: binds `HashingEmbeddingBackend` (dependency-free character n-gram
    # hashing, not a deep/transformer embedding — see agent_room/memory/
    # embedding.py) and a sqlite-vec KNN index fused via reciprocal rank
    # fusion. Only takes effect when `memory_enabled` is also true.
    memory_vector: bool = False

    # Vector recall backend (v1.x §6.15 / PLAN.md §6.15). Only consulted when
    # `memory_vector` is true. "sqlite" (default) keeps the zero-dependency
    # HashingEmbeddingBackend + sqlite-vec path from §6.14 byte-for-byte.
    # "qdrant" swaps in FastEmbedBackend (real transformer embeddings, `pip
    # install 'agent-room[vector]'`) + QdrantVectorIndex. See
    # agent_room/memory/{qdrant_index,fastembed_backend}.py.
    vector_backend: str = "sqlite"
    embedding_model: str = "jinaai/jina-embeddings-v2-base-zh"
    qdrant_path: str = "./.agent_room_qdrant"
    qdrant_url: str | None = None

    # SSE heartbeat: send a `: ping` comment every N seconds so reverse proxies /
    # gateways don't drop an idle stream during a long silent LLM call.
    sse_ping_seconds: int = 15

    # MCP (Model Context Protocol) servers to load tools from, keyed by server
    # name → langchain-mcp-adapters connection config. Empty (default) = no MCP
    # tools registered, zero behavior change. See agent_room/tools/mcp.py and
    # AGENT_ROOM_MCP_SERVERS in .env.example.
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Per-task budget ceiling (v1.x §6.9-2 / PLAN.md §6.12). Server-operator-level
    # only (not per-request) — every field unset (default) means unlimited, zero
    # behavior change. See agent_room/budget.py.
    max_tokens: int | None = None
    max_tool_calls: int | None = None
    max_cost_usd: float | None = None
    price_per_1k_input: float | None = None
    price_per_1k_output: float | None = None

    def budget(self) -> Budget | None:
        """The `Budget` ceiling these settings describe, or `None` if every
        dimension is unset (so `BudgetTracker` is a pure NoOp)."""
        if self.max_tokens is None and self.max_tool_calls is None and self.max_cost_usd is None:
            return None
        return Budget(
            max_tokens=self.max_tokens,
            max_tool_calls=self.max_tool_calls,
            max_cost_usd=self.max_cost_usd,
            price_per_1k_input=self.price_per_1k_input,
            price_per_1k_output=self.price_per_1k_output,
        )

    # Content guardrail (v1.x §6.9-3 / PLAN.md §6.13): "off" (default, zero
    # behavior change) / "warn" (record, never blocks) / "block" (raise on a
    # hit at any of the 4 checkpoints). See agent_room/guardrail.py.
    guardrail_mode: GuardrailMode = "off"

    def guardrail(self) -> Guardrail:
        return Guardrail(mode=self.guardrail_mode)

    def embedding_backend(self) -> EmbeddingBackend | None:
        """The vector-recall embedding backend these settings describe, or
        `None` when off.

        `TranscriptStore` only attaches the vector layer when its `embedding`
        constructor arg is not `None` — handing it a truthy
        `NoOpEmbeddingBackend()` instance instead would turn the vector layer
        on with `dim=0`, breaking the zero-behavior-change default every
        other toggle in this module follows.
        """
        if not self.memory_vector:
            return None
        if self.vector_backend == "qdrant":
            from agent_room.memory.fastembed_backend import FastEmbedBackend

            return FastEmbedBackend(model_name=self.embedding_model)
        return HashingEmbeddingBackend()

    def vector_index(self, *, tenant_dir: Path | None = None) -> VectorIndex | None:
        """The pluggable `VectorIndex` these settings describe, or `None`.

        `None` covers both "vector layer off" and "sqlite backend" —
        `TranscriptStore` builds its own sqlite-vec `VectorStore` internally
        whenever `vector_index=None`, so returning `None` for the sqlite
        backend is correct, not an oversight (same discipline as
        `embedding_backend()`: never hand back a constructed-but-inert
        instance for the "off" case).

        `tenant_dir` (v1.x §6.15), when given, scopes the embedded Qdrant
        storage path / collection name to one tenant — each tenant's
        `FileFtsMemoryProvider` gets its own physically separate index.
        """
        if not self.memory_vector or self.vector_backend != "qdrant":
            return None
        from agent_room.memory.qdrant_index import QdrantVectorIndex

        if self.qdrant_url:
            collection = (
                f"agent_room_memory_vectors__{tenant_dir.name}"
                if tenant_dir is not None
                else "agent_room_memory_vectors"
            )
            return QdrantVectorIndex(url=self.qdrant_url, collection=collection)
        path = tenant_dir / "qdrant" if tenant_dir is not None else Path(self.qdrant_path)
        return QdrantVectorIndex(path=str(path))


def _parse_allowlist(raw: str | None) -> tuple[str, ...]:
    """Parse the explicit shell allowlist. Unset/empty means shell disabled."""
    if raw is None:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _parse_mcp_servers(raw: str | None) -> dict[str, dict[str, Any]]:
    """Parse the `AGENT_ROOM_MCP_SERVERS` JSON env var: a map of server name →
    langchain-mcp-adapters connection config. Unset/empty → no MCP servers
    (zero behavior change). Malformed JSON raises — misconfiguration should
    fail loud at startup, not silently disable MCP."""
    if not raw or not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("AGENT_ROOM_MCP_SERVERS must be a JSON object of {name: connection}")
    return cast("dict[str, dict[str, Any]]", parsed)


def _parse_optional_int(raw: str | None) -> int | None:
    return None if raw is None or not raw.strip() else int(raw)


def _parse_optional_float(raw: str | None) -> float | None:
    return None if raw is None or not raw.strip() else float(raw)


def _parse_guardrail_mode(raw: str | None) -> GuardrailMode:
    mode = (raw or "off").strip().lower()
    if mode not in ("off", "warn", "block"):
        raise ValueError(f"AGENT_ROOM_GUARDRAIL must be off/warn/block, got {mode!r}")
    return cast("GuardrailMode", mode)


def _parse_vector_backend(raw: str | None) -> str:
    backend = (raw or "sqlite").strip().lower()
    if backend not in ("sqlite", "qdrant"):
        raise ValueError(f"AGENT_ROOM_VECTOR_BACKEND must be sqlite/qdrant, got {backend!r}")
    return backend


def load_settings() -> Settings:
    """Load settings from env. Reads .env via python-dotenv if available."""

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # dotenv is optional in production
        pass

    role_models: dict[Role, str] = {}
    for role in ("planner", "developer", "reviewer", "delivery", "supervisor"):
        env_key = f"AGENT_ROOM_{role.upper()}_MODEL"
        if value := os.getenv(env_key):
            role_models[role] = value

    default_model = os.getenv("AGENT_ROOM_DEFAULT_MODEL") or os.getenv("ANTHROPIC_MODEL")

    return Settings(
        db_path=os.getenv("AGENT_ROOM_DB", "./agent_room.db"),
        host=os.getenv("AGENT_ROOM_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_ROOM_PORT", "8765")),
        default_model=default_model or _DEFAULT_FALLBACK,
        role_models=role_models,
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        anthropic_auth_token=os.getenv("ANTHROPIC_AUTH_TOKEN") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        graph_preset=os.getenv("AGENT_ROOM_GRAPH", "full_react"),
        workspace_dir=os.getenv("AGENT_ROOM_WORKSPACE", "./workspace"),
        shell_allowlist=_parse_allowlist(os.getenv("AGENT_ROOM_SHELL_ALLOWLIST")),
        allow_direct_shell=os.getenv("AGENT_ROOM_ALLOW_DIRECT_SHELL", "0").lower()
        in ("1", "true", "yes", "on"),
        tool_mode=os.getenv("AGENT_ROOM_TOOL_MODE", "read_only"),
        sandbox_runner_url=os.getenv("AGENT_ROOM_SANDBOX_RUNNER_URL") or None,
        sandbox_runner_token=os.getenv("AGENT_ROOM_SANDBOX_RUNNER_TOKEN") or None,
        memory_enabled=os.getenv("AGENT_ROOM_MEMORY", "1").lower() not in ("0", "false", "no", ""),
        memory_vector=os.getenv("AGENT_ROOM_MEMORY_VECTOR", "0").lower()
        not in ("0", "false", "no", ""),
        vector_backend=_parse_vector_backend(os.getenv("AGENT_ROOM_VECTOR_BACKEND")),
        embedding_model=os.getenv(
            "AGENT_ROOM_EMBEDDING_MODEL", "jinaai/jina-embeddings-v2-base-zh"
        ),
        qdrant_path=os.getenv("AGENT_ROOM_QDRANT_PATH", "./.agent_room_qdrant"),
        qdrant_url=os.getenv("AGENT_ROOM_QDRANT_URL") or None,
        sse_ping_seconds=int(os.getenv("AGENT_ROOM_SSE_PING", "15")),
        mcp_servers=_parse_mcp_servers(os.getenv("AGENT_ROOM_MCP_SERVERS")),
        max_tokens=_parse_optional_int(os.getenv("AGENT_ROOM_MAX_TOKENS")),
        max_tool_calls=_parse_optional_int(os.getenv("AGENT_ROOM_MAX_TOOL_CALLS")),
        max_cost_usd=_parse_optional_float(os.getenv("AGENT_ROOM_MAX_COST_USD")),
        price_per_1k_input=_parse_optional_float(os.getenv("AGENT_ROOM_PRICE_PER_1K_INPUT")),
        price_per_1k_output=_parse_optional_float(os.getenv("AGENT_ROOM_PRICE_PER_1K_OUTPUT")),
        guardrail_mode=_parse_guardrail_mode(os.getenv("AGENT_ROOM_GUARDRAIL")),
    )


@dataclass
class RoleBindings:
    """Explicit per-role LLM bindings.

    If a role is not bound here, `resolve()` falls back to env then global default.
    """

    planner: BaseChatModel | None = None
    developer: BaseChatModel | None = None
    reviewer: BaseChatModel | None = None
    delivery: BaseChatModel | None = None
    supervisor: BaseChatModel | None = None
    """Goal mode's stuck-strategy decider (roles/supervisor.py). Resolves
    through the same fallback chain as the other four when unbound."""
    settings: Settings | None = None
    context_engine: ContextEngine = field(default_factory=NoOpContextEngine)
    """Context-management policy for ReAct working memory.

    Default `NoOpContextEngine` keeps v0.1-v0.3 behaviour bit-for-bit. Swap
    in `WindowedContextEngine(max_messages=..., protect_last_n=...)` (or a
    future SummaryEngine) to bound the LLM-input list when the developer
    ReAct loop runs long.

    Note: the engine compresses the *LLM-input* messages each turn; the
    persisted `state["dev_messages"]` keeps growing under its `add` reducer.
    See [`agent_room/roles/developer_react.py`](roles/developer_react.py)
    for the integration point.
    """

    memory: MemoryProvider = field(default_factory=NoOpMemoryProvider)
    """Cross-session memory backend (v0.5).

    Default `NoOpMemoryProvider` keeps v0.1-v0.4 behaviour unchanged. Swap
    in `FileFtsMemoryProvider(...)` to enable curated MEMORY.md / USER.md
    facts plus a SQLite+FTS5 transcript log; the developer ReAct node will
    then auto-prefetch relevant prior content into the system prompt and
    sync each turn back to the log.
    """

    budget: Budget | None = None
    """Per-task token/cost/tool-call ceiling (v1.x §6.9-2).

    Default `None` keeps behaviour unchanged (no enforcement). `graph.py`
    builds one `BudgetTracker(budget)` per compiled graph — i.e. per task run
    in the server's per-task-workspace architecture — and shares it across
    every role's transport so usage accumulates across the whole task, not
    per-node. See `agent_room/budget.py`.
    """

    guardrail: Guardrail = field(default_factory=Guardrail)
    """Content guardrail — 4 checkpoints: input / tool_call / tool_response /
    output (v1.x §6.9-3).

    Default `Guardrail(mode="off")` keeps behaviour unchanged (no scanning).
    Every role factory reads `bindings.guardrail` directly — unlike `budget`,
    it's stateless per check so there's no shared-instance/tracker to wire
    through the transport. See `agent_room/guardrail.py`.
    """

    def resolve(self, role: Role, *, model_override: str | None = None) -> BaseChatModel:
        """Resolve the LLM for a role.

        `model_override` (from a `NodeSpec.model` in v0.2) wins over an
        explicit binding *only* when the explicit binding is absent — an
        explicit `RoleBindings.reviewer=FakeReviewerLLM(...)` always takes
        precedence so existing tests stay deterministic.
        """

        explicit = getattr(self, role)
        if explicit is not None:
            return cast("BaseChatModel", explicit)
        settings = self.settings or load_settings()
        model_id = model_override or settings.role_models.get(role) or settings.default_model
        return _build_chat_model(model_id, settings=settings)

    def model_ids(self) -> dict[Role, str]:
        """Return the model id that `resolve(role)` would pick, per role.

        Used by the v1.0 `/healthz` endpoint (ADR-0011) to surface the
        currently-bound role→model mapping without leaking any provider keys.
        Roles bound to an explicit chat-model instance report the instance's
        `model` attribute (best effort) or `<bound>` if it cannot be derived.
        """

        settings = self.settings or load_settings()
        out: dict[Role, str] = {}
        for role in ("planner", "developer", "reviewer", "delivery", "supervisor"):
            explicit = getattr(self, role)
            if explicit is not None:
                out[role] = _describe_bound_model(explicit)
                continue
            out[role] = settings.role_models.get(role) or settings.default_model
        return out


def _describe_bound_model(model: BaseChatModel) -> str:
    for attr in ("model", "model_name", "model_id"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return "<bound>"


def _is_anthropic_shaped(model_id: str) -> bool:
    """True for native Claude IDs *and* anthropic-compatible gateway models.

    The gateway path is signalled by `ANTHROPIC_BASE_URL` being set; in that
    case any model id (including non-Claude names like `DeepSeek-V4-Pro`) is
    routed through `langchain-anthropic` because the gateway speaks the
    Anthropic Messages API.
    """

    lower = model_id.lower()
    if lower.startswith("claude") or lower.startswith("anthropic:"):
        return True
    return bool(os.getenv("ANTHROPIC_BASE_URL"))


def _build_chat_model(
    model_id: str, *, settings: Settings | None = None, **kwargs: Any
) -> BaseChatModel:
    """Best-effort factory: routes model id → provider class with proper credentials."""

    settings = settings or load_settings()
    lower = model_id.lower()

    if _is_anthropic_shaped(model_id):
        from langchain_anthropic import ChatAnthropic

        api_key = settings.anthropic_auth_token or settings.anthropic_api_key
        ctor: dict[str, Any] = {"model": model_id.removeprefix("anthropic:")}
        if api_key:
            ctor["anthropic_api_key"] = api_key
        if settings.anthropic_base_url:
            ctor["anthropic_api_url"] = settings.anthropic_base_url
        ctor.update(kwargs)
        return ChatAnthropic(**ctor)

    if lower.startswith("gpt") or lower.startswith("openai:") or lower.startswith("o"):
        from langchain_openai import ChatOpenAI

        ctor = {"model": model_id.removeprefix("openai:")}
        if settings.openai_api_key:
            ctor["api_key"] = settings.openai_api_key
        ctor.update(kwargs)
        return cast("BaseChatModel", ChatOpenAI(**ctor))

    # default: try anthropic
    from langchain_anthropic import ChatAnthropic

    return cast("BaseChatModel", ChatAnthropic(model=model_id, **kwargs))
