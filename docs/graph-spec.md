# GraphSpec — v0.2 DAG configuration

This document is the user-facing reference for `GraphSpec` — the YAML
schema that describes which roles run, in what order, and how routing
decisions map to next nodes.

> Design rationale: see [ADR-0008](adr/0008-graph-spec.md). The schema
> module is [`agent_room/spec.py`](../agent_room/spec.py); built-in
> presets ship in [`agent_room/presets/`](../agent_room/presets/).

## Quick start

The CLI accepts either a built-in preset name or a path to a YAML file:

```bash
agent-room run "title" -d "description"                  # default: full
agent-room run "quick prototype" -d "..." --graph solo
agent-room run "review only" -d "..." --graph dev_review
agent-room run "..." --graph ./my_pipeline.yaml          # custom file
```

In Python:

```python
from agent_room.config import RoleBindings
from agent_room.graph import build_from_spec
from agent_room.spec import load_preset, load_graph_spec

spec = load_preset("solo")            # built-in
# spec = load_graph_spec("path.yaml") # custom

graph = build_from_spec(spec, RoleBindings())
```

## Schema

```yaml
name: <string>                         # spec identifier (informational)
entry: <node-name>                     # where the run starts
nodes:                                 # map<name, NodeSpec>
  <name>:
    role: planner | developer | reviewer | delivery
    model: <string>            # optional — overrides settings.default_model
    prompt_override: <string>  # optional — replaces the role's SYSTEM prompt
    extra_context_keys: [...]  # optional — state keys appended to HUMAN message
    tools: [...]               # v0.3+ — tool names resolved against a Registry
    tool_mode: read_only | unrestricted | approval  # v0.3+ — defaults to read_only
    max_dev_rounds: <int>      # v0.3+ — developer ReAct loop budget (default 6)
edges:                                 # list<EdgeSpec>
  - from: <node-name>
    to: <node-name | __end__>          # unconditional edge (set EITHER `to`...
  - from: <node-name>
    branches:                          # ...OR `branches`, never both)
      - { "on": <signal>, to: <target> }
    router: <pkg.module:fn>            # optional — defaults to review_router
```

### NodeSpec fields

| Field | Type | Purpose |
|---|---|---|
| `role` | `Literal[planner / developer / reviewer / delivery]` | Picks the node's implementation. v0.2 locks these four. |
| `variant` | `str?` | Selects an alternative implementation for the same role. Built-in: `(planner, gate)`, `(reviewer, two_call)`. Unknown `(role, variant)` raises at build time, not at validate time. |
| `model` | `str?` | Overrides global / role-specific model for this node only. |
| `prompt_override` | `str?` | Replaces the role's built-in SYSTEM prompt verbatim. |
| `extra_context_keys` | `list[str]` | State keys whose values are serialized into a `# <Key>` block of the HUMAN message. List values render as `- item` bullets; empty/missing keys are skipped. |
| `tools` | `list[str]` | Tool names resolved against a `Registry` at build time (v0.3+). Activates a ReAct subgraph on `role="developer"`. See [tools.md](tools.md). |
| `tool_mode` | `Literal["read_only", "unrestricted", "approval"]?` | Permission policy for the resolved tool list. Defaults to `read_only` when `tools` is non-empty. Setting this with `tools=[]` is a validation error. |
| `max_dev_rounds` | `int?` | Developer ReAct loop ceiling. Once reached the agent stops binding tools so the LLM has to emit final code. Default 6. Only valid on `role="developer"`. |

### EdgeSpec fields

An edge is **either** `to` (unconditional) **or** `branches` (conditional);
never both. The validator rejects edges that violate this.

`router` is a dotted path of the form `pkg.module:fn` resolving to a
callable `(state) -> str`. The string returned is looked up in the
`branches` mapping. Default router: `agent_room.routers.review_router`,
which inspects `state.review.decision` and the round counter.

### YAML 1.1 footgun

PyYAML treats bare `on` as a boolean. **Always quote** the `on` key in
`branches`:

```yaml
branches:
  - { "on": developer, to: developer }    # correct
  - { on: developer, to: developer }      # parses as `True: developer`!
```

The presets in this repo do this consistently.

## Built-in presets

| Preset | Topology | When to use |
|---|---|---|
| `full` | `planner → developer → reviewer → (loop / delivery / halt)` | Default; equivalent to v0.1 hard-coded pipeline. |
| `dev_review` | `developer ↔ reviewer`, no planner / delivery | Fast iteration on small code-edit tasks. Approval terminates directly. |
| `solo` | `developer → END` | One-shot direct answer. No review loop. |
| `planner_gate` | `full` with `planner.variant=gate` + `planner_gate_router` | Surfaces ambiguity before code is written; halts at planner with `open_questions`. F2 escalation experiment, option A. |
| `two_call_review` | `full` with `reviewer.variant=two_call` | Cheap focus-check first, full review only if fully specified. F2 escalation experiment, option B. |

## Role variants

`NodeSpec.variant` selects an alternative implementation for the same
role without changing the locked `RoleName` literal. Built-ins:

| `(role, variant)` | Implementation | Notes |
|---|---|---|
| `(planner, gate)` | [`agent_room/roles/planner_gate.py`](../agent_room/roles/planner_gate.py) | Structured output `{plan, open_questions}`. Pair with `planner_gate_router` so `open_questions != []` parks the run at `awaiting_user`. Resume with `service.resume(task_id, ans, at_node="planner")`. |
| `(reviewer, two_call)` | [`agent_room/roles/reviewer_two_call.py`](../agent_room/roles/reviewer_two_call.py) | Two structured calls: `FocusCheck` then `ReviewerDecision`. Under-specified tasks skip the second call and emit `need_user_decision` directly. |

Adding a new variant: drop a `make_<role>_<variant>(bindings, *, prompt_override, extra_context_keys, model)` factory in `agent_room/roles/`, register it in `_VARIANT_FACTORIES` in [`agent_room/graph.py`](../agent_room/graph.py). No schema change needed.

## Custom routers

Default routing comes from `agent_room.routers.review_router`. To plug
in a different policy, point `router` at a callable:

```yaml
edges:
  - from: reviewer
    router: my_pkg.routing:strict_router
    branches:
      - { "on": developer, to: developer }
      - { "on": halt, to: __end__ }
```

```python
# my_pkg/routing.py
from agent_room.state import TaskState

def strict_router(state: TaskState) -> str:
    review = state.get("review")
    if review is None or review.decision != "approved":
        return "halt"
    if review.confidence < 0.9:
        return "developer"
    return "halt"
```

Resolution failure is fatal — there is no silent fallback to the default
router, so a typo in a spec halts the build instead of running with the
wrong policy.

## Validation guarantees

Loaded specs are validated *before* any LLM is bound:

- `entry` must reference a real node.
- Every edge `from` must reference a real node.
- Every edge target must be a real node or the literal `__end__`.
- An edge declares **exactly one** of `to` or `branches`.
- `router` requires `branches`.
- Unknown fields are rejected (`extra="forbid"`).

## Forward compatibility

`GraphSpec.state_class` is a reserved slot:

- `state_class` must be unset in v0.2 / v0.3; a later milestone introduces
  generic state and this field will name the dotted path of the user's
  TypedDict.

Setting `state_class` today raises a validation error so a forward-compat
spec can't silently downgrade.
