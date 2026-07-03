"""Tests for the LLM transport seam (ADR-0013).

What's pinned here:
  - `LangChainTransport.invoke` returns a `NormalizedResponse` whose
    `.message` is the raw `AIMessage` so existing role nodes that read
    `.tool_calls` / `.content` keep working.
  - `LangChainTransport.structured` wraps `with_structured_output` in
    `retry_on_parser_error` automatically, eliminating the three
    duplicated wraps in role files.
  - `tools=...` triggers `bind_tools` exactly once.
  - `model_override` flows through to `bindings.resolve`.
"""

from __future__ import annotations

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from agent_room.budget import Budget, BudgetExceededError, BudgetTracker
from agent_room.config import RoleBindings
from agent_room.llm import LangChainTransport, NormalizedResponse
from agent_room.schemas import ReviewerDecision
from tests.fakes import FakeListChatModel, FakeReviewerLLM, FakeUsageChatModel


class _Schema(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_invoke_returns_normalized_response_with_raw_message() -> None:
    bindings = RoleBindings(
        planner=FakeListChatModel(responses=["hello world"]),
    )
    tx = LangChainTransport(bindings)
    response = await tx.invoke("planner", [HumanMessage(content="hi")])
    assert isinstance(response, NormalizedResponse)
    assert isinstance(response.message, AIMessage)
    assert response.message.content == "hello world"


@pytest.mark.asyncio
async def test_structured_returns_schema_instance_via_with_retry() -> None:
    bindings = RoleBindings(
        reviewer=FakeReviewerLLM(
            responses=["ignored"],
            decisions=[
                ReviewerDecision(decision="approved", feedback="ok", confidence=0.9),
            ],
        )
    )
    tx = LangChainTransport(bindings)
    out = await tx.structured("reviewer", ReviewerDecision, [HumanMessage(content="x")])
    assert isinstance(out, ReviewerDecision)
    assert out.decision == "approved"


@pytest.mark.asyncio
async def test_structured_retries_on_parser_error() -> None:
    """A first-attempt OutputParserException must be retried, not propagated."""

    class FlakyLLM(FakeListChatModel):
        _calls: int = 0

        def with_structured_output(self, schema, **_kwargs):  # type: ignore[override]
            outer = self

            class _Stub:
                async def ainvoke(self, _msgs, **_kw):  # type: ignore[no-untyped-def]
                    outer._calls += 1  # noqa: SLF001
                    if outer._calls == 1:
                        raise OutputParserException("Unknown tool type: ''")
                    return _Schema(answer="resolved")

                def with_retry(self, **kwargs):  # type: ignore[no-untyped-def]
                    from langchain_core.runnables.base import RunnableLambda
                    from langchain_core.runnables.retry import RunnableRetry

                    async def call(msgs):  # type: ignore[no-untyped-def]
                        return await self.ainvoke(msgs)

                    return RunnableRetry(bound=RunnableLambda(call), **kwargs)

            return _Stub()

    bindings = RoleBindings(planner=FlakyLLM(responses=["unused"]))
    tx = LangChainTransport(bindings)
    out = await tx.structured("planner", _Schema, [HumanMessage(content="x")], attempts=2)
    assert isinstance(out, _Schema)
    assert out.answer == "resolved"


_REVIEW_JSON = '{"decision": "approved", "feedback": "ok", "issues": [], "confidence": 0.9}'


def _raising_structured_llm(exc: Exception, *, json_response: str) -> FakeListChatModel:
    """A fake whose `with_structured_output` always fails on invoke, but whose
    plain `ainvoke` returns JSON — exercising the JSON-prompt fallback."""

    class _LLM(FakeListChatModel):
        def with_structured_output(self, schema, **_kwargs):  # type: ignore[override]
            class _Stub:
                async def ainvoke(self, _msgs, **_kw):  # type: ignore[no-untyped-def]
                    raise exc

                def with_retry(self, **kwargs):  # type: ignore[no-untyped-def]
                    from langchain_core.runnables.base import RunnableLambda
                    from langchain_core.runnables.retry import RunnableRetry

                    async def call(msgs):  # type: ignore[no-untyped-def]
                        return await self.ainvoke(msgs)

                    return RunnableRetry(bound=RunnableLambda(call), **kwargs)

            return _Stub()

    return _LLM(responses=[json_response])


@pytest.mark.asyncio
async def test_structured_falls_back_on_thinking_mode_tool_choice() -> None:
    """A thinking-mode tool_choice rejection must fall back to JSON-prompt parsing."""

    llm = _raising_structured_llm(
        ValueError("Thinking mode does not support this tool_choice"),
        json_response=_REVIEW_JSON,
    )
    tx = LangChainTransport(RoleBindings(reviewer=llm))
    out = await tx.structured("reviewer", ReviewerDecision, [HumanMessage(content="x")])
    assert isinstance(out, ReviewerDecision)
    assert out.decision == "approved"


@pytest.mark.asyncio
async def test_structured_falls_back_when_model_returns_prose() -> None:
    """When structured output yields prose (OutputParserException), fall back."""

    llm = _raising_structured_llm(
        OutputParserException("Invalid json output: Decision: Approved..."),
        json_response=_REVIEW_JSON,
    )
    tx = LangChainTransport(RoleBindings(reviewer=llm))
    out = await tx.structured("reviewer", ReviewerDecision, [HumanMessage(content="x")], attempts=2)
    assert out.decision == "approved"


@pytest.mark.asyncio
async def test_structured_falls_back_when_primary_returns_none() -> None:
    """`with_structured_output` can yield None (no tool call) without raising;
    that must trigger the fallback, never propagate None to `.model_dump()`."""

    class _NoneLLM(FakeListChatModel):
        def with_structured_output(self, schema, **_kwargs):  # type: ignore[override]
            class _Stub:
                async def ainvoke(self, _msgs, **_kw):  # type: ignore[no-untyped-def]
                    return None

                def with_retry(self, **kwargs):  # type: ignore[no-untyped-def]
                    from langchain_core.runnables.base import RunnableLambda
                    from langchain_core.runnables.retry import RunnableRetry

                    async def call(msgs):  # type: ignore[no-untyped-def]
                        return await self.ainvoke(msgs)

                    return RunnableRetry(bound=RunnableLambda(call), **kwargs)

            return _Stub()

    llm = _NoneLLM(responses=[_REVIEW_JSON])
    tx = LangChainTransport(RoleBindings(reviewer=llm))
    out = await tx.structured("reviewer", ReviewerDecision, [HumanMessage(content="x")])
    assert isinstance(out, ReviewerDecision)
    assert out.decision == "approved"


@pytest.mark.asyncio
async def test_structured_caches_fallback_and_skips_doomed_primary() -> None:
    """After a model rejects structured output once, later calls must skip the
    doomed `with_structured_output` primary and go straight to the fallback."""

    class _CountingLLM(FakeListChatModel):
        wso_calls: int = 0

        def with_structured_output(self, schema, **_kwargs):  # type: ignore[override]
            self.wso_calls += 1

            class _Stub:
                async def ainvoke(self, _msgs, **_kw):  # type: ignore[no-untyped-def]
                    raise ValueError("Thinking mode does not support this tool_choice")

                def with_retry(self, **kwargs):  # type: ignore[no-untyped-def]
                    from langchain_core.runnables.base import RunnableLambda
                    from langchain_core.runnables.retry import RunnableRetry

                    async def call(msgs):  # type: ignore[no-untyped-def]
                        return await self.ainvoke(msgs)

                    return RunnableRetry(bound=RunnableLambda(call), **kwargs)

            return _Stub()

    llm = _CountingLLM(responses=[_REVIEW_JSON, _REVIEW_JSON])
    tx = LangChainTransport(RoleBindings(reviewer=llm))
    msgs = [HumanMessage(content="x")]

    first = await tx.structured("reviewer", ReviewerDecision, msgs)
    second = await tx.structured("reviewer", ReviewerDecision, msgs)

    assert first.decision == "approved"
    assert second.decision == "approved"
    assert llm.wso_calls == 1  # primary tried once, then cached out


@pytest.mark.asyncio
async def test_structured_reraises_genuine_error_without_fallback() -> None:
    """A real error (network/auth) must propagate, not trigger the fallback."""

    llm = _raising_structured_llm(ValueError("Connection refused"), json_response=_REVIEW_JSON)
    tx = LangChainTransport(RoleBindings(reviewer=llm))
    with pytest.raises(ValueError, match="Connection refused"):
        await tx.structured("reviewer", ReviewerDecision, [HumanMessage(content="x")])


@pytest.mark.asyncio
async def test_invoke_with_tools_calls_bind_tools_once() -> None:
    """Tools must reach the model via `bind_tools`; identity check via spy."""

    bound: list[object] = []

    class SpyLLM(FakeListChatModel):
        def bind_tools(self, tools, **_kwargs):  # type: ignore[override]
            bound.append(list(tools))
            return self  # plain FakeListChatModel returns the same shape

    bindings = RoleBindings(developer=SpyLLM(responses=["done"]))
    tx = LangChainTransport(bindings)

    fake_tool = object()
    response = await tx.invoke(
        "developer",
        [HumanMessage(content="x")],
        tools=[fake_tool],  # type: ignore[list-item]
    )
    assert response.message.content == "done"
    assert bound == [[fake_tool]]


@pytest.mark.asyncio
async def test_model_override_flows_to_bindings_resolve() -> None:
    """`model_override` must be forwarded so `NodeSpec.model` keeps working."""

    seen: list[str | None] = []

    class SpyBindings(RoleBindings):
        def resolve(self, role, *, model_override=None):  # type: ignore[override]
            seen.append(model_override)
            return super().resolve(role, model_override=model_override)

    bindings = SpyBindings(planner=FakeListChatModel(responses=["x"]))
    tx = LangChainTransport(bindings)
    await tx.invoke("planner", [HumanMessage(content="x")], model_override="my-model")
    assert seen == ["my-model"]


@pytest.mark.asyncio
async def test_no_tools_means_no_bind_tools_call() -> None:
    """Default path must not call `bind_tools` at all (preserves prefix-cache)."""

    class TrackingLLM(FakeListChatModel):
        bind_calls: int = 0

        def bind_tools(self, tools, **_kwargs):  # type: ignore[override]
            self.bind_calls += 1
            return self

    llm = TrackingLLM(responses=["ok"])
    bindings = RoleBindings(planner=llm)
    tx = LangChainTransport(bindings)
    await tx.invoke("planner", [HumanMessage(content="x")])
    assert llm.bind_calls == 0


@pytest.mark.asyncio
async def test_invoke_without_budget_never_raises() -> None:
    """Default (`budget=None`) is a pure NoOp — the existing behavior every
    other transport test above relies on stays exactly as-is."""
    bindings = RoleBindings(planner=FakeUsageChatModel(input_tokens=10_000, output_tokens=10_000))
    tx = LangChainTransport(bindings)
    response = await tx.invoke("planner", [HumanMessage(content="x")])
    assert response.usage is not None and response.usage.total_tokens == 20_000


@pytest.mark.asyncio
async def test_invoke_records_usage_into_shared_tracker() -> None:
    tracker = BudgetTracker(Budget(max_tokens=1000))
    bindings = RoleBindings(planner=FakeUsageChatModel(input_tokens=100, output_tokens=50))
    tx = LangChainTransport(bindings, budget=tracker)
    await tx.invoke("planner", [HumanMessage(content="x")])
    assert tracker.total_tokens == 150


@pytest.mark.asyncio
async def test_invoke_raises_budget_exceeded_when_tokens_cross_ceiling() -> None:
    tracker = BudgetTracker(Budget(max_tokens=100))
    bindings = RoleBindings(developer=FakeUsageChatModel(input_tokens=80, output_tokens=80))
    tx = LangChainTransport(bindings, budget=tracker)
    with pytest.raises(BudgetExceededError) as exc_info:
        await tx.invoke("developer", [HumanMessage(content="x")])
    assert exc_info.value.dimension == "max_tokens"


@pytest.mark.asyncio
async def test_invoke_records_tool_calls_and_raises_over_cap() -> None:
    tracker = BudgetTracker(Budget(max_tool_calls=1))
    calls = [{"name": "shell", "args": {"command": "ls"}, "id": "c1"}]
    bindings = RoleBindings(developer=FakeUsageChatModel(tool_calls=calls))
    tx = LangChainTransport(bindings, budget=tracker)
    await tx.invoke("developer", [HumanMessage(content="x")], tools=[])  # 1st call: OK
    with pytest.raises(BudgetExceededError) as exc_info:
        await tx.invoke("developer", [HumanMessage(content="x")], tools=[])  # 2nd: over cap
    assert exc_info.value.dimension == "max_tool_calls"


@pytest.mark.asyncio
async def test_shared_tracker_accumulates_across_multiple_roles() -> None:
    """One BudgetTracker shared by two `invoke()` calls for different roles —
    mirrors how graph.py shares a single transport across every node in a task."""
    tracker = BudgetTracker(Budget(max_tokens=120))
    bindings = RoleBindings(
        planner=FakeUsageChatModel(input_tokens=50, output_tokens=0),
        developer=FakeUsageChatModel(input_tokens=50, output_tokens=0),
    )
    tx = LangChainTransport(bindings, budget=tracker)
    await tx.invoke("planner", [HumanMessage(content="x")])  # total: 50
    await tx.invoke("developer", [HumanMessage(content="x")])  # total: 100
    assert tracker.total_tokens == 100
    with pytest.raises(BudgetExceededError):
        await tx.invoke("developer", [HumanMessage(content="x")])  # total: 150 > 120
