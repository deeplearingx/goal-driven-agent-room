"""v0.3 §3.9 — developer really runs `pytest` and ships a fix.

This is the v0.3 exit-criterion demo from [PLAN.md §3]:

    "developer 能跑 pytest tests/ 并把结果回灌给 reviewer"

It seeds a tiny sandbox project with a deliberate bug, then drives the full
graph against a real LLM (defaults to whatever `AGENT_ROOM_DEFAULT_MODEL`
resolves to — typically Volcano Ark via `ANTHROPIC_BASE_URL` /
`ANTHROPIC_AUTH_TOKEN`). The developer node has the four built-in tools
(`glob` / `read_text` / `write_text` / `shell` with `pytest` allowlisted)
under `tool_mode: unrestricted`, so it can:

    1. discover the buggy module,
    2. read it to understand the failure,
    3. write a fix,
    4. invoke `pytest` to verify,
    5. emit final code that the reviewer then approves.

Run:
    cp .env.example .env  # then fill in real creds
    python -m examples.dev_runs_tests

Skips quietly when no provider is configured, so this stays safe to invoke
from CI or hooks. Sandbox lives at ./snapshots/dev_runs_tests/<task_id>/
so you can inspect what the LLM actually did.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from agent_room.config import RoleBindings, load_settings
from agent_room.graph import build_with_sqlite_checkpointer
from agent_room.schemas import TaskRequest
from agent_room.service import AgentRoomService
from agent_room.spec import GraphSpec
from agent_room.tools import (
    GlobTool,
    ReadTextTool,
    Registry,
    ShellTool,
    ToolEntry,
    WriteTextTool,
)

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = ROOT / "snapshots" / "dev_runs_tests"

BUGGY_CALC = dedent(
    """\
    def add(a: int, b: int) -> int:
        # Bug: subtracts instead of adding. The test below catches it.
        return a - b


    def mul(a: int, b: int) -> int:
        return a * b
    """
)

TEST_CALC = dedent(
    """\
    from calc import add, mul


    def test_add_basic():
        assert add(2, 3) == 5


    def test_add_negative():
        assert add(-1, 1) == 0


    def test_mul_basic():
        assert mul(4, 5) == 20
    """
)


def _make_sandbox(task_id: str) -> Path:
    """Create a fresh per-task sandbox seeded with the buggy module + tests."""
    sandbox = SANDBOX_ROOT / task_id
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    (sandbox / "calc.py").write_text(BUGGY_CALC, encoding="utf-8")
    (sandbox / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    return sandbox


def _build_registry(sandbox: Path) -> Registry:
    """Wire tools whose `root` / `cwd` are pinned to the sandbox.

    We register the shell tool manually (rather than via
    `register_builtin_tools(shell_allowlist=...)`) because that helper
    doesn't expose `cwd`. The point of this example is showing how to
    plug a domain-scoped tool set into a custom NodeSpec — the two
    minutes of extra setup is part of the demo.
    """
    reg = Registry()
    reg.register(
        ToolEntry(name="read_text", toolset="filesystem", tool=ReadTextTool(root=str(sandbox)))
    )
    reg.register(
        ToolEntry(name="write_text", toolset="filesystem", tool=WriteTextTool(root=str(sandbox)))
    )
    reg.register(ToolEntry(name="glob", toolset="filesystem", tool=GlobTool(root=str(sandbox))))
    reg.register(
        ToolEntry(
            name="shell",
            toolset="shell",
            tool=ShellTool(allowlist=["pytest"], cwd=str(sandbox), timeout_s=30.0),
        )
    )
    return reg


def build_spec() -> GraphSpec:
    """Full pipeline + tool-using developer.

    `tool_mode: unrestricted` is required because the developer needs to
    write files and run a shell command. The default `read_only` mode would
    filter out write_text and shell, defeating the whole demo.
    """
    return GraphSpec.model_validate(
        {
            "name": "dev_runs_tests",
            "entry": "planner",
            "nodes": {
                "planner": {"role": "planner"},
                "developer": {
                    "role": "developer",
                    "tools": ["glob", "read_text", "write_text", "shell"],
                    "tool_mode": "unrestricted",
                    "max_dev_rounds": 8,
                },
                "reviewer": {"role": "reviewer"},
                "delivery": {"role": "delivery"},
            },
            "edges": [
                {"from": "planner", "to": "developer"},
                {"from": "developer", "to": "reviewer"},
                {
                    "from": "reviewer",
                    "branches": [
                        {"on": "developer", "to": "developer"},
                        {"on": "delivery", "to": "delivery"},
                        {"on": "halt", "to": "__end__"},
                    ],
                },
                {"from": "delivery", "to": "__end__"},
            ],
        }
    )


def _have_credentials(settings) -> bool:
    """True iff at least one provider has usable credentials in env."""
    return bool(
        settings.anthropic_auth_token or settings.anthropic_api_key or settings.openai_api_key
    )


async def main() -> int:
    settings = load_settings()
    if not _have_credentials(settings):
        print(
            "No provider credentials found in env "
            "(ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / OPENAI_API_KEY). "
            "This example needs a real LLM — skipping. "
            "See .env.example for setup."
        )
        return 0

    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    task_id = AgentRoomService.new_task_id()
    sandbox = _make_sandbox(task_id)
    registry = _build_registry(sandbox)

    print(f"Sandbox:  {sandbox.relative_to(ROOT)}")
    print(f"Provider: model={settings.default_model!r} base_url={settings.anthropic_base_url!r}")
    print(f"Task ID:  {task_id}\n")

    bindings = RoleBindings(settings=settings)
    spec = build_spec()
    db_path = str(SANDBOX_ROOT / "dev_runs_tests.db")

    async with build_with_sqlite_checkpointer(
        bindings, db_path=db_path, spec=spec, registry=registry
    ) as graph:
        service = AgentRoomService(graph)
        req = TaskRequest(
            title="Fix the failing add() in calc.py",
            description=(
                "There is a tiny project at the filesystem root (just calc.py and "
                "test_calc.py). `pytest` currently fails because add() is buggy. "
                "Use the tools to discover the project layout, read the relevant "
                "files, fix the bug in calc.py, and run pytest to confirm all tests "
                "pass before emitting your final answer. Do NOT modify the test file."
            ),
            max_revisions=2,
        )
        result = await service.run(req, task_id=task_id)

    print(f"--- Run finished: status={result.status} rounds={result.rounds} ---\n")

    if result.code:
        print(f"Final code ({len(result.code)} chars):\n{result.code}\n")
    if result.delivery:
        print(f"Delivery:\n{result.delivery}\n")

    final = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short"],
        cwd=str(sandbox),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    print(f"--- Final pytest in sandbox (exit {final.returncode}) ---")
    print(final.stdout)
    if final.stderr.strip():
        print("--- stderr ---")
        print(final.stderr)

    return 0 if final.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
