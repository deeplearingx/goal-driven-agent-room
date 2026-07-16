# Tools — v0.3 tool system

This document is the developer-facing reference for `agent_room.tools` —
the registry, the four built-in tools, the permission policy, and how a
node opts into a ReAct loop that can actually call them.

> Design rationale:
> - Independence: [ADR-0007](adr/0007-borrow-not-integrate-hermes.md) — the
>   registry pattern is borrowed from hermes-agent, but **everything is
>   reimplemented in this repo**. There are zero `from hermes` imports.
> - Permission default: shell / write are deny-by-default
>   ([PLAN.md] R3). Listing them in a spec without `tool_mode: unrestricted`
>   raises at build time.

## Quick start

A developer node opts into tool calling by listing tool names:

```yaml
# my_pipeline.yaml
name: dev-with-tools
entry: developer
nodes:
  developer:
    role: developer
    tools: ["glob", "read_text"]      # spec lists tool names
    tool_mode: read_only              # default; can omit
    max_dev_rounds: 6                 # default; can omit
  reviewer:
    role: reviewer
edges:
  - { from: developer, to: reviewer }
  - from: reviewer
    branches:
      - { "on": developer, to: developer }
      - { "on": delivery, to: __end__ }
      - { "on": halt, to: __end__ }
```

Wire a registry that backs those names:

```python
from agent_room.config import RoleBindings
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.spec import load_graph_spec
from agent_room.tools import Registry, register_builtin_tools

registry = Registry()
register_builtin_tools(registry, fs_root="/path/to/project")  # no shell

spec = load_graph_spec("my_pipeline.yaml")
async with build_with_sqlite_checkpointer(
    RoleBindings(),
    db_path="./agent_room.db",
    spec=spec,
    registry=registry,        # passed straight through to build_from_spec
) as graph:
    ...
```

The developer node now runs as a ReAct subgraph: each turn the LLM either
emits final code or asks for a tool call; tool output is appended to
`state.dev_messages` and the LLM is invoked again. Convergence is forced
once `dev_round` hits `max_dev_rounds` (the agent stops binding tools so
the LLM physically cannot emit another tool call).

A complete, working example — including a real LLM fixing a deliberately
buggy `calc.py` by reading it, writing the fix, and running `pytest` to
verify — lives in [`examples/dev_runs_tests.py`](../examples/dev_runs_tests.py).

## Built-in tools

Four LangChain `BaseTool` subclasses ship in
[`agent_room/tools/`](../agent_room/tools/). All file paths go through
`resolve_within_root`, which canonicalizes the input and refuses anything
that escapes `root` (including via `..` or symlinks).

| Tool | What it does | Safety | Source |
|---|---|---|---|
| `read_text` | Read a UTF-8 file under `root`. Output truncated to ~8 KB. | Path containment. | [fs.py](../agent_room/tools/fs.py) |
| `write_text` | Write a UTF-8 file under `root`. Optional `create_dirs`. | Path containment. **Write tool — denied under `read_only` mode.** | [fs.py](../agent_room/tools/fs.py) |
| `glob` | List paths matching a pattern under `root`. Up to 200 results. | Path containment; rejects absolute patterns. | [fs.py](../agent_room/tools/fs.py) |
| `shell` | Run one allowlisted command via `subprocess.run([...])`. 30s timeout, 8 KB output cap. | `shell=False`, allowlist on head token, rejects shell metacharacters (`;`, `&&`, `\|`, `>`, etc). **Denied under `read_only` mode.** | [shell.py](../agent_room/tools/shell.py) |

Registering the four built-ins:

```python
from agent_room.tools import Registry, register_builtin_tools

reg = Registry()
register_builtin_tools(
    reg,
    fs_root="/path/to/project",
    shell_allowlist=["pytest", "ruff"],   # None ⇒ shell tool is NOT registered
    shell_timeout_s=30.0,
)
```

`shell_allowlist=None` (the default) means the `shell` entry is not
registered at all. A spec listing `tools: ["shell"]` against such a
registry fails fast at build time with `KeyError: unknown tool: 'shell'`.

## Permission policy

`agent_room.tools.policy` defines three modes:

