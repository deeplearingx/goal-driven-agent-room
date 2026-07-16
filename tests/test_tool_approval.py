"""v0.3.x P2-A: approval mode end-to-end (interrupt → resume).

Covers the runtime gate added in `agent_room/tools/_approval.py` plus the
new `service.resume(at_node="tool_call")` path:

- Build-time: ToolNode under `tool_mode: approval` carries
  `awrap_tool_call=make_approval_wrapper()` (regression guard).
- Decision parsing: approve / deny tokens, dict form with custom message,
  unknown / malformed shapes default to deny.
- Approve path: graph halts on first tool call → `service.snapshot` reports
  status='awaiting_user' + pending_tool_calls populated; resume("approve")
  runs the tool and the loop continues.
- Deny path: resume("deny") synthesizes a denial ToolMessage; the LLM sees
  it on its next turn.
- Parallel model output is serialized so one approval authorizes one call.
- service.resume requires a string for non-tool_call paths (legacy),
  accepts string or dict for tool_call.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from agent_room.config import RoleBindings
from agent_room.graph import build_from_spec
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService
from agent_room.spec import GraphSpec
from agent_room.tools import (
    Registry,
    register_builtin_tools,
)
from agent_room.tools._approval import _classify_decision, make_approval_wrapper
from tests.fakes import FakeReviewerLLM


class ScriptedToolCallingLLM(BaseChatModel):
    """Same pattern as `tests/test_developer_react.ScriptedToolCallingLLM`.

    Each script entry is either a final-text string OR a list of tool_calls.
    `bind_tools` is a no-op so the script alone drives behavior.
    """

    script: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-calling-approval"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:  # type: ignore[override]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        idx = len(self._used)
        if idx >= len(self.script):
            raise RuntimeError(
                f"ScriptedToolCallingLLM exhausted: {idx} calls but only "
                f"{len(self.script)} scripted responses"
            )
        entry = self.script[idx]
        self._used.append(entry)
        if isinstance(entry, str):
            msg = AIMessage(content=entry)
        else:
            msg = AIMessage(content="", tool_calls=entry)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def model_post_init(self, __ctx: Any) -> None:
        object.__setattr__(self, "_used", [])


def _bindings(scripted: ScriptedToolCallingLLM) -> RoleBindings:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    return RoleBindings(
        planner=FakeListChatModel(responses=["1. plan"]),
        developer=scripted,
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[
                ReviewerDecision(decision="approved", feedback="ok", issues=[], confidence=0.9)
            ],
        ),
        delivery=FakeListChatModel(responses=["# done"]),
    )


def _build_approval_spec() -> GraphSpec:
    return GraphSpec.model_validate(
        {
            "name": "approval-test",
            "entry": "developer",
            "nodes": {
                "developer": {
                    "role": "developer",
                    "tools": ["read_text", "write_text"],
                    "tool_mode": "approval",
                    "max_dev_rounds": 4,
                },
                "reviewer": {"role": "reviewer"},
                "delivery": {"role": "delivery"},
            },
            "edges": [
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


# ---------- decision parser ----------


@pytest.mark.parametrize(
    ("decision", "expected_verdict"),
    [
        ("approve", "approve"),
        ("Approve", "approve"),
        ("yes", "approve"),
        ("y", "approve"),
        ("ok", "approve"),
        ("deny", "deny"),
        ("DENIED", "deny"),
        ("no", "deny"),
        ("reject", "deny"),
    ],
)
def test_classify_decision_string_tokens(decision: str, expected_verdict: str):
    verdict, _ = _classify_decision(decision)
    assert verdict == expected_verdict


def test_classify_decision_dict_approve():
    verdict, msg = _classify_decision({"action": "approve"})
    assert verdict == "approve"
    assert msg == ""


def test_classify_decision_dict_deny_with_custom_message():
    verdict, msg = _classify_decision({"action": "deny", "message": "policy says no"})
    assert verdict == "deny"
    assert msg == "policy says no"


def test_classify_decision_dict_deny_default_message():
    verdict, msg = _classify_decision({"action": "deny"})
    assert verdict == "deny"
    assert "denied" in msg.lower()


@pytest.mark.parametrize("decision", [None, 42, [], {"action": "maybe"}, "shrug"])
def test_classify_decision_unknown_defaults_to_deny(decision: Any):
    """Conservative: anything we don't understand is a deny, not an approve."""
    verdict, msg = _classify_decision(decision)
    assert verdict == "deny"
    assert msg  # always produces a non-empty refusal message


