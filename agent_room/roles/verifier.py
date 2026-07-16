"""Verifier node — the goal mode's objective oracle (PLAN.md goal-mode section).

Not an LLM node: deterministic framework code that runs the task's
`verify_command` in the task workspace and records pass/fail. The routing
built on top (`goal_router`) exits the develop→verify loop only when this
oracle passes — the model saying "done" is never the exit signal.

Transcript-push pattern (shared with roles/reviewer.py and roles/planner.py):
a ReAct developer's `dev_messages` is append-only and its initial prompt
never re-renders on re-entry, so any node with new information for an
in-flight developer must append it as a `HumanMessage` via the `add` reducer
(and reset `dev_round` so the next iteration gets a fresh tool budget). On
failure this node pushes the command's output tail — that's what makes each
iteration a directed fix instead of a blind retry.

Anti-tamper: `verify_files` are re-written into the workspace before EVERY
run. A developer that edits the test files to make them pass gets its edits
overwritten before the oracle executes — the real cheat path observed in
coding agents, closed mechanically rather than by prompt admonition.

Failure containment: allowlist rejection / unparseable command / spawn
failure become a `VerificationResult(passed=False, exit_code=None)` with the
reason in `output_tail`, not a raised exception — a misconfigured task should
surface as a visible failed verification, not abort the whole run.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from agent_room.schemas import Event, VerificationResult
from agent_room.state import TaskState
from agent_room.tools import ReadTextTool, Registry
from agent_room.tools._safety import enforce_command_allowlist, resolve_within_root

VERIFY_TIMEOUT_S = 60.0
OUTPUT_TAIL_CHARS = 2000

FIX_GUIDANCE = (
    "\n\nThe goal is NOT yet achieved. Read the failure output above, fix the "
    "code (not the tests — they are re-seeded before every verification), and "
    "finish with your updated final answer."
)


def make_verifier(
    *,
    registry: Registry,
    shell_allowlist: list[str],
) -> Callable[[TaskState], Awaitable[dict[str, Any]]]:
    """Build the verifier node.

    `registry` supplies the workspace root: the per-task `read_text` tool's
    `root` is the same confinement boundary every fs tool in this graph got
    at compile time (see server/react_runtime.build_server_registry), so the
    oracle runs exactly where the developer wrote. `shell_allowlist` gates
    the task-supplied command through the same head-token allowlist as the
    developer's shell tool — verify_command adds no new execution surface.
    """
    read_text = registry.get("read_text").tool
    if not isinstance(read_text, ReadTextTool):
        raise TypeError(
            f"verifier needs the registry's read_text to be a ReadTextTool "
            f"(its `root` is the workspace boundary), got {type(read_text).__name__}"
        )
    workspace = Path(read_text.root)

    async def verifier(state: TaskState) -> dict[str, Any]:
        command = state.get("verify_command")
        if not command:
            # No objective oracle configured — goal_router sends this to the
            # reviewer's subjective judgment instead.
            return {"verification": None}

        round_no = state.get("verify_round", 0) + 1
        _reseed_verify_files(workspace, state.get("verify_files") or {})
        result = await _run_verify_command(
            command, workspace=workspace, allowlist=shell_allowlist, round_no=round_no
        )

        update: dict[str, Any] = {
            "verification": result,
            "verify_round": round_no,
            "events": [
                Event(
                    type="verification_completed",
                    role="verifier",
                    round=round_no,
                    payload={"passed": result.passed, "exit_code": result.exit_code},
                )
            ],
        }
        if not result.passed and state.get("dev_messages"):
            update["dev_messages"] = [
                HumanMessage(
                    content=(
                        f"# Verification failed (round {round_no}, "
                        f"exit={result.exit_code})\n{result.output_tail}{FIX_GUIDANCE}"
                    )
                )
            ]
            update["dev_round"] = 0
        return update

    return verifier


def _reseed_verify_files(workspace: Path, verify_files: dict[str, str]) -> None:
    for rel, content in verify_files.items():
        target = resolve_within_root(workspace, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


async def _run_verify_command(
    command: str, *, workspace: Path, allowlist: list[str], round_no: int
) -> VerificationResult:
    try:
        argv = enforce_command_allowlist(command, allowlist)
    except ValueError as exc:
        return VerificationResult(
            passed=False,
            exit_code=None,
            output_tail=f"verify_command rejected: {exc}",
            round=round_no,
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return VerificationResult(
            passed=False,
            exit_code=None,
            output_tail=f"verify_command failed to start: {exc}",
            round=round_no,
        )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=VERIFY_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return VerificationResult(
            passed=False,
            exit_code=None,
            output_tail=f"verify_command timed out after {VERIFY_TIMEOUT_S}s ({shlex.join(argv)})",
            round=round_no,
        )
    tail = stdout.decode(errors="replace").strip()[-OUTPUT_TAIL_CHARS:]
    return VerificationResult(
        passed=proc.returncode == 0, exit_code=proc.returncode, output_tail=tail, round=round_no
    )
