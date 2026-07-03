"""v0.4 §4.8 — context compression PoC, runs offline.

A toy ReAct loop driven by scripted fakes so you can watch the three
context engines behave side-by-side without burning provider credits.
The point isn't realism — it's giving you a turn-by-turn view of what
each engine actually hands the LLM.

Run:
    python -m examples.context_compression

Output is a table per engine: round, persisted state size, LLM input size,
"compressed?" yes/no. With the synthetic 30-round loop you should see:

    NoOpContextEngine        : LLM input == state size, every round
    WindowedContextEngine    : LLM input plateaus at max_messages
    SummaryContextEngine     : same plateau, with summary marker text

Plus a §3.4 spill demo: same SummaryContextEngine, two passes, second
one wired with an `InMemorySpillStore`. Summarizer prompt shrinks ~3x
in this synthetic demo (~24 KB → ~8 KB) because each big `ToolMessage`
body becomes a content-hashed pointer + short preview. Same content
hashes to the same ref, so 25 identical payloads dedupe to 1 store entry.

No external services. No creds. Sub-second.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

from agent_room.config import RoleBindings
from agent_room.context import (
    ContextEngine,
    NoOpContextEngine,
    SummaryContextEngine,
    WindowedContextEngine,
)
from agent_room.roles.developer_react import make_developer_react
from agent_room.tools.spill import InMemorySpillStore

N_ROUNDS = 30


class _ScriptedDevLLM(BaseChatModel):
    """Returns N_ROUNDS-1 tool_calls then a final-text. Records each
    `ainvoke` input list so we can show what the engine actually produced."""

    @property
    def _llm_type(self) -> str:
        return "context-compression-poc-dev"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:  # type: ignore[override]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._observed.append(list(messages))
        idx = len(self._observed) - 1
        if idx < N_ROUNDS - 1:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_text",
                        "args": {"path": f"src/m{idx}.py"},
                        "id": f"call-{idx}",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            msg = AIMessage(content="```python\nprint('done')\n```")
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
        object.__setattr__(self, "_observed", [])

    @property
    def observed(self) -> list[list[BaseMessage]]:
        return self._observed


class _ScriptedSummaryLLM(BaseChatModel):
    """Returns deterministic short summaries so the SummaryEngine path is
    visible without real LLM cost."""

    @property
    def _llm_type(self) -> str:
        return "context-compression-poc-summary"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Each call gets a unique tag so we can confirm fresh marker per round.
        self._calls += 1
        text = f"[summary #{self._calls}] developer scanned several modules"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def model_post_init(self, __ctx: Any) -> None:
        object.__setattr__(self, "_calls", 0)


class _RecordingSummaryLLM(BaseChatModel):
    """Same as `_ScriptedSummaryLLM` but records every prompt's serialized
    size. Lets the spill demo show how much smaller the summarizer's input
    becomes when big tool outputs go through `InMemorySpillStore`."""

    @property
    def _llm_type(self) -> str:
        return "context-compression-poc-recording-summary"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        # The serialized middle is the second message (HumanMessage).
        body = str(messages[1].content) if len(messages) >= 2 else ""
        self._prompt_sizes.append(len(body))
        text = "developer scanned several modules"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def model_post_init(self, __ctx: Any) -> None:
        object.__setattr__(self, "_prompt_sizes", [])

    @property
    def prompt_sizes(self) -> list[int]:
        return self._prompt_sizes


class _BigToolDevLLM(BaseChatModel):
    """Variant of `_ScriptedDevLLM` that emits the same tool_call sequence
    but the simulated tool-output ABOVE the spill threshold. Drives the
    §3.4 demo — pair with `_simulate_big_tool_node` below."""

    @property
    def _llm_type(self) -> str:
        return "context-compression-poc-big-tool-dev"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:  # type: ignore[override]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._observed.append(list(messages))
        idx = len(self._observed) - 1
        if idx < N_ROUNDS - 1:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "shell",
                        "args": {"cmd": f"pytest tests/m{idx}.py -v"},
                        "id": f"call-{idx}",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            msg = AIMessage(content="```python\nprint('done')\n```")
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
        object.__setattr__(self, "_observed", [])

    @property
    def observed(self) -> list[list[BaseMessage]]:
        return self._observed


_BIG_TOOL_PAYLOAD = (
    "============================= test session starts =============================\n"
    + ("FAILED tests/foo.py::test_bar - AssertionError: 1 != 2\n" * 200)
    + "============================= 200 failed in 12.34s ============================\n"
)


async def _drive_one_engine(label: str, engine: ContextEngine) -> None:
    """Run the same N_ROUNDS loop with the given engine, print one line per round."""
    llm = _ScriptedDevLLM()
    bindings = RoleBindings(developer=llm, context_engine=engine)
    node = make_developer_react(bindings, tools=[], max_dev_rounds=N_ROUNDS + 5)

    state: dict = {
        "task_id": "ctx-poc",
        "title": "context-compression demo",
        "description": "synthetic 30-round ReAct loop",
        "plan": "1. inspect modules 2. report findings",
        "revision_round": 0,
        "dev_round": 0,
    }

    print(f"\n=== {label} ===")
    print(f"{'round':>5}  {'state_msgs':>10}  {'llm_input':>9}  compressed?")

    for round_no in range(N_ROUNDS):
        update = await node(state)
        # Apply increment under `add` reducer semantics.
        state = dict(state)
        state["dev_messages"] = (state.get("dev_messages") or []) + list(update["dev_messages"])
        state["dev_round"] = update["dev_round"]

        seen = llm.observed[round_no]
        compressed = any(
            "messages omitted" in str(m.content) or "earlier turns compacted" in str(m.content)
            for m in seen
        )
        print(
            f"{round_no:>5}  {len(state['dev_messages']):>10}  "
            f"{len(seen):>9}  {'YES' if compressed else 'no'}"
        )

        last = state["dev_messages"][-1]
        if not (isinstance(last, AIMessage) and last.tool_calls):
            break  # final-text turn

        # Simulate ToolNode: append one ToolMessage per tool_call.
        appended = [
            ToolMessage(content=f"<contents of {tc['args']['path']}>", tool_call_id=tc["id"])
            for tc in last.tool_calls
        ]
        state["dev_messages"] = state["dev_messages"] + appended

    # Show the head + tail of the final LLM input so you can see protect-window in action.
    final_seen = llm.observed[-1]
    print(f"  final LLM input ({len(final_seen)} msgs):")
    for i, m in enumerate(final_seen):
        kind = type(m).__name__
        body = str(m.content)
        if len(body) > 70:
            body = body[:67] + "..."
        body = body.replace("\n", " ⏎ ")
        print(f"    [{i:>2}] {kind:<15} {body}")


async def _spill_demo() -> None:
    """v0.4 §3.4 — same engine, two passes, second one wired with a SpillStore.

    Both passes drive the same 30-round loop where every ToolMessage is
    a ~10 KB simulated `pytest -v` failure dump. We watch the summarizer's
    prompt size shrink when spill is on.
    """
    print("\n=== §3.4 spill demo: SummaryContextEngine, with vs. without store ===")
    print(f"  ({len(_BIG_TOOL_PAYLOAD)}-char tool outputs, threshold=4000)\n")

    no_spill = await _run_one_spill_pass(label="no spill_store", spill_store=None)
    with_spill = await _run_one_spill_pass(
        label="spill_store=InMemorySpillStore()",
        spill_store=InMemorySpillStore(),
    )

    print(f"\n  summarizer compressions:  {len(no_spill):>3}  vs  {len(with_spill):>3}")
    if no_spill and with_spill:
        avg_no = sum(no_spill) // len(no_spill)
        avg_yes = sum(with_spill) // len(with_spill)
        ratio = avg_no / max(avg_yes, 1)
        print(
            f"  avg summarizer prompt:    {avg_no:>5} chars  vs  {avg_yes:>5} chars  ({ratio:.1f}x)"
        )
        print(
            f"  max summarizer prompt:    {max(no_spill):>5} chars  vs  {max(with_spill):>5} chars"
        )


async def _run_one_spill_pass(*, label: str, spill_store: InMemorySpillStore | None) -> list[int]:
    """One driven pass; returns the recorded summarizer prompt sizes."""
    summarizer = _RecordingSummaryLLM()
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=10,
        protect_first_n=2,
        protect_last_n=4,
        spill_store=spill_store,
    )
    llm = _BigToolDevLLM()
    bindings = RoleBindings(developer=llm, context_engine=engine)
    node = make_developer_react(bindings, tools=[], max_dev_rounds=N_ROUNDS + 5)

    state: dict = {
        "task_id": "ctx-spill",
        "title": "spill demo",
        "description": "synthetic 30-round ReAct with 10KB tool outputs",
        "plan": "1. shell out 2. inspect 3. report",
        "revision_round": 0,
        "dev_round": 0,
    }

    for _ in range(N_ROUNDS):
        update = await node(state)
        state = dict(state)
        state["dev_messages"] = (state.get("dev_messages") or []) + list(update["dev_messages"])
        state["dev_round"] = update["dev_round"]

        last = state["dev_messages"][-1]
        if not (isinstance(last, AIMessage) and last.tool_calls):
            break

        appended = [
            ToolMessage(content=_BIG_TOOL_PAYLOAD, tool_call_id=tc["id"]) for tc in last.tool_calls
        ]
        state["dev_messages"] = state["dev_messages"] + appended

    sizes = summarizer.prompt_sizes
    store_size = len(spill_store) if spill_store is not None else 0
    print(
        f"  {label:<40} compressions={len(sizes):>2}  "
        f"avg_prompt={(sum(sizes) // max(len(sizes), 1)):>5}c  "
        f"store_entries={store_size}"
    )
    return sizes


async def main() -> int:
    print(
        f"Running a synthetic {N_ROUNDS}-round ReAct loop through three engines.\n"
        "Watch how `llm_input` grows under NoOp but plateaus under Windowed/Summary."
    )

    await _drive_one_engine("NoOpContextEngine (v0.3 default)", NoOpContextEngine())

    await _drive_one_engine(
        "WindowedContextEngine(max=10, protect_first=2, protect_last=4)",
        WindowedContextEngine(max_messages=10, protect_first_n=2, protect_last_n=4),
    )

    await _drive_one_engine(
        "SummaryContextEngine(max=10, protect_first=2, protect_last=4)",
        SummaryContextEngine(
            summarizer=_ScriptedSummaryLLM(),
            max_messages=10,
            protect_first_n=2,
            protect_last_n=4,
        ),
    )

    await _spill_demo()

    print(
        "\nKey observations:\n"
        "  * state_msgs is the persisted audit trail — grows monotonically under all engines.\n"
        "  * llm_input is what the LLM actually sees this turn.\n"
        "  * NoOp re-sends everything (token cost scales O(rounds²)).\n"
        "  * Windowed plateaus at max_messages; middle replaced by '[messages omitted]'.\n"
        "  * Summary plateaus too; middle replaced by an LLM-generated digest.\n"
        "  * Spill (§3.4) shrinks the summarizer prompt ~3x in this demo (raw\n"
        "    payload is per-message 1500-char-capped already; spill replaces\n"
        "    each big body with a 250-char placeholder). Bigger absolute\n"
        "    payloads → bigger ratio. Content-hashed store dedupes identical\n"
        "    bodies — `store_entries=1` for 25 identical tool outputs.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
