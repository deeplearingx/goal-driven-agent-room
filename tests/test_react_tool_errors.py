"""F1 regression: a disallowed tool command must come back to the LLM as an
error, not crash the whole run.

Discovered by the eval harness (PLAN.md EVAL run A/B): the developer reached for
`python -m pytest` / `cd ...`, the shell allowlist rejected it with a
`ValueError`, and the exception propagated out of the ReAct loop — aborting the
graph (status stuck at `running`, reviewer/delivery never ran). A healthy agent
should see the rejection as a tool result and adapt.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agent_room.graph import build_from_spec
from agent_room.spec import GraphSpec
from agent_room.tools import Registry, ShellTool, ToolEntry
from tests.test_developer_react import (
    ScriptedToolCallingLLM,
    _bindings_with_scripted_dev,
)


def _spec_with_shell() -> GraphSpec:
    return GraphSpec.model_validate(
        {
            "name": "shell-err-test",
            "entry": "planner",
            "nodes": {
                "planner": {"role": "planner"},
                "developer": {
                    "role": "developer",
                    "tools": ["shell"],
                    "tool_mode": "unrestricted",
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
                        {"on": "delivery", "to": "delivery"},
                        {"on": "developer", "to": "developer"},
                        {"on": "halt", "to": "__end__"},
                    ],
                },
                {"from": "delivery", "to": "__end__"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_disallowed_shell_command_feeds_error_back_not_crash(tmp_path) -> None:
    reg = Registry()
    reg.register(
        ToolEntry(
            name="shell",
            toolset="shell",
            tool=ShellTool(allowlist=["pytest"], cwd=str(tmp_path)),
        )
    )
    scripted = ScriptedToolCallingLLM(
        script=[
            # Round 0: reach for a disallowed command.
            [{"id": "c1", "name": "shell", "args": {"command": "cd /tmp"}, "type": "tool_call"}],
            # Round 1: having seen the rejection, emit final code instead.
            "```python\nprint('done')\n```",
        ]
    )
    bindings = _bindings_with_scripted_dev(scripted)
    graph = build_from_spec(_spec_with_shell(), bindings, checkpointer=MemorySaver(), registry=reg)

    final = await graph.ainvoke(
        {"task_id": "t1", "title": "x", "description": "y", "max_revisions": 1},
        config={"configurable": {"thread_id": "shell-err"}},
    )

    # The rejection must not abort the run: the loop continues to round 1,
    # reviewer approves, delivery runs.
    assert final.get("delivery"), "run aborted — disallowed command crashed the loop"
    assert len(scripted._used) == 2
