"""Goal-mode e2e through the real compiled `goal` preset (PLAN.md goal-mode).

Every scenario drives `build_from_spec(load_preset("goal"), ...)` with a
scripted ReAct developer whose write_text tool calls really execute, and a
verifier that really runs `python check.py` in the workspace — the oracle is
a genuine subprocess exit code, not a mock. Zero network, deterministic.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agent_room.config import RoleBindings, Settings
from agent_room.graph import build_from_spec
from agent_room.routers import STUCK_EVERY
from agent_room.schemas import ReviewerDecision, StuckDecision, TaskRequest
from agent_room.service import _materialize_status
from agent_room.spec import load_preset
from agent_room.tools import Registry, register_builtin_tools
from tests.fakes import FakeReviewerLLM
from tests.test_developer_react import ScriptedToolCallingLLM

# check.py passes iff solution.py contains exactly "v2".
CHECK_PY = (
    "import sys, pathlib\n"
    "p = pathlib.Path('solution.py')\n"
    "ok = p.exists() and p.read_text() == 'v2'\n"
    "print('solution.py missing or wrong' if not ok else 'ok')\n"
    "sys.exit(0 if ok else 1)\n"
)


def _write_solution(content: str, call_id: str) -> list[dict]:
    return [
        {
            "name": "write_text",
            "args": {"path": "solution.py", "content": content},
            "id": call_id,
        }
    ]


def _build(tmp_path: Path, *, developer_script: list, supervisor=None, reviewer=None):
    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path), shell_allowlist=["python"])
    bindings = RoleBindings(
        settings=Settings(),
        planner=FakeListChatModel(responses=["1. write solution.py containing v2"]),
        developer=ScriptedToolCallingLLM(script=developer_script),
        reviewer=reviewer or FakeReviewerLLM(responses=[], decisions=[]),
        delivery=FakeListChatModel(responses=["Handoff: goal achieved"]),
        supervisor=supervisor
        or FakeReviewerLLM(responses=[], outputs_by_schema={StuckDecision: []}),
    )
    return build_from_spec(load_preset("goal"), bindings, checkpointer=MemorySaver(), registry=reg)


def _state(task_id: str, **overrides) -> dict:
    base = {
        "task_id": task_id,
        "title": "goal task",
        "description": "make check.py pass",
        "verify_command": "python check.py",
        "verify_files": {"check.py": CHECK_PY},
        "max_iterations": 10,
    }
    base.update(overrides)
    return base


async def test_fail_feedback_then_pass(tmp_path: Path) -> None:
    """The core loop: wrong attempt → oracle fails → failure lands in the
    developer's transcript → fixed attempt → oracle passes → delivery."""
    graph = _build(
        tmp_path,
        developer_script=[
            _write_solution("v1", "1"),
            "wrote v1",
            _write_solution("v2", "2"),
            "wrote v2",
        ],
    )
    result = await graph.ainvoke(_state("t1"), config={"configurable": {"thread_id": "t1"}})
    assert result["verification"].passed is True
    assert result["verify_round"] == 2
    assert result["delivery"] == "Handoff: goal achieved"
    assert _materialize_status(result) == "completed"
    # The round-1 failure output must be IN the transcript the round-2
    # developer saw — that's what makes iteration directed, not blind.
    failure_msgs = [
        m
        for m in result["dev_messages"]
        if isinstance(m, HumanMessage) and "Verification failed" in str(m.content)
    ]
    assert len(failure_msgs) == 1
    assert "solution.py missing or wrong" in str(failure_msgs[0].content)


async def test_ceiling_exhausted_fails(tmp_path: Path) -> None:
    graph = _build(tmp_path, developer_script=[_write_solution("v1", "1"), "wrote v1"])
    result = await graph.ainvoke(
        _state("t2", max_iterations=1), config={"configurable": {"thread_id": "t2"}}
    )
    assert result["verification"].passed is False
    assert result.get("delivery") is None
    assert _materialize_status(result) == "failed"


async def test_stuck_supervisor_continue_then_pass(tmp_path: Path) -> None:
    """After STUCK_EVERY consecutive failures the supervisor gets one strategy
    call; 'continue' sends the loop back to the developer, which then fixes it."""
    fails = []
    for i in range(STUCK_EVERY):
        fails += [_write_solution("v1", str(i)), f"attempt {i}"]
    supervisor = FakeReviewerLLM(
        responses=[],
        outputs_by_schema={
            StuckDecision: [StuckDecision(action="continue", reasoning="errors are changing")]
        },
    )
    graph = _build(
        tmp_path,
        developer_script=[*fails, _write_solution("v2", "fix"), "fixed"],
        supervisor=supervisor,
    )
    result = await graph.ainvoke(_state("t3"), config={"configurable": {"thread_id": "t3"}})
    assert result["stuck_decision"].action == "continue"
    assert result["verification"].passed is True
    assert _materialize_status(result) == "completed"
    supervisor_events = [e for e in result["events"] if e.type == "supervisor_decided"]
    assert len(supervisor_events) == 1  # exactly one strategy call, at round STUCK_EVERY