| Mode | Behavior | When to use |
|---|---|---|
| `read_only` (**default**) | Filters the resolved tool list down to read-only tools (`read_text`, `glob`, plus any tool whose `metadata={"read_only": True}`). | Default for any node listing tools. Safe for code-review-style tasks. |
| `unrestricted` | Passes all listed tools through unchanged. | Needed when the developer must `write_text` or run `shell`. Set explicitly. |
| `approval` | All listed tools pass through, but each tool call halts the graph via `langgraph.types.interrupt()` so a human can approve / deny it call-by-call. Resume with `service.resume(task_id, decision, at_node="tool_call")`. | Production-leaning: the agent is allowed to *propose* writes / shell, but a human authorizes each one. |

The default is `read_only` deliberately ([PLAN.md] R3, RISK-5). Code that
needs to write files or run commands has to opt in by saying so:

```yaml
nodes:
  developer:
    role: developer
    tools: ["read_text", "write_text", "shell"]
    tool_mode: unrestricted
```

### Empty filter is a hard error

If your spec lists only write tools and your `tool_mode` is `read_only`,
the policy filters everything out. Rather than silently downgrading the
node to a no-tool path (which would mask the misconfiguration), this
raises:

```text
ValueError: tool_mode='read_only' filtered out every listed tool: ['write_text', 'shell'].
Either pick tools the mode allows, or set tool_mode='unrestricted' if
you really want write/exec access.
```

### Approval mode walk-through

Under `tool_mode: approval` every tool call halts the graph via
`langgraph.types.interrupt()` and surfaces a structured payload:

```python
spec = GraphSpec.model_validate({
    "name": "review-with-approval",
    "entry": "developer",
    "nodes": {
        "developer": {
            "role": "developer",
            "tools": ["read_text", "write_text", "shell"],
            "tool_mode": "approval",     # ← per-call human gate
        },
        "reviewer": {"role": "reviewer"},
        "delivery": {"role": "delivery"},
    },
    "edges": [...],
})

result = await service.run(req, task_id=task_id)
# result.status == "awaiting_user"
# result.pending_tool_calls == [
#     {"action": "approve_tool_call",
#      "name": "shell",
#      "args": {"command": "pytest -q"},
#      "id": "tc-abc123"}
# ]
```

The client decides per call:

```python
final = await service.resume(task_id, "approve", at_node="tool_call")
# or:
final = await service.resume(task_id, "deny", at_node="tool_call")
# or with a custom rejection message the LLM will see:
final = await service.resume(
    task_id,
    {"action": "deny", "message": "policy says no shell on prod paths"},
    at_node="tool_call",
)
```

On `approve` the tool runs through the normal ToolNode path (argument
validation, `_run` / `_arun`, output truncation). On `deny` the wrapper
synthesizes a `ToolMessage(status="error")` with the rejection message
and the LLM sees it on its next turn — the agent can then pick a
different approach or finalize without the tool call.

Decision parsing is conservative — the only strings interpreted as
approve are `approve` / `approved` / `yes` / `y` / `ok` (case-insensitive).
Anything malformed defaults to **deny** so a misformatted resume
payload can't accidentally green-light a `shell` call.

When an `AIMessage` carries multiple parallel tool calls, the same
verdict applies to every call in that batch (per-call decision lists are
deferred — see "What's not (yet) here").

### Custom tool: declare your safety level

`is_read_only()` first checks `tool.metadata["read_only"]` (author-declared,
wins both ways), then a built-in name table, then defaults to `False`
(conservative). To classify a custom tool:

```python
from langchain_core.tools import BaseTool

class HttpGetTool(BaseTool):
    name: str = "http_get"
    description: str = "Fetch a URL and return the response body."
    metadata: dict = {"read_only": True}   # declares: safe under read_only

    def _run(self, url: str) -> str: ...
```

A tool with side effects (POST, file write, mutation) should leave
metadata unset so it remains denied under `read_only`.

## Spec validation

`NodeSpec` enforces these constraints at validate time, before any LLM
or tool is touched:

- `tool_mode` set without `tools` → `ValueError`. Prevents typos that
  would silently no-op.
- `tool_mode` set to anything other than `read_only` / `approval` /
  `unrestricted` → Pydantic literal-validation error.
- `max_dev_rounds` on a non-developer role → `ValueError`. Tools are
  only wired for `role="developer"` in v0.3 (other roles are scheduled
  for later milestones).
- `max_dev_rounds < 1` → `ValueError`.

Listing a tool name that isn't in the registry fails at build time:

```python
KeyError: unknown tool: 'curl' (registered: ['glob', 'read_text', 'shell', 'write_text'])
```

Resolution is the single source of truth (`_resolve_tools_with_policy`
in [graph.py](../agent_room/graph.py)) — the tool list bound to the LLM
and the tool list inside the sibling `ToolNode` always match. Drift here
would mean the LLM has a tool bound that the executor refuses (or vice
versa).

## Authoring a custom tool

The 4 built-ins are conventional LangChain `BaseTool` subclasses with a
typed `args_schema`. Pattern:

```python
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class GitDiffInput(BaseModel):
    ref: str = Field(default="HEAD~1", description="Git ref to diff against")


class GitDiffTool(BaseTool):
    name: str = "git_diff"
    description: str = "Show changes since `ref`. Read-only."
    args_schema: ClassVar[type[BaseModel]] = GitDiffInput
    metadata: dict = {"read_only": True}

    cwd: str | None = None

    def _run(self, ref: str = "HEAD~1") -> str:
        # ... call subprocess, return stdout (truncate!) ...
        ...
```

Then register and reference by name:

```python
reg.register(ToolEntry(name="git_diff", toolset="vcs", tool=GitDiffTool(cwd=repo)))
```

```yaml
nodes:
  developer:
    role: developer
    tools: ["read_text", "git_diff"]
    # tool_mode omitted → read_only → both tools allowed (both flagged read-only)
```

### Output sizing

Every tool runs inside the LLM's context window. Aim for ≤ 8 KB per call,
matching the built-ins' `_truncate` helpers. Truncation should be marked
inline (`\n[... truncated, N more bytes]`) so the LLM knows it isn't
seeing everything. A principled spill design that keeps full bodies
out of state — pointing at SQLite-backed blobs instead — is deferred
to v0.4 ([PLAN.md] RISK-9).

### `_run` vs `_arun`

`BaseTool._arun` falls back to `_run` via the LangChain default. Override
`_arun` only when there's a real async backend (an aiohttp client, an
async DB driver). Don't wrap sync code in `asyncio.to_thread` here —
LangGraph's executor already handles that.

## Loop budget

The developer ReAct loop is governed by `dev_round` and `max_dev_rounds`:

- `dev_round` increments on every developer-agent invocation (initial
  call + each tool round-trip).
- Once `dev_round >= max_dev_rounds` the next call **drops `bind_tools`**.
  The LLM physically cannot emit `tool_calls` without bound tools, so
  it has to produce final text. This is enforcement-by-construction,
  not a prompt instruction the model might ignore.
- A short note is appended to the prompt explaining the situation so the
  output isn't a confused half-thought.

Default: 6. Override per node:

```yaml
nodes:
  developer:
    role: developer
    tools: ["read_text", "shell"]
    tool_mode: unrestricted
    max_dev_rounds: 8           # raises the ceiling for tool-heavy tasks
```

The example in `examples/dev_runs_tests.py` uses 8 because the LLM has
to glob, read, write, and run pytest — typically 4-5 rounds even for
the simple bug fix.

## Tracing what happened

Tool calls round-trip through `state.dev_messages` (the developer ReAct
working memory). To see what the LLM did after a run:

```python
snap = await service.snapshot(task_id)
state = snap.events  # or use service.graph.aget_state for full state
```

For richer introspection, drop a checkpointer and inspect the SQLite
DB directly — `langgraph_checkpoints` rows hold every intermediate
`dev_messages` value. The `examples/dev_runs_tests.py` sandbox lives at
`./snapshots/dev_runs_tests/<task_id>/` so you can also just `ls` what
the developer wrote.

## Security envelope

Each layer is meant to be independently sufficient — the goal is that
removing any one of them doesn't enable the next attack:

1. **Path containment** ([_safety.py](../agent_room/tools/_safety.py)):
   `resolve_within_root` canonicalizes `..`, symlinks, and absolute
   paths, then refuses anything outside `root`. Blocks the obvious
   "read /etc/passwd via `../../etc/passwd`" attempts.
2. **Allowlist-only shell**: `shell=False` means metacharacters reach
   the program literally, not the shell. The allowlist check on the
   head token blocks running anything not pre-approved. The metachar
   reject (`;`, `&&`, `|`, `>`) is belt-and-suspenders for argv items
   that look like compound shell expressions.
