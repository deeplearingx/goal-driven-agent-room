"""Shared fakes for offline tests.

Importable from any test file or runnable example. No network, no real LLM.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable

from agent_room.config import RoleBindings
from agent_room.schemas import ReviewerDecision


class FakeStructured(Runnable):
    """Mimics `BaseChatModel.with_structured_output` by returning canned outputs.

    Records every prompt in `captured_prompts` so tests can assert on what the
    role actually saw (directives, plan, etc.). Generic over the structured
    output type — pass any sequence of pydantic models / dicts.
    """

    def __init__(self, decisions: Sequence[Any]):
        self._decisions = list(decisions)
        self._idx = 0
        self.captured_prompts: list[str] = []

    def _capture(self, input: Any) -> None:
        if isinstance(input, list):
            joined = "\n".join(
                m.content if isinstance(m.content, str) else str(m.content) for m in input
            )
            self.captured_prompts.append(joined)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        self._capture(input)
        d = self._decisions[min(self._idx, len(self._decisions) - 1)]
        self._idx += 1
        return d

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        return self.invoke(input, config, **kwargs)


class FakeReviewerLLM(FakeListChatModel):
    """A fake chat model whose `with_structured_output` returns canned outputs.

    The underlying `FakeStructured` is memoized per output schema so the
    index for each schema survives across multiple graph builds (needed for
    CLI run → resume tests that rebuild the graph between invocations) and
    so two_call reviewers can hand out one queue per schema.

    Attribute set on subclass / instance:
      `decisions` — default queue, returned for any schema not specifically routed.
      `outputs_by_schema` — optional `{schema_class: [...]}` for variants
         that ask for multiple structured types from the same model
         (e.g. `FocusCheck` then `ReviewerDecision`).
    """

    decisions: list[Any] = []
    outputs_by_schema: dict[Any, list[Any]] = {}
    _structured_pool: dict[Any, Any] = {}

    def with_structured_output(  # type: ignore[override]
        self,
        schema: Any = None,
        **kwargs: Any,
    ) -> Runnable:
        pool = self.__dict__.setdefault("_structured_pool", {})
        if schema in pool:
            return pool[schema]
        outputs = self.outputs_by_schema.get(schema, self.decisions)
        fake = FakeStructured(outputs)
        pool[schema] = fake
        return fake


class CapturingFakeChatModel(FakeListChatModel):
    """A `FakeListChatModel` that records every prompt it was invoked with.

    Test code can assert on `model.captured_prompts` to verify which state
    fields actually reached the developer/planner prompt.
    """

    captured_prompts: list[str] = []

    def _call(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> str:  # type: ignore[override]
        joined = "\n".join(
            m.content if isinstance(m.content, str) else str(m.content) for m in messages
        )
        self.captured_prompts.append(joined)
        return super()._call(messages, *args, **kwargs)


class FakeUsageChatModel(BaseChatModel):
    """A `BaseChatModel` whose response carries `usage_metadata` (and optional
    `tool_calls`) — `FakeListChatModel` only returns plain strings, discarding
    both, so budget-tracking tests (`agent_room/budget.py`) need this instead.
    """

    input_tokens: int = 10
    output_tokens: int = 5
    tool_calls: list[dict[str, Any]] = []

    @property
    def _llm_type(self) -> str:
        return "fake-usage"

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        msg = AIMessage(
            content="ok",
            tool_calls=self.tool_calls,
            usage_metadata={
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])


def bindings_with_fakes(
    *,
    plan_response: str = "1. step\n2. step",
    code_responses: list[str] | None = None,
    decisions: list[ReviewerDecision] | None = None,
    delivery_response: str = "# Delivered",
    developer: FakeListChatModel | None = None,
) -> RoleBindings:
    """Build a `RoleBindings` populated with deterministic fake LLMs.

    Pass `developer=` to inject a custom developer model (e.g.
    `CapturingFakeChatModel`) while keeping the other roles default.
    """

    return RoleBindings(
        planner=FakeListChatModel(responses=[plan_response]),
        developer=developer or FakeListChatModel(responses=code_responses or ["code"]),
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=decisions
            or [ReviewerDecision(decision="approved", feedback="ok", issues=[], confidence=0.9)],
        ),
        delivery=FakeListChatModel(responses=[delivery_response]),
    )