async def test_stuck_supervisor_ask_user_parks_run(tmp_path: Path) -> None:
    """ask_user reuses the need_user_decision contract: run parks as
    awaiting_user, resumable through the existing service.resume path."""
    fails = []
    for i in range(STUCK_EVERY):
        fails += [_write_solution("v1", str(i)), f"attempt {i}"]
    supervisor = FakeReviewerLLM(
        responses=[],
        outputs_by_schema={
            StuckDecision: [
                StuckDecision(
                    action="ask_user",
                    reasoning="the goal may be unachievable as specified",
                    question="should solution.py really contain exactly 'v2'?",
                )
            ]
        },
    )
    graph = _build(tmp_path, developer_script=fails, supervisor=supervisor)
    result = await graph.ainvoke(_state("t4"), config={"configurable": {"thread_id": "t4"}})
    assert result["review"].decision == "need_user_decision"
    assert "exactly 'v2'" in result["review"].feedback
    assert _materialize_status(result) == "awaiting_user"


async def test_stuck_supervisor_abort_fails(tmp_path: Path) -> None:
    fails = []
    for i in range(STUCK_EVERY):
        fails += [_write_solution("v1", str(i)), f"attempt {i}"]
    supervisor = FakeReviewerLLM(
        responses=[],
        outputs_by_schema={
            StuckDecision: [StuckDecision(action="abort", reasoning="clearly impossible")]
        },
    )
    graph = _build(tmp_path, developer_script=fails, supervisor=supervisor)
    result = await graph.ainvoke(_state("t5"), config={"configurable": {"thread_id": "t5"}})
    assert result["stuck_decision"].action == "abort"
    assert _materialize_status(result) == "failed"


async def test_stuck_supervisor_replan_gets_revised_plan(tmp_path: Path) -> None:
    """replan routes back through the planner; the revised plan lands in the
    developer's transcript (planner's transcript-push) before the next attempt."""
    fails = []
    for i in range(STUCK_EVERY):
        fails += [_write_solution("v1", str(i)), f"attempt {i}"]
    supervisor = FakeReviewerLLM(
        responses=[],
        outputs_by_schema={
            StuckDecision: [StuckDecision(action="replan", reasoning="same error every round")]
        },
    )
    graph = _build(
        tmp_path,
        developer_script=[*fails, _write_solution("v2", "fix"), "fixed after replan"],
        supervisor=supervisor,
    )
    result = await graph.ainvoke(_state("t6"), config={"configurable": {"thread_id": "t6"}})
    assert result["verification"].passed is True
    assert _materialize_status(result) == "completed"
    revised = [
        m
        for m in result["dev_messages"]
        if isinstance(m, HumanMessage) and "# Revised plan" in str(m.content)
    ]
    assert len(revised) == 1


async def test_no_verify_command_falls_back_to_reviewer(tmp_path: Path) -> None:
    reviewer = FakeReviewerLLM(
        responses=[], decisions=[ReviewerDecision(decision="approved", feedback="looks right")]
    )
    graph = _build(tmp_path, developer_script=["final code, no tools needed"], reviewer=reviewer)
    state = _state("t7")
    state.pop("verify_command")
    state.pop("verify_files")
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "t7"}})
    assert result.get("verification") is None
    assert result["review"].decision == "approved"
    assert _materialize_status(result) == "completed"


async def test_tampered_check_file_is_reseeded(tmp_path: Path) -> None:
    """Developer overwrites check.py to always pass — the reseed before each
    verification makes the tamper irrelevant and the oracle still fails."""
    graph = _build(
        tmp_path,
        developer_script=[
            [
                {
                    "name": "write_text",
                    "args": {"path": "check.py", "content": "import sys; sys.exit(0)"},
                    "id": "cheat",
                }
            ],
            "made the check pass",
        ],
    )
    result = await graph.ainvoke(
        _state("t8", max_iterations=1), config={"configurable": {"thread_id": "t8"}}
    )
    assert result["verification"].passed is False
    assert _materialize_status(result) == "failed"


async def test_task_request_carries_goal_fields() -> None:
    req = TaskRequest(
        title="t",
        description="d",
        verify_command="python check.py",
        verify_files={"check.py": "import sys; sys.exit(0)"},
        max_iterations=5,
    )
    assert req.verify_command == "python check.py"
    assert req.max_iterations == 5
