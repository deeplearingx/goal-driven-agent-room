"""Smoke tests for the event adapter — pure function, no network."""

from __future__ import annotations

from agent_room.events import StreamEventFormatter, format_stream_event


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


def _stream_event(text: str, *, task_id: str = "t1") -> dict:
    return {
        "event": "on_chat_model_stream",
        "task_id": task_id,
        "metadata": {"langgraph_node": "developer"},
        "data": {"chunk": _Chunk(text)},
    }


def test_skip_unknown_event() -> None:
    assert format_stream_event({"event": "on_misc", "name": "x"}) is None


def test_node_start() -> None:
    out = format_stream_event({"event": "on_chain_start", "name": "planner", "task_id": "t1"})
    assert out == {"type": "node_start", "role": "planner", "task_id": "t1"}


def test_node_end_summary_for_planner() -> None:
    out = format_stream_event(
        {
            "event": "on_chain_end",
            "name": "planner",
            "task_id": "t1",
            "data": {"output": {"plan": "1. do thing\n2. do other thing"}},
        }
    )
    assert out is not None
    assert out["type"] == "node_end"
    assert out["summary"]["plan_preview"].startswith("1. do thing")


def test_node_end_summary_for_reviewer() -> None:
    class _R:
        decision = "approved"

    out = format_stream_event(
        {
            "event": "on_chain_end",
            "name": "reviewer",
            "task_id": "t1",
            "data": {"output": {"review": _R(), "revision_round": 1}},
        }
    )
    assert out is not None
    assert out["summary"] == {"decision": "approved", "round": 1}


def test_stream_formatter_redacts_complete_memory_context() -> None:
    formatter = StreamEventFormatter()

    out = formatter.format(_stream_event("before <memory-context>secret</memory-context> after"))

    assert out is not None
    assert out["text"] == "before [memory-context redacted] after"
    assert "secret" not in out["text"]
    assert formatter.flush() == []


def test_stream_formatter_redacts_memory_context_split_across_chunks() -> None:
    formatter = StreamEventFormatter()

    outs = [
        formatter.format(_stream_event("before <memory-")),
        formatter.format(_stream_event("context>secret")),
        formatter.format(_stream_event("</memory-context> after")),
    ]

    texts = [out["text"] for out in outs if out is not None]
    assert texts == ["before ", "[memory-context redacted]", " after"]
    assert "secret" not in "".join(texts)


def test_stream_formatter_flushes_partial_non_fence_text() -> None:
    formatter = StreamEventFormatter()

    out = formatter.format(_stream_event("hello <memo"))
    flushed = formatter.flush()

    assert out is not None
    assert out["text"] == "hello "
    assert flushed == [
        {
            "type": "token",
            "role": "developer",
            "task_id": "t1",
            "text": "<memo",
        }
    ]


def test_stream_formatter_drops_unclosed_memory_context_on_flush() -> None:
    formatter = StreamEventFormatter()

    out = formatter.format(_stream_event("before <memory-context>secret"))

    assert out is not None
    assert out["text"] == "before [memory-context redacted]"
    assert formatter.flush() == []


def test_tool_start_emits_tool_call() -> None:
    out = format_stream_event(
        {
            "event": "on_tool_start",
            "name": "shell",
            "task_id": "t1",
            "data": {"input": {"command": "pytest -q"}},
            "metadata": {"langgraph_node": "developer_tools"},
        }
    )
    assert out is not None
    assert out["type"] == "tool_call"
    assert out["role"] == "developer"  # `developer_tools` → developer
    assert out["tool"] == "shell"
    assert "pytest" in out["args"]


def test_tool_end_emits_tool_result() -> None:
    from langchain_core.messages import ToolMessage

    out = format_stream_event(
        {
            "event": "on_tool_end",
            "name": "shell",
            "task_id": "t1",
            "data": {"output": ToolMessage(content="3 passed", tool_call_id="c1")},
            "metadata": {"langgraph_node": "developer_tools"},
        }
    )
    assert out is not None
    assert out["type"] == "tool_result"
    assert out["tool"] == "shell"
    assert "3 passed" in out["preview"]


def test_chat_model_end_emits_usage() -> None:
    from langchain_core.messages import AIMessage

    msg = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    out = format_stream_event(
        {
            "event": "on_chat_model_end",
            "name": "ChatModel",
            "task_id": "t1",
            "data": {"output": msg},
            "metadata": {"langgraph_node": "reviewer"},
        }
    )
    assert out is not None
    assert out["type"] == "usage"
    assert out["role"] == "reviewer"
    assert out["input_tokens"] == 10
    assert out["output_tokens"] == 5


def test_chat_model_end_without_usage_is_skipped() -> None:
    from langchain_core.messages import AIMessage

    out = format_stream_event(
        {
            "event": "on_chat_model_end",
            "name": "ChatModel",
            "task_id": "t1",
            "data": {"output": AIMessage(content="ok")},
            "metadata": {"langgraph_node": "reviewer"},
        }
    )
    assert out is None


# --- goal mode node-outcome events (PLAN.md §6.16) ---


def test_verification_completed_from_verify_node() -> None:
    from agent_room.schemas import VerificationResult

    out = format_stream_event(
        {
            "event": "on_chain_end",
            "name": "verify",
            "task_id": "t1",
            "data": {
                "output": {
                    "verification": VerificationResult(
                        passed=False, exit_code=1, output_tail="fail", round=2
                    )
                }
            },
        }
    )
    assert out == {
        "type": "verification_completed",
        "role": "verifier",
        "task_id": "t1",
        "passed": False,
        "exit_code": 1,
        "round": 2,
    }


def test_verify_passthrough_none_is_skipped() -> None:
    """No verify_command → verification is None → no SSE event."""
    out = format_stream_event(
        {"event": "on_chain_end", "name": "verify", "task_id": "t1", "data": {"output": {}}}
    )
    assert out is None


def test_supervisor_decided_event() -> None:
    from agent_room.schemas import StuckDecision

    out = format_stream_event(
        {
            "event": "on_chain_end",
            "name": "supervisor",
            "task_id": "t1",
            "data": {
                "output": {
                    "stuck_decision": StuckDecision(action="replan", reasoning="same error")
                }
            },
        }
    )
    assert out == {
        "type": "supervisor_decided",
        "role": "supervisor",
        "task_id": "t1",
        "action": "replan",
        "reasoning": "same error",
    }
