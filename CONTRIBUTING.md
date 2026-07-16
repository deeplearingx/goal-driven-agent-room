# Contributing

Companion to [CLAUDE.md](CLAUDE.md). This file holds the *git / commit /
PR / dangerous-ops* conventions; CLAUDE.md holds the architectural rules.

## Branches

- `main`: stable, `pytest -q` must be green.
- `feature/<topic>`: one feature per branch, PR into `main`.
- No direct push to `main`.

## Commit message

Format: `<scope>: <imperative summary>`

Scope: `graph` / `service` / `roles` / `server` / `cli` / `events` /
`schemas` / `config` / `tests` / `docs` / `chore`.

```
graph: add timeout edge from reviewer when LLM stalls
roles: developer now includes previous code in revision prompt
docs: clarify need_user_decision resume semantics in CLAUDE.md
```

## PR description template

```
## What
（one line）

## Why
（motivation / PLAN.md section）

## How
- changed X
- added Y
- removed Z

## Test
- [ ] pytest passes
- [ ] new tests cover the changed path
- [ ] CLAUDE.md update needed?
- [ ] PLAN.md update needed?
```

## Dangerous ops (require explicit confirmation)

- Deleting tests
- Modifying `ReviewerDecision` fields
- Changing FastAPI endpoint paths
- Upgrading LangGraph major version
- Adding a new top-level dependency

## Local quality gates

Before pushing:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff format --check . && .venv/bin/ruff check .
.venv/bin/mypy --strict agent_room
.venv/bin/python -m tests._independence_driver
```

CI runs the same gates.
