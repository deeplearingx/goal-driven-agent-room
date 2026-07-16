"""Regression coverage for goal-mode trajectories near the request ceilings."""

from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import MemorySaver

from agent_room.config import RoleBindings, Settings
from agent_room.graph import build_from_spec
from agent_room.schemas import StuckDecision, TaskRequest
from agent_room.service import AgentRoomService
from agent_room.spec import load_preset
from agent_room.tools import Registry, register_builtin_tools
from tests.fakes import FakeReviewerLLM
from tests.test_developer_react import ScriptedToolCallingLLM


def _maximal_developer_script(iterations: int) -> list[object]:
    script: list[object] = []
    for iteration in range(iterations):
        for tool_round in range(8):
            script.append(
                [
                    {
                        "name": "glob",
                        "args": {"pattern": "*"},
                        "id": f"glob-{iteration}-{tool_round}",
                    }
                ]
            )
        script.append(f"attempt {iteration} complete")
    return script


async def test_max_iterations_halts_cleanly_instead_of_recursion_error(tmp_path: Path) -> None:
    """Every iteration consumes the full eight-tool ReAct allowance and every
    stuck decision replans, which is the maximum-superstep valid request."""
    iterations = 50
    registry = Registry()
    register_builtin_tools(
        registry,
        fs_root=str(tmp_path),
        shell_allowlist=[sys.executable],
    )
    supervisor = FakeReviewerLLM(
        responses=[],
        outputs_by_schema={
            StuckDecision: [StuckDecision(action="replan", reasoning="try another approach")]
        },
    )
    bindings = RoleBindings(
        settings=Settings(shell_allowlist=(sys.executable,)),
        planner=FakeListChatModel(responses=["1. keep trying"]),
        developer=ScriptedToolCallingLLM(script=_maximal_developer_script(iterations)),
        reviewer=FakeReviewerLLM(responses=[], decisions=[]),
        delivery=FakeListChatModel(responses=["done"]),
        supervisor=supervisor,
    )
    graph = build_from_spec(
        load_preset("goal"), bindings, checkpointer=MemorySaver(), registry=registry
    )
    service = AgentRoomService(graph)
    result = await service.run(
        TaskRequest(
            title="hard goal",
            description="exercise the entire legal trajectory",
            verify_command=f'{sys.executable} -c "raise SystemExit(1)"',
            max_iterations=iterations,
        )
    )
    assert result.status == "failed"
    assert result.verification is not None
    assert result.verification.round == iterations
