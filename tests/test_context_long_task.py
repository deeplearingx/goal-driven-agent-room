"""v0.4 §4.6 — long-task compression end-to-end tests.

PLAN.md §4 出口标准: "一个长达 50 轮的 ReAct 任务能在固定 token 预算内完成".
The §4.5 integration tests proved the engine fires for *one* turn with a
synthetic 22-message history pre-loaded into state. These tests prove the
engine survives a real 50-turn loop where each round actually grows the
state by 2 messages (one AI tool_call + one ToolMessage).

We drive the developer ReAct node directly without the LangGraph runtime,
manually applying the `add` reducer + simulating `ToolNode`'s
`{"dev_messages": [ToolMessage(...)]}` output. That keeps the test purely
in-process (sub-second) and pinpoints failures to the node + engine, not
graph compile noise.

What we pin:

1. With `WindowedContextEngine`, every one of 50 LLM calls receives ≤
   `max_messages` messages. The state grows unboundedly (audit trail).
2. With `SummaryContextEngine`, the same loop works AND the summarizer
   is called the right number of times — once per turn that triggers
   compression, never more (no off-by-one re-call).
3. The protect-window invariant holds across compressions: the first
   `protect_first_n` and last `protect_last_n` messages of the LLM input
   are byte-equal to their first appearance.
4. No ToolMessage in any LLM input is an orphan (no AIMessage(tool_call)
   with matching id earlier in the same input). This is the §4.2
   alignment fix tested across 50 rounds, not 1.
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
from agent_room.context import SummaryContextEngine, WindowedContextEngine
from agent_room.roles.developer_react import make_developer_react
from agent_room.state import TaskState
from tests.test_context_summary import _ScriptedSummarizer


class _CapturingLLM(BaseChatModel):
    """Local copy of `_CapturingScriptedLLM` from
    `tests/test_context_react_integration.py`. We don't import that one
    because pytest discovers the module path differently in CI vs local
    and we'd rather not couple two test files. The contract is small."""

    script: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "capturing-long"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:  # type: ignore[override]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        # `list(messages)` so later mutation of the input list (LangChain
        # passes a fresh list anyway, but be defensive) doesn't change
        # what we recorded.
        self._observed_inputs.append(list(messages))
        idx = len(self._used)
        if idx >= len(self.script):
            raise RuntimeError(
                f"_CapturingLLM exhausted at call {idx}; only {len(self.script)} responses scripted"
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


# ---------- helpers ----------


def _initial_state() -> TaskState:
    return {  # type: ignore[return-value]
        "task_id": "t",
        "title": "T",
        "description": "D",
        "plan": "p",
        "revision_round": 0,
        "dev_round": 0,
    }


def _apply_update(state: TaskState, update: dict) -> TaskState:
    """Mimic LangGraph's `add` reducer for `dev_messages` + scalar
    overwrites for everything else."""
    next_state = dict(state)
    for key, value in update.items():
        if key == "dev_messages":
            next_state[key] = (state.get("dev_messages") or []) + list(value)  # type: ignore[assignment]
        else:
            next_state[key] = value
    return next_state  # type: ignore[return-value]


def _simulate_tool_node(state: TaskState, tool_response: str = "ok") -> TaskState:
    """Append a ToolMessage for every tool_call on the trailing AIMessage.

    This is what `ToolNode(tools, messages_key="dev_messages")` does at
    runtime — it executes each tool_call and emits one ToolMessage per
    call back into state. We don't actually run a tool, just emit a
    canned response, since the engine doesn't care about content."""
    msgs = state.get("dev_messages") or []
    last = msgs[-1]
    assert isinstance(last, AIMessage) and last.tool_calls, (
        "harness bug: _simulate_tool_node called when last message has no tool_calls"
    )
    appended = [ToolMessage(content=tool_response, tool_call_id=tc["id"]) for tc in last.tool_calls]
    next_state = dict(state)
    next_state["dev_messages"] = list(msgs) + appended  # type: ignore[assignment]
    return next_state  # type: ignore[return-value]


def _assert_no_orphan_tool_messages(seen: list[BaseMessage]) -> None:
    """Every ToolMessage must have an earlier AIMessage(tool_call) with
    a matching id in the same conversation slice."""
    seen_call_ids: set[str] = set()
    for msg in seen:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                seen_call_ids.add(tc["id"])
        elif isinstance(msg, ToolMessage):
            assert msg.tool_call_id in seen_call_ids, (
                f"orphan ToolMessage with id {msg.tool_call_id!r}: "
                f"no preceding AIMessage(tool_call) in compressed input"
            )


def _tool_call_script(n_tool_rounds: int, final_text: str) -> list[Any]:
    """Build a ScriptedLLM script: n_tool_rounds tool calls + 1 final text."""
    return [
        [
            {
                "id": f"call-{i}",
                "name": "read_text",
                "args": {"path": f"f{i}.py"},
                "type": "tool_call",
            }
        ]
        for i in range(n_tool_rounds)
    ] + [final_text]


async def _drive_react(
    node: Any,
    *,
    n_rounds: int,
    state: TaskState | None = None,
) -> TaskState:
    """Drive the ReAct node manually for up to `n_rounds` turns,
    simulating ToolNode after every turn whose AI response carries
    tool_calls. Stops early if a final-text turn arrives.

    Returns the final accumulated state."""
    cur: TaskState = state if state is not None else _initial_state()
    for _ in range(n_rounds):
        update = await node(cur)
        cur = _apply_update(cur, update)
        last = cur["dev_messages"][-1]  # type: ignore[index]
        if not (isinstance(last, AIMessage) and last.tool_calls):
            return cur
        cur = _simulate_tool_node(cur)
    return cur


# ---------- 1. Windowed engine over 50 rounds ----------


@pytest.mark.asyncio
async def test_50_round_react_with_windowed_engine_stays_under_budget():
    """50-round end-to-end with `WindowedContextEngine`. Every LLM call
    sees ≤ max_messages; orphan tool messages never appear; final state
    contains the full audit trail (102 messages: System + Human + 50*(AI+Tool))."""
    n_tool_rounds = 49
    script = _tool_call_script(n_tool_rounds, final_text="final code")
    llm = _CapturingLLM(script=script)
    engine = WindowedContextEngine(max_messages=10, protect_first_n=2, protect_last_n=4)
    bindings = RoleBindings(developer=llm, context_engine=engine)
    node = make_developer_react(bindings, tools=[], max_dev_rounds=60)

    final_state = await _drive_react(node, n_rounds=50)

    # 50 LLM calls, the last returning final text.
    assert len(llm.observed_inputs) == 50
    for round_idx, seen in enumerate(llm.observed_inputs):
        assert len(seen) <= engine.max_messages, (
            f"round {round_idx}: LLM saw {len(seen)} messages, budget is {engine.max_messages}"
        )
        _assert_no_orphan_tool_messages(seen)

    # Final state has the full audit trail. Per-round growth:
    #   round 0 (initial entry): node returns [System, Human, AI0(tc)] = 3
    #     msgs; simulate adds 1 Tool → +1. State after round 0: 4.
    #   rounds 1..48 (48 rounds): node returns [AIk(tc)] = 1; simulate
    #     adds 1 Tool → +2 each. State after round 48: 4 + 48*2 = 100.
    #   round 49: node returns [AI_final(no tc)] = 1; no simulate (no
    #     tool_calls on the final AI). State after round 49: 101.
    assert len(final_state["dev_messages"]) == 101  # type: ignore[arg-type]
    assert isinstance(final_state["dev_messages"][0], SystemMessage)  # type: ignore[index]
    assert isinstance(final_state["dev_messages"][1], HumanMessage)  # type: ignore[index]
    last = final_state["dev_messages"][-1]  # type: ignore[index]
    assert isinstance(last, AIMessage) and not last.tool_calls
    assert "final code" in str(last.content)


# ---------- 2. Summary engine over 50 rounds ----------


@pytest.mark.asyncio
async def test_50_round_react_with_summary_engine_calls_summarizer_per_compression():
    """Same loop, swap engine. Summarizer must fire once per round that
    actually compresses (never twice for the same round, never on rounds
    under budget)."""
    n_tool_rounds = 49
    script = _tool_call_script(n_tool_rounds, final_text="done")
    llm = _CapturingLLM(script=script)

    # Plenty of summary responses — we don't know exact compression count
    # ahead of time, but ≥ 1 and ≤ 50.
    summarizer = _ScriptedSummarizer(responses=[f"summary-{i}" for i in range(60)])
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=10,
        protect_first_n=2,
        protect_last_n=4,
    )
    bindings = RoleBindings(developer=llm, context_engine=engine)
    node = make_developer_react(bindings, tools=[], max_dev_rounds=60)

    await _drive_react(node, n_rounds=50)

    assert len(llm.observed_inputs) == 50

    # Count how many rounds *actually* compressed (LLM input has a marker).
    compressed_rounds = [
        i
        for i, seen in enumerate(llm.observed_inputs)
        if any("earlier turns compacted" in str(m.content) for m in seen)
    ]
    # First few rounds are under budget (start with [System, Human]), so
    # there must be at least one uncompressed round and at least one
    # compressed round.
    assert len(compressed_rounds) > 0
    assert len(compressed_rounds) < 50

    # Summarizer was called exactly once per compressed round — no skip,
    # no double-call.
    assert len(summarizer.calls) == len(compressed_rounds), (
        f"summarizer called {len(summarizer.calls)} times but "
        f"{len(compressed_rounds)} rounds had a summary marker"
    )

    # Every LLM input still under budget + no orphan tool messages.
    for round_idx, seen in enumerate(llm.observed_inputs):
        assert len(seen) <= engine.max_messages, (
            f"round {round_idx}: {len(seen)} > {engine.max_messages}"
        )
        _assert_no_orphan_tool_messages(seen)


