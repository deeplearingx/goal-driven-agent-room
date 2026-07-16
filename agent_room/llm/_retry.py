"""Retry wrapper for `OutputParserException`-style provider noise.

Background — see [docs/findings/2026-06-11-smoke-v0.1.md] §F2: DeepSeek-V4-Pro
through the Volcano Ark gateway intermittently returns an empty `tool_use`
block, which langchain-anthropic's structured-output dispatcher surfaces as
`OutputParserException("Unknown tool type: '')`. Empirically 3/9 escalation
runs failed this way — high enough to wreck `with_structured_output`-heavy
nodes (reviewer, planner_gate, two_call_review) but low enough that an
immediate redraw usually succeeds.

This is a transient *content* fault, not a transport fault: rate limiting,
auth errors, and 5xx are deliberately NOT retried here — those need
exponential backoff and a different upper bound. Callers wanting that
behaviour should compose another `with_retry` outside this one.

Design notes:
  - We retry only `OutputParserException`. ValueError catches it (it's a
    subclass) but would also swallow user input errors — too broad.
  - No wait between attempts. The fault is randomized re-draws from the
    provider; backing off doesn't change the odds and just costs latency.
  - 2 attempts (1 retry) by default. Empirically the failure rate per call
    is ~33%; 2 attempts brings it to ~11%, 3 attempts to ~4%. The marginal
    third attempt costs a full call and only buys 7pp — not worth the
    p99 tail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from langchain_core.exceptions import OutputParserException

if TYPE_CHECKING:
    from langchain_core.runnables import Runnable

DEFAULT_PARSER_RETRY_ATTEMPTS = 2

_Out = TypeVar("_Out")


def retry_on_parser_error(
    runnable: Runnable[Any, _Out],
    *,
    attempts: int = DEFAULT_PARSER_RETRY_ATTEMPTS,
) -> Runnable[Any, _Out]:
    """Wrap `runnable` so transient `OutputParserException`s are retried.

    `attempts` is the *total* number of attempts (initial + retries), matching
    LangChain's `stop_after_attempt` convention. `attempts=1` disables retry
    (returns the original runnable unchanged so we don't pay for a wrapper
    that never fires).
    """
    if attempts < 1:
        msg = f"attempts must be >= 1, got {attempts}"
        raise ValueError(msg)
    if attempts == 1:
        return runnable
    return runnable.with_retry(
        retry_if_exception_type=(OutputParserException,),
        stop_after_attempt=attempts,
        wait_exponential_jitter=False,
    )
