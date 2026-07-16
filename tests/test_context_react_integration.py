"""v0.4 §4.5 — ContextEngine wired into the developer ReAct loop.

Integration tests: confirms that

1. With the default `NoOpContextEngine`, behaviour matches v0.3 exactly —
   the LLM receives the full conversation each turn (covered implicitly by
   `tests/test_developer_react.py` continuing to pass; this file pins it
   explicitly via the `_observed_inputs` capture).
2. With `WindowedContextEngine`, the LLM-input list is bounded each turn,
   while persisted `state["dev_messages"]` keeps growing (audit trail
   intact). This is the load-bearing invariant of §4.5: compress the
   prompt, not the state.
3. The boundary alignment fix from §4.2 holds end-to-end: a long ReAct
   conversation can be compressed mid-loop without producing an illegal
   tool_call/tool_result split.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

from agent_room.config import RoleBindings
from agent_room.context import NoOpContextEngine, WindowedContextEngine
from agent_room.roles.developer_react import make_developer_react


class _CapturingScriptedLLM(BaseChatModel):
    """Scripted LLM that records the messages passed to each invocation.

    `observed_inputs` collects the full message list the engine handed to
    `ainvoke` per call — that's the LLM-input we want to assert is bounded.
    """

    script: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "capturing-scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:  # type: ignore[override]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._observed_inputs.append(list(messages))
        idx = len(self._used)
        if idx >= len(self.script):
            raise RuntimeError(
                f"capturing-scripted exhausted at call {idx}; "
                f"only {len(self.script)} responses scripted"
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
        object.__setattr__(self, "_observed_inputs", [])

    @property
    def observed_inputs(self) -> list[list[BaseMessage]]:
        return self._observed_inputs


def _build_long_react_history(n_rounds: int) -> list[BaseMessage]:
    """Synthesize a plausible mid-ReAct dev_messages list.

    Shape matches what `developer_react` + `ToolNode` produce after
    `n_rounds` of tool-calling: [System, Human, AI(tool_call), Tool, ...].
    """
    out: list[BaseMessage] = [
        SystemMessage(content="dev system prompt"),
        HumanMessage(content="# Plan ...\n# Task T"),
    ]
    for i in range(n_rounds):
        out.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "read_text", "args": {"path": f"f{i}.py"}, "id": f"call-{i}"}],
            )
        )
        out.append(ToolMessage(content=f"contents of f{i}", tool_call_id=f"call-{i}"))
    return out


# ---------- 1. NoOp default: behaviour unchanged ----------


@pytest.mark.asyncio
async def test_noop_default_passes_full_conversation_to_llm():
    """Default `NoOpContextEngine` → LLM sees every prior turn."""
    history = _build_long_react_history(n_rounds=10)  # 22 messages
    llm = _CapturingScriptedLLM(script=["final code"])
    bindings = RoleBindings(developer=llm)  # no context_engine override = NoOp
    assert isinstance(bindings.context_engine, NoOpContextEngine)

    node = make_developer_react(bindings, tools=[], max_dev_rounds=20)
    state = {
        "task_id": "t",
        "title": "T",
        "description": "D",
        "plan": "p",
        "revision_round": 0,
        "dev_round": 5,
        "dev_messages": history,
    }
    await node(state)

    assert len(llm.observed_inputs) == 1
    seen = llm.observed_inputs[0]
    assert len(seen) == len(history)
    assert seen[0] is history[0]  # System
    assert seen[-1] is history[-1]  # last ToolMessage


# ---------- 2. Windowed: LLM-input bounded; state grows ----------


@pytest.mark.asyncio
async def test_windowed_engine_bounds_llm_input_but_persists_full_state():
    """The load-bearing §4.5 invariant: compress prompt, not state.

    Persisted `dev_messages` returned in the update is the *increment*
    only (one new AIMessage), per LangGraph reducer semantics. Combined
    with the existing 22-message history under `add`, the next round's
    state sees 23 messages — full audit trail intact, even though this
    round's LLM only saw `max_messages` items.
    """
    history = _build_long_react_history(n_rounds=10)  # 22 messages
    llm = _CapturingScriptedLLM(script=["final code"])
    bindings = RoleBindings(
        developer=llm,
        context_engine=WindowedContextEngine(max_messages=8, protect_first_n=2, protect_last_n=4),
    )

    node = make_developer_react(bindings, tools=[], max_dev_rounds=20)
    state = {
        "task_id": "t",
        "title": "T",
        "description": "D",
        "plan": "p",
        "revision_round": 0,
        "dev_round": 5,
        "dev_messages": history,
    }
    update = await node(state)

    # LLM saw a bounded list — protect_first(2) + marker(1) + protect_last(4),
    # minus any orphan tool messages alignment dropped from the tail front.
    seen = llm.observed_inputs[0]
    assert len(seen) <= 7  # 2 + 1 + 4 ceiling, possibly less after alignment
    # Head still starts with the System+Human framing.
    assert isinstance(seen[0], SystemMessage)
    assert isinstance(seen[1], HumanMessage)
    # A truncation marker sits between head and tail.
    markers = [m for m in seen if "messages omitted" in str(m.content)]
    assert len(markers) == 1
    # Tail ends with the most recent ToolMessage (the LLM gets the latest signal).
    assert isinstance(seen[-1], ToolMessage)
    assert seen[-1].tool_call_id == "call-9"

    # Persisted state — the update we return adds only the new AIMessage;
    # LangGraph's `add` reducer combines it with the 22-message history
    # already in state. We verify by inspecting the increment shape.
    increment = update["dev_messages"]
    assert len(increment) == 1
    assert isinstance(increment[0], AIMessage)
    assert increment[0].content == "final code"
    # Existing state untouched (we never mutate it).
    assert len(history) == 22


@pytest.mark.asyncio
async def test_windowed_engine_no_orphan_tool_in_llm_input():
    """Compressed LLM-input must never start the tail with an orphan ToolMessage.

    With protect_last_n=3 on a 22-msg history that ends ...AI, Tool, AI, Tool,
    naive slicing yields tail=[Tool, AI(tool_call), Tool] — illegal. The
    alignment fix drops the leading orphan.
    """
    history = _build_long_react_history(n_rounds=10)  # ends with [..., AI, Tool]
    llm = _CapturingScriptedLLM(script=["done"])
    bindings = RoleBindings(
        developer=llm,
        context_engine=WindowedContextEngine(max_messages=6, protect_first_n=1, protect_last_n=3),
    )
    node = make_developer_react(bindings, tools=[], max_dev_rounds=20)

    await node(
        {
            "task_id": "t",
            "title": "T",
            "description": "D",
            "plan": "p",
            "revision_round": 0,
            "dev_round": 5,
            "dev_messages": history,
        }
    )

    seen = llm.observed_inputs[0]
    # Walk: every ToolMessage must be immediately preceded by an AIMessage
    # carrying a tool_call with the matching id.
    pending: dict[str, bool] = {}
    for msg in seen:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                pending[tc["id"]] = True
        elif isinstance(msg, ToolMessage):
            assert msg.tool_call_id in pending, (
                f"orphan ToolMessage with id {msg.tool_call_id!r} in compressed input"
            )


# ---------- 3. Stateless / re-entry path picks up engine ----------


@pytest.mark.asyncio
async def test_initial_entry_compresses_only_when_history_exceeds_budget():
    """First entry builds a 2-message [System, Human] convo. Way under any
    sane budget — compression must be a no-op even with the engine bound."""
    llm = _CapturingScriptedLLM(script=["initial answer"])
    bindings = RoleBindings(
        developer=llm,
        context_engine=WindowedContextEngine(max_messages=10, protect_first_n=1, protect_last_n=4),
    )
    node = make_developer_react(bindings, tools=[], max_dev_rounds=5)

    state = {
        "task_id": "t",
        "title": "T",
        "description": "D",
        "plan": "p",
        "revision_round": 0,
    }
    await node(state)

    seen = llm.observed_inputs[0]
    assert len(seen) == 2  # [System, Human]
    assert isinstance(seen[0], SystemMessage)
    assert isinstance(seen[1], HumanMessage)
    # No marker — under budget, no compression happened.
    assert not any("messages omitted" in str(m.content) for m in seen)