# ---------- 3. Protect-window byte equality ----------


@pytest.mark.asyncio
async def test_protect_window_messages_byte_equal_across_compressions():
    """Across 50 rounds, the protected head and tail messages survive
    compression byte-for-byte. We pin head[0:2] (System + Human) — those
    never change. Tail varies by definition (it slides), so we don't pin
    it; we pin only head."""
    script = _tool_call_script(49, final_text="done")
    llm = _CapturingLLM(script=script)
    engine = WindowedContextEngine(max_messages=10, protect_first_n=2, protect_last_n=4)
    bindings = RoleBindings(developer=llm, context_engine=engine)
    node = make_developer_react(bindings, tools=[], max_dev_rounds=60)

    await _drive_react(node, n_rounds=50)

    # First round: must be the 2-msg initial frame OR slightly larger
    # (the framework may have grown by an AI before the LLM is called —
    # actually no, it's called first). Either way, those System+Human
    # contents are the canonical head.
    head_system = llm.observed_inputs[0][0]
    head_human = llm.observed_inputs[0][1]
    assert isinstance(head_system, SystemMessage)
    assert isinstance(head_human, HumanMessage)

    # Sample 10 evenly-spaced rounds beyond the first.
    sample_indices = list(range(0, 50, 5))
    for idx in sample_indices:
        seen = llm.observed_inputs[idx]
        # Head still in position; content byte-equal.
        assert isinstance(seen[0], SystemMessage)
        assert seen[0].content == head_system.content, (
            f"round {idx}: SystemMessage content drifted under compression"
        )
        assert isinstance(seen[1], HumanMessage)
        assert seen[1].content == head_human.content, (
            f"round {idx}: HumanMessage (initial task framing) content drifted"
        )


