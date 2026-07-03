"""ShellTool — restricted subprocess runner.

Defaults to deny-everything; you must pass `allowlist=[...]` to enable any
commands. Even with an allowlist, the tool:

- never uses `shell=True` (uses `subprocess.run([argv...])`),
- enforces a default 30s timeout,
- truncates stdout/stderr to ~8 KB,
- rejects shell metacharacters in tokens (no `;`, `&&`, `|`, redirects, etc).

If you need pipelines or compound commands, decompose them into separate
tool calls. This is intentional — RISK-5 is a top-3 risk and the cost of
"convenience" features here is a vulnerability surface.
"""

from __future__ import annotations

import subprocess
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agent_room.guardrail import Guardrail
from agent_room.tools._safety import enforce_command_allowlist

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_OUTPUT_BYTES = 8 * 1024


def _truncate_bytes(b: bytes, max_bytes: int) -> str:
    if len(b) <= max_bytes:
        return b.decode("utf-8", errors="replace")
    head = b[:max_bytes].decode("utf-8", errors="replace")
    return f"{head}\n[... truncated, {len(b) - max_bytes} more bytes]"


class ShellInput(BaseModel):
    command: str = Field(
        description=(
            "Command string. The first token must be in the allowlist. "
            "Shell metacharacters (;, &&, |, >, etc) are rejected — issue "
            "multiple tool calls instead."
        )
    )


class ShellTool(BaseTool):
    name: str = "shell"
    description: str = (
        "Run a single allowlisted shell command and return stdout/stderr. "
        "Subject to a per-call timeout; output is truncated. "
        "Use this to run tests, linters, or other read-mostly commands."
    )
    args_schema: ClassVar[type[BaseModel]] = ShellInput

    allowlist: list[str] = Field(default_factory=list)
    timeout_s: float = DEFAULT_TIMEOUT_S
    cwd: str | None = None
    max_output_bytes: int = DEFAULT_OUTPUT_BYTES
    # §6.9-3 "tool_call" checkpoint — scans `command` before execution.
    # `None` (default) = no scan, zero behavior change.
    guardrail: Guardrail | None = None

    def _run(self, command: str) -> str:
        if self.guardrail is not None:
            self.guardrail.check(command, checkpoint="tool_call")
        argv = enforce_command_allowlist(command, self.allowlist)
        try:
            completed = subprocess.run(  # noqa: S603 — allowlist enforced above
                argv,
                cwd=self.cwd,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial_out = _truncate_bytes(exc.stdout or b"", self.max_output_bytes)
            partial_err = _truncate_bytes(exc.stderr or b"", self.max_output_bytes)
            return (
                f"[timeout after {self.timeout_s}s]\n"
                f"--- stdout (partial) ---\n{partial_out}\n"
                f"--- stderr (partial) ---\n{partial_err}"
            )
        except FileNotFoundError as exc:
            return f"[command not found: {argv[0]}] {exc}"

        out = _truncate_bytes(completed.stdout, self.max_output_bytes)
        err = _truncate_bytes(completed.stderr, self.max_output_bytes)
        return f"[exit {completed.returncode}]\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
