"""GraphSpec — Pydantic schema + YAML loader for v0.2 DAG configuration.

See [docs/adr/0008-graph-spec.md](../docs/adr/0008-graph-spec.md).

The schema is structurally validated at load time so a bad spec fails
before any LLM is bound: every edge target must reference a known node or
`__end__`, every conditional edge must declare its branches, and the entry
point must exist. The router for a conditional edge can be the built-in
`agent_room.routers.review_router` (default) or a user-supplied callable
referenced by dotted path (`pkg.module:fn`).

`load_graph_spec` accepts a path, a YAML/JSON string, or an already-parsed
dict — the same loader is used by the CLI's `--graph` flag and by tests.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_room.routers import review_router
from agent_room.state import TaskState
from agent_room.tools.policy import ALL_MODES as PERMISSION_MODES
from agent_room.tools.policy import PermissionMode

RoleName = Literal["planner", "developer", "reviewer", "delivery", "verifier", "supervisor"]


class NodeSpec(BaseModel):
    """One node in the graph.

    `role` selects which factory (`make_planner` / `make_developer` / ...)
    instantiates the node. The other fields override or extend that role's
    defaults without touching code.
    """

    model_config = ConfigDict(extra="forbid")

    role: RoleName
    variant: str | None = None
    """Optional alternative implementation for the same `role`.

    Variant strings are not Literal-locked because they're experimental
    (e.g. `gate` for the planner, `two_call` for the reviewer — both born
    out of the F2 escalation lab). Unknown `(role, variant)` pairs raise
    at build time, not at validate time, so adding a variant doesn't
    require a schema change.
    """

    model: str | None = None
    prompt_override: str | None = None
    extra_context_keys: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    tool_mode: PermissionMode | None = None
    """Permission mode for the ReAct subgraph. Ignored when `tools` is empty.

    `None` → policy default (currently `read_only`). See
    `agent_room.tools.policy` for the semantics of each mode. Setting this
    when `tools=[]` raises at validate time so a typo can't silently no-op.
    """
    max_dev_rounds: int | None = None
    """Developer-only ReAct loop budget. Ignored when `tools` is empty.

    `None` → use the role default (currently 6). Setting this on a non-
    developer role raises at validate time so a typo can't silently no-op.
    """

    @model_validator(mode="after")
    def _max_dev_rounds_only_for_developer(self) -> NodeSpec:
        if self.max_dev_rounds is not None and self.role != "developer":
            raise ValueError(
                f"max_dev_rounds is only valid on role='developer', got role={self.role!r}"
            )
        if self.max_dev_rounds is not None and self.max_dev_rounds < 1:
            raise ValueError(f"max_dev_rounds must be >= 1, got {self.max_dev_rounds}")
        return self

    @model_validator(mode="after")
    def _tool_mode_requires_tools(self) -> NodeSpec:
        if self.tool_mode is not None and not self.tools:
            raise ValueError(
                f"tool_mode={self.tool_mode!r} set but tools=[] — either list "
                f"tools or remove tool_mode (allowed modes: {list(PERMISSION_MODES)})"
            )
        return self


class BranchSpec(BaseModel):
    """Maps a router signal (e.g. `approved`, `developer`) to a destination."""

    model_config = ConfigDict(extra="forbid")

    on: str
    to: str


class EdgeSpec(BaseModel):
    """Either an unconditional edge (`to`) or a conditional one (`branches`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str | None = None
    branches: list[BranchSpec] | None = None
    router: str | None = None

    @model_validator(mode="after")
    def _exactly_one_path(self) -> EdgeSpec:
        has_to = self.to is not None
        has_branches = bool(self.branches)
        if has_to == has_branches:
            raise ValueError(f"EdgeSpec({self.from_!r}) needs exactly one of `to` or `branches`")
        if self.router and not self.branches:
            raise ValueError(f"EdgeSpec({self.from_!r}) sets `router` but has no `branches`")
        return self


class GraphSpec(BaseModel):
    """Top-level spec. Cross-field references are validated together."""

    model_config = ConfigDict(extra="forbid")

    name: str
    entry: str
    nodes: dict[str, NodeSpec]
    edges: list[EdgeSpec]
    state_class: str | None = None

    @model_validator(mode="after")
    def _references_resolve(self) -> GraphSpec:
        node_names = set(self.nodes)
        valid_targets = node_names | {"__end__"}

        if self.entry not in node_names:
            raise ValueError(f"entry={self.entry!r} is not in nodes (have: {sorted(node_names)})")

        for edge in self.edges:
            if edge.from_ not in node_names:
                raise ValueError(
                    f"edge.from={edge.from_!r} is not a node (have: {sorted(node_names)})"
                )
            targets = [edge.to] if edge.to is not None else [b.to for b in (edge.branches or [])]
            for tgt in targets:
                if tgt not in valid_targets:
                    raise ValueError(
                        f"edge target {tgt!r} from {edge.from_!r} is not a node and not '__end__'"
                    )

        if self.state_class is not None:
            raise ValueError("state_class is reserved for v0.3 — leave unset for v0.2")

        return self


def load_graph_spec(source: str | Path | dict[str, Any]) -> GraphSpec:
    """Load a `GraphSpec` from a path, YAML/JSON string, or dict.

    Always uses `yaml.safe_load`; `yaml.load` is forbidden — a hostile spec
    must not be able to instantiate arbitrary Python classes.
    """

    if isinstance(source, dict):
        data = source
    elif isinstance(source, Path):
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    elif isinstance(source, str):
        as_path = Path(source)
        if as_path.suffix.lower() in {".yaml", ".yml"} and as_path.is_file():
            data = yaml.safe_load(as_path.read_text(encoding="utf-8"))
        else:
            data = yaml.safe_load(source)
    else:
        raise TypeError(f"unsupported spec source: {type(source).__name__}")

    if not isinstance(data, dict):
        raise ValueError(f"spec must deserialize to a mapping, got {type(data).__name__}")

    return GraphSpec.model_validate(data)


PRESET_DIR = Path(__file__).resolve().parent / "presets"


def load_preset(name: str) -> GraphSpec:
    """Load a built-in preset by name (e.g. `full`, `dev_review`, `solo`)."""

    path = PRESET_DIR / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in PRESET_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"unknown preset {name!r}; available: {available}")
    return load_graph_spec(path)


