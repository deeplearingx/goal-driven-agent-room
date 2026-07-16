"""Transcript-push pattern: nodes with new information for an in-flight ReAct
developer append it to `dev_messages` (PLAN.md goal-mode section §0).

Covers the reviewer feedback push (a mainline bug fix — a sent-back ReAct
developer used to retry blind on its stale transcript) and the planner's
revised-plan push (goal-mode re-plan path), plus the zero-behavior-change
guarantee for non-ReAct presets (empty `dev_messages` ⇒ no push).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent_room.config import RoleBindings
from agent_room.roles.planner import make_planner
from agent_room.roles.reviewer import make_reviewer
from agent_room.schemas import ReviewerDecision, VerificationResult
from tests.fakes import FakeReviewerLLM


class CaptureLLM(BaseChatModel):
    """Returns a canned text and records every prompt it saw."""

    reply: str = "1. do the thing"
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "capture"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    def model_post_init(self, __ctx: Any) -> None:
        object.__setattr__(self, "seen", [])


def _revision(feedback: str = "fix the bug", issues: list[str] | None = None) -> ReviewerDecision:
    return ReviewerDecision(
        decision="revision_required", feedback=feedback, issues=issues or ["issue-1"]
    )


# ---------- reviewer push ----------


async def test_reviewer_pushes_feedback_into_nonempty_transcript() -> None:
    llm = FakeReviewerLLM(responses=[], decisions=[_revision("use a dict not a list")])
    reviewer = make_reviewer(RoleBindings(reviewer=llm))
    update = await reviewer(
        {
            "title": "t",
            "description": "d",
            "code": "old code",
            "dev_messages": [AIMessage(content="prior attempt")],
            "dev_round": 5,
        }
    )
    pushed = update["dev_messages"]
    assert len(pushed) == 1
    assert isinstance(pushed[0], HumanMessage)
    assert "use a dict not a list" in pushed[0].content
    assert "issue-1" in pushed[0].content
    assert update["dev_round"] == 0


async def test_reviewer_no_push_when_transcript_empty() -> None:
    """Non-ReAct presets: reviewer behavior is byte-for-byte unchanged."""
    llm = FakeReviewerLLM(responses=[], decisions=[_revision()])
    reviewer = make_reviewer(RoleBindings(reviewer=llm))
    update = await reviewer({"title": "t", "description": "d"})
    assert "dev_messages" not in update
    assert "dev_round" not in update


async def test_reviewer_no_push_on_approval() -> None:
    llm = FakeReviewerLLM(
        responses=[], decisions=[ReviewerDecision(decision="approved", feedback="ship it")]
    )
    reviewer = make_reviewer(RoleBindings(reviewer=llm))
    update = await reviewer(
        {"title": "t", "description": "d", "dev_messages": [AIMessage(content="attempt")]}
    )
    assert "dev_messages" not in update


# ---------- planner push (goal-mode re-plan) ----------


async def test_planner_pushes_revised_plan_into_nonempty_transcript() -> None:
    llm = CaptureLLM(reply="1. new approach")
    planner = make_planner(RoleBindings(planner=llm))
    update = await planner(
        {
            "title": "t",
            "description": "d",
            "dev_messages": [AIMessage(content="prior attempt")],
            "dev_round": 3,
        }
    )
    pushed = update["dev_messages"]
    assert len(pushed) == 1
    assert "# Revised plan" in pushed[0].content
    assert "1. new approach" in pushed[0].content
    assert update["dev_round"] == 0


async def test_planner_no_push_when_transcript_empty() -> None:
    """Every existing preset (planner always runs first): unchanged."""
    llm = CaptureLLM()
    planner = make_planner(RoleBindings(planner=llm))
    update = await planner({"title": "t", "description": "d"})
    assert "dev_messages" not in update
    assert "dev_round" not in update


async def test_replan_prompt_includes_failure_context() -> None:
    """A goal-mode re-plan must see what kept failing, not restate the task."""
    llm = CaptureLLM()
    planner = make_planner(RoleBindings(planner=llm))
    await planner(
        {
            "title": "t",
            "description": "d",
            "plan": "1. old plan",
            "verification": VerificationResult(
                passed=False, exit_code=1, output_tail="ImportError: no module named foo", round=3
            ),
        }
    )
    prompt_text = "\n".join(str(m.content) for m in llm.seen[0])
    assert "1. old plan" in prompt_text
    assert "ImportError: no module named foo" in prompt_text
    assert "REVISED plan" in prompt_text


async def test_first_plan_prompt_has_no_failure_section() -> None:
    llm = CaptureLLM()
    planner = make_planner(RoleBindings(planner=llm))
    await planner({"title": "t", "description": "d"})
    prompt_text = "\n".join(str(m.content) for m in llm.seen[0])
    assert "Previous plan" not in prompt_text