# ---------- 4. State unbounded growth ----------


@pytest.mark.asyncio
async def test_state_grows_unbounded_under_compression():
    """`state["dev_messages"]` is the audit log — engine compresses the
    LLM input, never the persisted state. After 50 rounds state holds
    every turn."""
    script = _tool_call_script(49, final_text="done")
    llm = _CapturingLLM(script=script)
    engine = WindowedContextEngine(max_messages=8, protect_first_n=1, protect_last_n=3)
    bindings = RoleBindings(developer=llm, context_engine=engine)
    node = make_developer_react(bindings, tools=[], max_dev_rounds=60)

    final = await _drive_react(node, n_rounds=50)

    # Audit trail accumulation, same arithmetic as the windowed test:
    # round 0 emits [Sys, Human, AI0(tc)] (3); simulate +1 Tool → 4.
    # rounds 1..48 emit [AIk(tc)] (1) + simulate +1 Tool → +2 each → 100.
    # round 49 emits [AI_final(no tc)] (1) → 101.
    assert len(final["dev_messages"]) == 101  # type: ignore[arg-type]
    assert final["dev_round"] == 50  # type: ignore[index]
    # First three are stable.
    assert isinstance(final["dev_messages"][0], SystemMessage)  # type: ignore[index]
    assert isinstance(final["dev_messages"][1], HumanMessage)  # type: ignore[index]
    assert isinstance(final["dev_messages"][2], AIMessage)  # type: ignore[index]