def resolve_spec_source(source: str | Path) -> GraphSpec:
    """CLI-friendly resolver: preset name OR file path.

    A bare identifier (no path separators, no `.yaml` suffix) is treated as
    a preset name; anything else is a path. This matches the `--graph` flag
    semantics in `agent_room.cli`.
    """

    s = str(source)
    if "/" not in s and "\\" not in s and not s.endswith((".yaml", ".yml")):
        return load_preset(s)
    return load_graph_spec(Path(s))


RouterFn = Callable[[TaskState], str]


def resolve_router(dotted: str | None) -> RouterFn:
    """Resolve a router string to a callable.

    `None` returns the built-in `review_router`. Otherwise the format is
    `pkg.module:attr` (colon-separated). Resolution failure is fatal —
    no silent fallback to the default — so a typo in a spec cannot cause
    the run to silently use a different router than the user intended.
    """

    if dotted is None:
        return review_router
    if ":" not in dotted:
        raise ValueError(f"router {dotted!r} must be a dotted path of the form 'pkg.module:fn'")
    module_path, attr = dotted.split(":", 1)
    module = importlib.import_module(module_path)
    fn = getattr(module, attr, None)
    if fn is None:
        raise AttributeError(f"router {dotted!r}: {attr!r} not found in {module_path!r}")
    if not callable(fn):
        raise TypeError(f"router {dotted!r} resolved to non-callable {type(fn).__name__}")
    return cast("RouterFn", fn)