3. **Permission policy**: deny-by-default for write/exec at the spec
   layer, before any tool runs. Filters all listed tools through
   `apply_policy(...)` based on `tool_mode`. `approval` adds a per-call
   runtime gate via `make_approval_wrapper` — every call halts on
   `interrupt()` until a human resumes.
4. **Build-time resolution**: `resolve_tool_names` and the policy filter
   both run at `build_from_spec` time. A spec that mentions an unknown
   tool, or whose policy filters everything, fails before the graph
   compiles — never at runtime mid-task.
5. **Safe server defaults**: an unset shell allowlist disables ShellTool and
   the runtime defaults to `read_only`. Direct subprocess execution additionally
   requires `AGENT_ROOM_ALLOW_DIRECT_SHELL=1`; this is a local-development
   escape hatch, not a production setting.
6. **Remote sandbox boundary**: configured `sandbox_exec` calls are approved
   like other mutations and sent to a dedicated runner. See
   [`sandbox-security.md`](sandbox-security.md).

Test coverage is in [`tests/test_tools.py`](../tests/test_tools.py)
(35 tests covering path traversal, command injection, metachar rejection,
allowlist enforcement) plus
[`tests/test_tool_policy.py`](../tests/test_tool_policy.py) (16 tests
covering policy semantics + spec validation + end-to-end through
`build_from_spec`) plus
[`tests/test_tool_approval.py`](../tests/test_tool_approval.py) (22 tests
covering the runtime approval gate: decision parsing, halt-then-resume,
deny path with synthetic ToolMessage, conservative-default-on-malformed-decisions).

## What's not (yet) here

- **Approval batching across calls** — when an `AIMessage` carries multiple
  parallel tool calls, the current `service.resume(at_node="tool_call",
  decision=...)` applies the *same* verdict to every pending call in that
  batch. Per-call decisions (a list of decisions matched by index) are
  reserved for v0.4 if real workloads call for it.
- **Output spill** — large tool outputs currently sit in `dev_messages`
  in full (after the per-call 8 KB truncate). v0.4 merges this with the
  artifact spill design (RISK-9, ADR-0009 pending).
- **Tools on non-developer roles** — only `role="developer"` is wired
  for ReAct in v0.3. Listing `tools` on a `reviewer` / `planner` /
  `delivery` node raises at build time.
- **Auto-discovery** — hermes-agent's AST scan + module-import-time
  registration was deliberately not ported. With ≤ ~10 tools an
  explicit `register_*` call is cleaner; the cost is paying for one
  function call at startup.

## Reference

| File | Purpose |
|---|---|
| [`agent_room/tools/__init__.py`](../agent_room/tools/__init__.py) | Public API: `Registry`, `register_builtin_tools`, the 4 tool classes, `apply_policy`, `is_read_only`, `resolve_tool_names`. |
| [`agent_room/tools/registry.py`](../agent_room/tools/registry.py) | `ToolEntry` dataclass, `Registry` (RLock + dict), `default_registry()` singleton. |
| [`agent_room/tools/fs.py`](../agent_room/tools/fs.py) | `ReadTextTool` / `WriteTextTool` / `GlobTool`. |
| [`agent_room/tools/shell.py`](../agent_room/tools/shell.py) | `ShellTool` with allowlist, timeout, output cap. |
| [`agent_room/tools/_safety.py`](../agent_room/tools/_safety.py) | `resolve_within_root`, `enforce_command_allowlist`. |
| [`agent_room/tools/policy.py`](../agent_room/tools/policy.py) | `PermissionMode`, `apply_policy`, `is_read_only`. |
| [`agent_room/tools/_approval.py`](../agent_room/tools/_approval.py) | `make_approval_wrapper` — runtime gate for `tool_mode: approval` (interrupts via `langgraph.types.interrupt`). |
| [`agent_room/tools/spec_resolver.py`](../agent_room/tools/spec_resolver.py) | `resolve_tool_names(names, registry) -> [BaseTool]`. |
| [`agent_room/roles/developer_react.py`](../agent_room/roles/developer_react.py) | `make_developer_react` — agent half of the ReAct subgraph. |
| [`examples/dev_runs_tests.py`](../examples/dev_runs_tests.py) | End-to-end demo: real LLM fixes a buggy `calc.py` + verifies via `pytest`. |
