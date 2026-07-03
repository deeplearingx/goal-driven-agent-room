"""LLM transport: a thin seam over `RoleBindings + LangChain` (ADR-0013).

Why this exists: reviewer protocol, parser-error retry, and (incoming)
prompt-cache control are all cross-provider concerns. Without a seam they
duplicate across role files. The transport is one place to put them.

Borrowed shape from hermes-agent's `agent/transports/base.py` (89 lines)
and `types.py` (174 lines), reimplemented here per ADR-0007 with a much
smaller surface — three methods, two dataclasses, no per-provider
implementations. LangChain stays the runtime; the seam stays empty until
something actually crosses it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser

from agent_room.llm._retry import retry_on_parser_error
from agent_room.obs.tracing import (
    GEN_AI_OPERATION,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_USAGE_INPUT,
    GEN_AI_USAGE_OUTPUT,
    ROLE,
    get_tracer,
)

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel

    from agent_room.budget import BudgetTracker
    from agent_room.config import RoleBindings
    from agent_room.schemas import Role


_S = TypeVar("_S", bound="BaseModel")


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class NormalizedResponse:
    """The shared shape every consumer reads.

    `message` is the raw LangChain `AIMessage` so existing nodes that
    already destructure `response.tool_calls` / `response.content` keep
    working without translation. `usage` and `provider_data` are the
    cross-provider hooks; today both are best-effort.
    """

    message: AIMessage
    usage: Usage | None = None
    provider_data: dict[str, Any] | None = field(default=None, repr=False)


class Transport(Protocol):
    """The seam. Three methods cover every current call site.

    `invoke` and `structured` are the only methods role nodes need; the
    streaming path stays on `astream_events` at the graph level (ADR-0001),
    so the transport doesn't expose a stream method in v1.0 — adding one
    is a strictly additive change when a real consumer needs it.
    """

    async def invoke(
        self,
        role: Role,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[BaseTool] | None = None,
        model_override: str | None = None,
    ) -> NormalizedResponse: ...

    async def structured(
        self,
        role: Role,
        schema: type[_S],
        messages: Sequence[BaseMessage],
        *,
        attempts: int = 2,
        model_override: str | None = None,
    ) -> _S: ...


class LangChainTransport:
    """Default implementation: `RoleBindings.resolve` + LangChain calls.

    Equivalent to the v0.5 inline pattern, with one difference: `structured`
    automatically wraps `with_structured_output` in `retry_on_parser_error`,
    so the three role files that did this by hand can drop the import.
    """

    def __init__(self, bindings: RoleBindings, *, budget: BudgetTracker | None = None) -> None:
        self._bindings = bindings
        # Shared across every role that uses this transport instance — see
        # graph.py::build_uncompiled_from_spec, which builds one transport (and
        # therefore one tracker) per compiled graph so usage accumulates across
        # the whole task run, not per-node. `None` (default) = no enforcement,
        # zero overhead, zero behavior change (agent_room/budget.py).
        self._budget = budget
        # (role, model_override) keys whose model rejected structured output
        # once. Thinking models 400 on every structured call, so after the first
        # failure we skip the doomed primary and go straight to the JSON-prompt
        # fallback. Per-instance — a node's transport persists across its rounds,
        # and resets when the graph is rebuilt.
        self._needs_json_fallback: set[tuple[Role, str | None]] = set()

    async def invoke(
        self,
        role: Role,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[BaseTool] | None = None,
        model_override: str | None = None,
    ) -> NormalizedResponse:
        llm: BaseChatModel = self._bindings.resolve(role, model_override=model_override)
        runnable: Any = llm.bind_tools(list(tools)) if tools else llm
        with get_tracer().start_as_current_span("llm.invoke") as span:
            span.set_attribute(ROLE, role)
            span.set_attribute(GEN_AI_OPERATION, "chat")
            _set_model_attr(span, llm, model_override)
            msg = await runnable.ainvoke(list(messages))
            usage = _extract_usage(msg)
            _set_usage_attrs(span, usage)
            if self._budget is not None:
                self._budget.record(usage)
                if msg.tool_calls:
                    self._budget.record_tool_calls(len(msg.tool_calls))
            return NormalizedResponse(message=msg, usage=usage)

    async def structured(
        self,
        role: Role,
        schema: type[_S],
        messages: Sequence[BaseMessage],
        *,
        attempts: int = 2,
        model_override: str | None = None,
    ) -> _S:
        # NOTE (budget scope, see agent_room/budget.py): `with_structured_output`
        # doesn't surface `usage_metadata` on the non-`include_raw` path this
        # uses, so structured() calls (reviewer / planner_gate / reviewer_two_call)
        # don't contribute tokens to a configured budget. `invoke()` — used by
        # planner/developer/delivery, including the developer ReAct tool loop
        # (the highest-risk runaway-cost path) — is where enforcement lives.
        llm: BaseChatModel = self._bindings.resolve(role, model_override=model_override)
        with get_tracer().start_as_current_span("llm.structured") as span:
            span.set_attribute(ROLE, role)
            span.set_attribute(GEN_AI_OPERATION, "structured_output")
            _set_model_attr(span, llm, model_override)
            key = (role, model_override)
            if key not in self._needs_json_fallback:
                try:
                    runnable = retry_on_parser_error(
                        llm.with_structured_output(schema), attempts=attempts
                    )
                    result = await runnable.ainvoke(list(messages))
                except Exception as exc:  # noqa: BLE001 — provider may not support structured output
                    if not _structured_output_unsupported(exc):
                        raise
                    result = None
                if result is not None:
                    return cast("_S", result)
                # Thinking-mode models reject the forced tool_choice that
                # `with_structured_output` uses; some providers ignore the
                # json_schema method and return prose; and with no tool call it
                # can yield `None` silently (crashing callers that do
                # `.model_dump()`). Remember this model needs the fallback.
                span.set_attribute("agent_room.structured_fallback", True)
                self._needs_json_fallback.add(key)
            # JSON-format prompt + lenient parse: no tool_choice, never `None`.
            return await self._structured_via_json_prompt(llm, schema, messages, attempts=attempts)

    async def _structured_via_json_prompt(
        self,
        llm: BaseChatModel,
        schema: type[_S],
        messages: Sequence[BaseMessage],
        *,
        attempts: int,
    ) -> _S:
        parser: PydanticOutputParser[_S] = PydanticOutputParser(pydantic_object=schema)
        prompt = [*messages, HumanMessage(parser.get_format_instructions())]
        last_exc: OutputParserException | None = None
        for _ in range(max(attempts, 1)):
            response = await llm.ainvoke(prompt)
            try:
                return parser.parse(_message_text(response))
            except OutputParserException as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc


def _set_model_attr(span: Any, llm: BaseChatModel, model_override: str | None) -> None:
    model = model_override or getattr(llm, "model", None) or getattr(llm, "model_name", None)
    if model:
        span.set_attribute(GEN_AI_REQUEST_MODEL, str(model))


def _set_usage_attrs(span: Any, usage: Usage | None) -> None:
    if usage is not None:
        span.set_attribute(GEN_AI_USAGE_INPUT, usage.prompt_tokens)
        span.set_attribute(GEN_AI_USAGE_OUTPUT, usage.completion_tokens)


def _structured_output_unsupported(exc: Exception) -> bool:
    """True when a structured-output failure means the provider can't do it,
    so the JSON-prompt fallback should run. Genuine errors (auth, network)
    return False and propagate."""

    if isinstance(exc, OutputParserException):
        return True
    msg = str(exc).lower()
    return "tool_choice" in msg or "thinking mode" in msg or "structured output" in msg


def _message_text(message: AIMessage) -> str:
    """Plain text from a response, dropping `thinking` blocks (reasoning models
    return a `[{type:thinking...}, {type:text...}]` content list)."""

    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def _extract_usage(message: AIMessage) -> Usage | None:
    metadata = getattr(message, "usage_metadata", None) or {}
    if not metadata:
        return None
    return Usage(
        prompt_tokens=int(metadata.get("input_tokens", 0)),
        completion_tokens=int(metadata.get("output_tokens", 0)),
        total_tokens=int(metadata.get("total_tokens", 0)),
        cached_tokens=int(metadata.get("input_token_details", {}).get("cache_read", 0)),
    )
