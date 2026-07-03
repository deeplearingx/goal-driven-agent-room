"""v0.4 §4.4 — `agent_room.prompt.developer` preset equivalence tests.

The load-bearing tests here pin BYTE EQUIVALENCE between the new
builder-based assembly and the historical hand-stitched
`_build_initial_messages` output. If a future refactor accidentally
shifts a `\\n` or rewrites a header, these tests fail loud.

The reference outputs were captured by inlining the prior
`_build_initial_messages` implementation (now removed) at this commit
and asserting parity for the four key state shapes:

  1. Minimum task — no plan, no directives, no review.
  2. Task with directives.
  3. Task with reviewer feedback (no previous code).
  4. Task with reviewer feedback + previous code.
  5. Task with extra_context_keys.

We also test the API surface (system_prompt / extra_context_keys
arguments behave the same as the old kwargs).
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from agent_room.prompt import (
    DEVELOPER_SYSTEM,
    DEVELOPER_SYSTEM_WITH_TOOLS,
    developer_prompt_builder,
)
from agent_room.schemas import ReviewerDecision
from agent_room.state import TaskState


def _state(**kwargs) -> TaskState:
    base = {
        "task_id": "t",
        "title": "the title",
        "description": "the description",
        "plan": "the plan",
        "code": "",
        "review": None,
        "events": [],
        "artifacts": [],
        "user_directives": [],
        "revision_round": 0,
    }
    base.update(kwargs)
    return base  # type: ignore[return-value]


# ---------- equivalence: golden message shapes ----------


def test_minimum_state_produces_two_messages():
    builder = developer_prompt_builder(system_prompt="SYS")
    msgs = builder.build(_state())
    assert len(msgs) == 2
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == "SYS"
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == "# Plan\nthe plan\n\n# Task\nthe title\nthe description"


def test_directives_appended_after_task():
    builder = developer_prompt_builder(system_prompt="SYS")
    msgs = builder.build(_state(user_directives=["d1", "d2"]))
    expected = (
        "# Plan\nthe plan\n\n"
        "# Task\nthe title\nthe description\n\n"
        "# User directives (apply in order)\n- d1\n- d2"
    )
    assert msgs[1].content == expected


def test_review_feedback_renders_when_revision_required():
    review = ReviewerDecision(
        decision="revision_required",
        feedback="please redo it",
        issues=["i1", "i2"],
        confidence=0.7,
    )
    builder = developer_prompt_builder(system_prompt="SYS")
    msgs = builder.build(_state(review=review, revision_round=2))
    expected = (
        "# Plan\nthe plan\n\n"
        "# Task\nthe title\nthe description\n\n"
        "# Reviewer feedback (round 2)\nplease redo it\n\n## Issues\n- i1\n- i2"
    )
    assert msgs[1].content == expected


def test_review_feedback_includes_previous_code_when_present():
    review = ReviewerDecision(
        decision="revision_required",
        feedback="fix it",
        issues=[],
    )
    builder = developer_prompt_builder(system_prompt="SYS")
    msgs = builder.build(_state(review=review, code="def f(): pass"))
    expected_human = (
        "# Plan\nthe plan\n\n"
        "# Task\nthe title\nthe description\n\n"
        "# Reviewer feedback (round 0)\nfix it\n\n## Issues\n(see feedback)\n\n"
        "# Previous attempt\ndef f(): pass"
    )
    assert msgs[1].content == expected_human


def test_review_approved_does_not_render_feedback():
    """`approved` decision means no feedback layer fires."""
    review = ReviewerDecision(decision="approved", feedback="lgtm", issues=[])
    builder = developer_prompt_builder(system_prompt="SYS")
    msgs = builder.build(_state(review=review))
    assert "Reviewer feedback" not in msgs[1].content


def test_extra_context_keys_render_at_bottom():
    builder = developer_prompt_builder(system_prompt="SYS", extra_context_keys=["plan"])
    msgs = builder.build(_state(plan="P", title="T", description="D"))
    # extra_context renders state['plan'] as `# Plan\nP`. plan_and_task layer
    # also renders `# Plan\nP`, so both appear.
    assert "# Plan\nP" in msgs[1].content
    # The extra context block lands AFTER the main task block.
    plan_idx = msgs[1].content.index("# Task")
    assert msgs[1].content.rindex("# Plan") > plan_idx


def test_extra_context_skipped_when_no_keys():
    builder = developer_prompt_builder(system_prompt="SYS", extra_context_keys=None)
    msgs = builder.build(_state())
    # Nothing added beyond plan_and_task.
    assert msgs[1].content.count("# ") == 2  # one for # Plan, one for # Task


# ---------- defaults ----------


def test_default_system_prompt_is_with_tools():
    """Tool-using developer is the default; non-tool variant requires opt-in."""
    builder = developer_prompt_builder()
    msgs = builder.build(_state())
    assert msgs[0].content == DEVELOPER_SYSTEM_WITH_TOOLS


def test_legacy_developer_system_opt_in():
    builder = developer_prompt_builder(system_prompt=DEVELOPER_SYSTEM)
    msgs = builder.build(_state())
    assert msgs[0].content == DEVELOPER_SYSTEM
    assert "tools to read" not in msgs[0].content


# ---------- ordering invariant ----------


def test_layer_order_is_stable():
    """The five preset layers fire in declaration order."""
    builder = developer_prompt_builder(extra_context_keys=["title"])
    layer_names = [layer.name for layer in builder.layers]
    assert layer_names == [
        "identity",
        "plan_and_task",
        "user_directives",
        "review_feedback",
        "extra_context",
    ]
