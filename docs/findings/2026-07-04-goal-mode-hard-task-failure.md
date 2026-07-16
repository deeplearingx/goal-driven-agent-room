# Goal mode hard-task failure analysis (2026-07-04)

## Symptom

Goal-mode tasks with long ReAct trajectories could terminate with a generic
task error before the configured `max_iterations` limit. On Windows, commands
using an absolute Python executable could also fail verification repeatedly.
A fresh GitHub archive could not start at all because the memory package was
absent.

## Root causes

1. **LangGraph recursion limit under-counted valid supervisor detours.**
   The old limit was 928 supersteps. A legal 50-iteration goal run can consume
   all eight developer tool rounds per iteration and route through
   `supervisor -> planner` after every third failure. The formula omitted the
   maximum 16 supervisor plus 16 replan steps. The run therefore raised
   `GraphRecursionError` before the deterministic `max_iterations` brake.

2. **POSIX command parsing corrupted Windows executable paths.**
   `shlex.split()` treated backslashes in `D:\\...\\python.exe` as escapes.
   The corrupted head token no longer matched the allowlist. Developer shell
   calls and the goal verifier share this function, so the verifier recorded a
   false failure on every round.

3. **The public source archive omitted `agent_room/memory`.**
   A broad `.gitignore` rule, `memory/`, matched nested directories as well as
   the intended root runtime directory. Imports from `agent_room.memory` then
   failed during application startup and test collection.

4. **Per-request goal graph selection was not persisted.**
   Creation used `req.graph`, but later snapshot/resume handlers rebuilt the
   service with the server default graph. A goal task paused for human input
   could resume as `full_react`, bypassing its verifier loop.

## Fixes

- Added the worst-case supervisor/replan allowance to
  `GRAPH_RECURSION_LIMIT`.
- Match an allowlisted command head verbatim before parsing only its argument
  tail, preserving Windows paths without enabling `shell=True`.
- Restored the complete memory package and narrowed the ignore rule to
  `/memory/`.
- Added `graph_preset` to persisted session metadata (with an SQLite migration)
  and use it for task snapshot/resume service selection.

## Regression evidence

- Added a maximum legal goal trajectory: 50 iterations, eight tool calls per
  iteration, and `replan` at every stuck threshold. It now reaches round 50 and
  returns the business-level `failed` result instead of a framework exception.
- Added graph-preset persistence coverage across database close/reopen.
- Final local validation: **616 passed, 1 skipped**; Ruff clean; mypy strict
  reports no issues in 76 source files.
