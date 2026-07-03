"""Tests for `agent_room.llm.retry_on_parser_error`.

Background: DeepSeek/Ark intermittently emits empty `tool_use` blocks that
langchain-anthropic surfaces as `OutputParserException`. The wrapper retries
those once by default; transport / non-parser errors must propagate.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import Runnable

from agent_room.llm import DEFAULT_PARSER_RETRY_ATTEMPTS, retry_on_parser_error


class _Flaky(Runnable):
    """Runnable that raises an exception once, then returns a value.

    Tracks total attempts via `calls`. Set `exc=None` to never raise.
    """

    def __init__(self, *, fails: int, exc: type[BaseException] | None, value: Any = "ok") -> None:
        self.fails_remaining = fails
        self.exc = exc
        self.value = value
        self.calls = 0

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls += 1
        if self.fails_remaining > 0 and self.exc is not None:
            self.fails_remaining -= 1
            raise self.exc("synthetic")
        return self.value

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        return self.invoke(input, config, **kwargs)


def test_default_attempts_constant_is_two():
    """Document the policy choice: 2 total attempts (1 retry).

    Bumping this is a real decision — see the rationale in
    `agent_room/llm/_retry.py`. Test exists so the change is intentional.
    """
    assert DEFAULT_PARSER_RETRY_ATTEMPTS == 2


def test_passthrough_when_no_error():
    runnable = _Flaky(fails=0, exc=None, value="result")
    wrapped = retry_on_parser_error(runnable)
    assert wrapped.invoke({}) == "result"
    assert runnable.calls == 1


def test_retries_once_on_parser_exception():
    runnable = _Flaky(fails=1, exc=OutputParserException, value="result-after-retry")
    wrapped = retry_on_parser_error(runnable)
    assert wrapped.invoke({}) == "result-after-retry"
    assert runnable.calls == 2  # 1 initial + 1 retry


def test_exhausts_attempts_then_raises():
    runnable = _Flaky(fails=10, exc=OutputParserException)
    wrapped = retry_on_parser_error(runnable, attempts=3)
    with pytest.raises(OutputParserException):
        wrapped.invoke({})
    assert runnable.calls == 3


def test_does_not_retry_other_exceptions():
    """A `ValueError` that's NOT an `OutputParserException` must propagate.

    `OutputParserException` IS-A `ValueError` — we must filter on the exact
    class, not the parent. Otherwise we'd swallow user bugs (bad input,
    schema mismatches) and turn them into latency.
    """
    runnable = _Flaky(fails=1, exc=ValueError)
    wrapped = retry_on_parser_error(runnable, attempts=3)
    with pytest.raises(ValueError):
        wrapped.invoke({})
    assert runnable.calls == 1


def test_does_not_retry_runtime_error():
    """Transport-style errors are not in scope for this wrapper."""
    runnable = _Flaky(fails=1, exc=RuntimeError)
    wrapped = retry_on_parser_error(runnable, attempts=3)
    with pytest.raises(RuntimeError):
        wrapped.invoke({})
    assert runnable.calls == 1


def test_attempts_one_is_no_op():
    """`attempts=1` returns the original runnable unchanged.

    Saves a wrapper allocation and makes the disabled state observable in
    a debugger / repr without going through RunnableRetry's plumbing.
    """
    runnable = _Flaky(fails=0, exc=None, value="r")
    wrapped = retry_on_parser_error(runnable, attempts=1)
    assert wrapped is runnable


def test_zero_attempts_rejected():
    runnable = _Flaky(fails=0, exc=None)
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        retry_on_parser_error(runnable, attempts=0)


@pytest.mark.asyncio
async def test_async_retry_path():
    """`ainvoke` path must retry the same way `invoke` does."""
    runnable = _Flaky(fails=1, exc=OutputParserException, value="async-result")
    wrapped = retry_on_parser_error(runnable)
    assert await wrapped.ainvoke({}) == "async-result"
    assert runnable.calls == 2


async def test_reviewer_factory_wraps_structured_output():
    """Smoke: every `transport.structured(...)` call must run through `with_retry`.

    Post-ADR-0013 the wrap moved from factory time to call time inside
    `LangChainTransport.structured`. This test asserts the contract by
    spying on `with_retry` calls during a real reviewer invocation.
    """
    from unittest.mock import patch

    from langchain_core.runnables.retry import RunnableRetry

    from agent_room.config import RoleBindings
    from agent_room.roles.reviewer import make_reviewer
    from agent_room.schemas import ReviewerDecision
    from agent_room.state import TaskState
    from tests.fakes import FakeReviewerLLM

    bindings = RoleBindings(
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[
                ReviewerDecision(decision="approved", feedback="ok", issues=[], confidence=0.9),
            ],
        )
    )
    fn = make_reviewer(bindings)
    assert callable(fn)

    state: TaskState = {  # type: ignore[typeddict-item]
        "title": "t",
        "description": "d",
        "plan": "step 1",
        "code": "print(1)",
        "events": [],
        "artifacts": [],
        "revision_round": 0,
        "max_revisions": 2,
    }

    seen: list[type] = []
    real_with_retry = type(bindings.reviewer).with_retry

    def spy(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = real_with_retry(self, *args, **kwargs)
        seen.append(type(result))
        return result

    with patch(
        "langchain_core.runnables.base.Runnable.with_retry",
        autospec=True,
        side_effect=spy,
    ):
        await fn(state)

    assert RunnableRetry in seen, (
        "expected transport.structured() to wrap with_structured_output in retry_on_parser_error"
    )