# ---------- end-to-end: approve, then deny ----------


def _read_tool_call(call_id: str = "tc1") -> list[dict[str, Any]]:
    return [{"id": call_id, "name": "read_text", "args": {"path": "calc.py"}}]


@pytest.mark.asyncio
async def test_approval_halts_then_resume_approve_runs_tool(tmp_path):
    """First turn: LLM emits a read_text call. Approval gate halts.

    snapshot() should report awaiting_user + the pending tool call. After
    resume('approve'), the tool runs, the LLM produces final code, and the
    run completes.
    """
    # Seed a real file so the read tool succeeds when approved.
    (tmp_path / "calc.py").write_text("x = 1", encoding="utf-8")

    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))

    scripted = ScriptedToolCallingLLM(
        script=[
            _read_tool_call(),  # turn 1: ask for read
            "```python\nx = 1\n```",  # turn 2: emit final code
        ]
    )
    bindings = _bindings(scripted)
    spec = _build_approval_spec()
    graph = build_from_spec(spec, bindings, registry=reg, checkpointer=MemorySaver())
    service = AgentRoomService(graph)

    task_id = AgentRoomService.new_task_id()
    req = TaskRequest(title="t", description="d")
    result = await service.run(req, task_id=task_id)

    # Halted at first tool call.
    assert result.status == "awaiting_user"
    assert len(result.pending_tool_calls) == 1
    pending = result.pending_tool_calls[0]
    assert pending["action"] == "approve_tool_call"
    assert pending["name"] == "read_text"
    assert pending["args"] == {"path": "calc.py"}
    assert pending["id"] == "tc1"
    # The graph hasn't reached reviewer yet.
    assert result.review is None

    # Resume with approve → tool runs, LLM produces code, reviewer approves.
    final = await service.resume(task_id, "approve", at_node="tool_call")
    assert final.status == "completed"
    assert final.pending_tool_calls == []
    assert final.code is not None and "```python" in final.code


@pytest.mark.asyncio
async def test_approval_resume_deny_synthesizes_error_tool_message(tmp_path):
    """Deny path: tool doesn't run; LLM sees a status='error' ToolMessage."""
    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))

    scripted = ScriptedToolCallingLLM(
        script=[
            _read_tool_call(),  # turn 1: ask for read (will be denied)
            "```python\nx = 2\n```",  # turn 2: emit final code anyway
        ]
    )
    bindings = _bindings(scripted)
    spec = _build_approval_spec()
    graph = build_from_spec(spec, bindings, registry=reg, checkpointer=MemorySaver())
    service = AgentRoomService(graph)

    task_id = AgentRoomService.new_task_id()
    await service.run(TaskRequest(title="t", description="d"), task_id=task_id)

    final = await service.resume(
        task_id,
        {"action": "deny", "message": "policy says no writes"},
        at_node="tool_call",
    )
    assert final.status == "completed"
    # Inspect graph state for the synthetic ToolMessage.
    state = await graph.aget_state({"configurable": {"thread_id": task_id}})
    dev_msgs = state.values.get("dev_messages", [])
    tool_msgs = [m for m in dev_msgs if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].status == "error"
    assert "policy says no writes" in tool_msgs[0].content
    assert tool_msgs[0].tool_call_id == "tc1"


