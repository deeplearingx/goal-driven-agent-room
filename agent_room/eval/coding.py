"""CodingTask: the correctness oracle (EVAL-1b).

Seeds a throwaway sandbox with source + tests, pins the developer's file/shell
tools to that sandbox via `registry()`, and verifies by running the test command
inside it — pass iff the suite exits 0. This is the real-output oracle the
behavioural oracle (`BehavioralTask`) is a proxy for: it scores whether the work
*works*, not whether the run escalated.

Tool wiring mirrors `examples/dev_runs_tests.py` (fs tools rooted at the sandbox,
shell allowlisted to the test runner). The sandbox is created in `setup()` and
removed in `teardown()`, so runs never collide and nothing leaks to the repo.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agent_room.eval.task import Outcome, Verdict
from agent_room.schemas import TaskRequest
from agent_room.tools import (
    GlobTool,
    ReadTextTool,
    Registry,
    ShellTool,
    ToolEntry,
    WriteTextTool,
)


@dataclass
class CodingTask:
    """A bug-fix / implementation task scored by running its test suite."""

    name: str
    request: TaskRequest
    files: Mapping[str, str]
    """Relative path → seed content (buggy source + the tests that catch it)."""

    hidden_tests: Mapping[str, str] = field(default_factory=dict)
    """Tests written into the sandbox only at `verify()` time, so the developer
    never sees or runs them. This is the discriminator: a config that overfits
    the visible tests passes its own pytest but fails the hidden edge cases."""

    test_command: tuple[str, ...] = ("-m", "pytest", "-q")
    """Args after the Python executable, run with cwd = sandbox."""

    shell_allowlist: tuple[str, ...] = ("pytest", "python", "python3")
    """Allowed shell command names. Includes `python` so the developer can run
    `python -m pytest` — its instinctive way to invoke the suite."""
    follow_up: str | None = None
    timeout_s: float = 60.0
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    _sandbox: Path | None = field(default=None, init=False, repr=False, compare=False)

    def _require_sandbox(self) -> Path:
        if self._sandbox is None:
            raise RuntimeError(f"CodingTask {self.name!r} used before setup()")
        return self._sandbox

    async def setup(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix=f"evaltask-{self.name}-"))
        for rel, content in self.files.items():
            path = sandbox / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._sandbox = sandbox

    async def teardown(self) -> None:
        if self._sandbox is not None:
            shutil.rmtree(self._sandbox, ignore_errors=True)
        self._sandbox = None

    def registry(self) -> Registry:
        sandbox = str(self._require_sandbox())
        reg = Registry()
        reg.register(
            ToolEntry(name="read_text", toolset="filesystem", tool=ReadTextTool(root=sandbox))
        )
        reg.register(
            ToolEntry(name="write_text", toolset="filesystem", tool=WriteTextTool(root=sandbox))
        )
        reg.register(ToolEntry(name="glob", toolset="filesystem", tool=GlobTool(root=sandbox)))
        reg.register(
            ToolEntry(
                name="shell",
                toolset="shell",
                tool=ShellTool(
                    allowlist=list(self.shell_allowlist),
                    cwd=sandbox,
                    timeout_s=self.timeout_s,
                ),
            )
        )
        return reg

    async def verify(self, outcome: Outcome) -> Verdict:
        sandbox = self._require_sandbox()
        for rel, content in self.hidden_tests.items():
            path = sandbox / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            *self.test_command,
            cwd=str(sandbox),
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return Verdict(passed=False, detail=f"test command timed out after {self.timeout_s}s")

        passed = proc.returncode == 0
        detail = f"pytest exit={proc.returncode}"
        if not passed:
            tail = stdout.decode(errors="replace").strip()[-400:]
            detail = f"{detail} | {tail}"
        return Verdict(passed=passed, detail=detail)
