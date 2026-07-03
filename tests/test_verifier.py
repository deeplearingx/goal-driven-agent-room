"""Verifier node — the goal mode's objective oracle (PLAN.md goal-mode section).

Real subprocess runs (python -c) — deterministic, zero network, fast.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from agent_room.roles.verifier import make_verifier
from agent_room.tools import Registry, register_builtin_tools


def _verifier(tmp_path: Path, allowlist: list[str] | None = None):
    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))
    return make_verifier(registry=reg, shell_allowlist=allowlist or ["python", "python3"])


# ---------- no oracle configured ----------


async def test_no_verify_command_returns_none_verification(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    update = await verifier({"title": "t"})
    assert update == {"verification": None}


# ---------- pass / fail ----------


async def test_exit_zero_passes(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    update = await verifier({"verify_command": 'python -c "import sys; sys.exit(0)"'})
    v = update["verification"]
    assert v.passed is True
    assert v.exit_code == 0
    assert update["verify_round"] == 1
    assert update["events"][0].type == "verification_completed"
    assert "dev_messages" not in update  # pass pushes nothing


async def test_nonzero_exit_fails_and_captures_output(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    update = await verifier(
        {"verify_command": "python -c \"print('boom hint'); import sys; sys.exit(3)\""}
    )
    v = update["verification"]
    assert v.passed is False
    assert v.exit_code == 3
    assert "boom hint" in v.output_tail


async def test_failure_pushes_feedback_into_dev_messages(tmp_path: Path) -> None:
    """The core of goal-mode iteration: the failure output must reach the
    in-flight ReAct developer's transcript, with a fresh tool budget."""
    verifier = _verifier(tmp_path)
    existing = [AIMessage(content="my previous attempt")]
    update = await verifier(
        {
            "verify_command": "python -c \"print('AssertionError: expected 4'); exit(1)\"",
            "dev_messages": existing,
            "dev_round": 7,
        }
    )
    pushed = update["dev_messages"]
    assert len(pushed) == 1
    assert isinstance(pushed[0], HumanMessage)
    assert "AssertionError: expected 4" in pushed[0].content
    assert "Verification failed" in pushed[0].content
    assert update["dev_round"] == 0


async def test_failure_with_empty_transcript_pushes_nothing(tmp_path: Path) -> None:
    """Non-ReAct developer (no dev_messages) — nothing to push into."""
    verifier = _verifier(tmp_path)
    update = await verifier({"verify_command": 'python -c "exit(1)"'})
    assert "dev_messages" not in update
    assert "dev_round" not in update


# ---------- verify_round accumulates ----------


async def test_verify_round_increments_from_state(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    update = await verifier({"verify_command": 'python -c "exit(0)"', "verify_round": 4})
    assert update["verify_round"] == 5
    assert update["verification"].round == 5


# ---------- allowlist / spawn containment ----------


async def test_command_not_in_allowlist_fails_visibly(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path, allowlist=["pytest"])
    update = await verifier({"verify_command": 'python -c "exit(0)"'})
    v = update["verification"]
    assert v.passed is False
    assert v.exit_code is None
    assert "not in allowlist" in v.output_tail


async def test_shell_metacharacters_rejected(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    update = await verifier({"verify_command": 'python -c "exit(0)" && rm -rf /'})
    v = update["verification"]
    assert v.passed is False
    assert v.exit_code is None


async def test_nonexistent_binary_fails_visibly(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path, allowlist=["definitely-not-a-real-binary"])
    update = await verifier({"verify_command": "definitely-not-a-real-binary --help"})
    v = update["verification"]
    assert v.passed is False
    assert v.exit_code is None
    assert "failed to start" in v.output_tail


# ---------- verify_files reseed (anti-tamper) ----------


async def test_verify_files_seeded_before_run(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    update = await verifier(
        {
            "verify_command": "python check.py",
            "verify_files": {"check.py": "import sys; sys.exit(0)"},
        }
    )
    assert update["verification"].passed is True
    assert (tmp_path / "check.py").read_text() == "import sys; sys.exit(0)"


async def test_verify_files_overwrite_tampered_test(tmp_path: Path) -> None:
    """The anti-cheat mechanism: a developer that rewrote the check to always
    pass gets its edit overwritten before the oracle runs."""
    # Developer "tampered": made the check trivially pass.
    (tmp_path / "check.py").write_text("import sys; sys.exit(0)")
    verifier = _verifier(tmp_path)
    update = await verifier(
        {
            "verify_command": "python check.py",
            # The canonical check demands solution.py exist — it doesn't.
            "verify_files": {
                "check.py": "import sys, os; sys.exit(0 if os.path.exists('solution.py') else 1)"
            },
        }
    )
    assert update["verification"].passed is False  # tamper didn't stick


async def test_verify_files_path_escape_rejected(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    import pytest

    with pytest.raises(ValueError, match="escapes root"):
        await verifier(
            {
                "verify_command": 'python -c "exit(0)"',
                "verify_files": {"../outside.py": "print('escape')"},
            }
        )