@pytest.mark.asyncio
async def test_approval_unknown_decision_defaults_to_deny(tmp_path):
    """A garbled decision must NOT silently approve a write tool.

    Conservatism is the whole point of approval mode — refusing to fail
    safe would defeat the feature.
    """
    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))

    scripted = ScriptedToolCallingLLM(
        script=[
            _read_tool_call(),  # ask
            "```python\ndone\n```",  # then finalize
        ]
    )
    bindings = _bindings(scripted)
    spec = _build_approval_spec()
    graph = build_from_spec(spec, bindings, registry=reg, checkpointer=MemorySaver())
    service = AgentRoomService(graph)

    task_id = AgentRoomService.new_task_id()
    await service.run(TaskRequest(title="t", description="d"), task_id=task_id)

    final = await service.resume(task_id, "shrug", at_node="tool_call")
    state = await graph.aget_state({"configurable": {"thread_id": task_id}})
    tool_msgs = [m for m in state.values.get("dev_messages", []) if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].status == "error"
    assert final.status == "completed"


@pytest.mark.asyncio
async def test_approval_serializes_parallel_tool_calls(tmp_path):
    """One approval must never authorize an implicit batch of operations."""
    (tmp_path / "first.txt").write_text("first", encoding="utf-8")
    (tmp_path / "second.txt").write_text("second", encoding="utf-8")

    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))
    scripted = ScriptedToolCallingLLM(
        script=[
            [
                {"id": "tc1", "name": "read_text", "args": {"path": "first.txt"}},
                {"id": "tc2", "name": "read_text", "args": {"path": "second.txt"}},
            ],
            "```python\ndone = True\n```",
        ]
    )
    graph = build_from_spec(
        _build_approval_spec(),
        _bindings(scripted),
        registry=reg,
        checkpointer=MemorySaver(),
    )
    service = AgentRoomService(graph)
    task_id = AgentRoomService.new_task_id()

    halted = await service.run(TaskRequest(title="t", description="d"), task_id=task_id)
    assert [call["id"] for call in halted.pending_tool_calls] == ["tc1"]

    final = await service.resume(task_id, "approve", at_node="tool_call")
    assert final.status == "completed"
    state = await graph.aget_state({"configurable": {"thread_id": task_id}})
    tool_msgs = [m for m in state.values.get("dev_messages", []) if isinstance(m, ToolMessage)]
    assert [message.tool_call_id for message in tool_msgs] == ["tc1"]
    serialization_events = [
        event
        for event in state.values.get("events", [])
        if event.type == "parallel_tool_calls_serialized"
    ]
    assert len(serialization_events) == 1
    assert serialization_events[0].payload["deferred_ids"] == ["tc2"]


# ---------- API guards ----------


@pytest.mark.asyncio
async def test_resume_dict_decision_only_allowed_for_tool_call():
    """Legacy at_node='reviewer' / 'planner' must reject non-string decisions."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    bindings = RoleBindings(
        planner=FakeListChatModel(responses=["1"]),
        developer=FakeListChatModel(responses=["x"]),
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[
                ReviewerDecision(decision="approved", feedback="ok", issues=[], confidence=0.9)
            ],
        ),
        delivery=FakeListChatModel(responses=["d"]),
    )
    # Build any compiled graph just so we can construct the service.
    graph = build_from_spec(
        GraphSpec.model_validate(
            {
                "name": "n",
                "entry": "d",
                "nodes": {"d": {"role": "developer"}, "r": {"role": "reviewer"}},
                "edges": [
                    {"from": "d", "to": "r"},
                    {"from": "r", "to": "__end__"},
                ],
            }
        ),
        bindings,
        checkpointer=MemorySaver(),
    )
    service = AgentRoomService(graph)
    with pytest.raises(TypeError, match="dict decisions are only valid"):
        await service.resume("any-task", {"action": "approve"}, at_node="reviewer")


# ---------- wrapper smoke ----------


def test_make_approval_wrapper_is_callable():
    """The factory must return a callable; this prevents a silent rename."""
    wrapper = make_approval_wrapper()
    assert callable(wrapper)
