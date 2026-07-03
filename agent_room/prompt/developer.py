"""Developer-role preset for the layered prompt builder (v0.4 §4.4).

Crystallizes the prior hand-stitched assembly in
[`agent_room/roles/developer_react.py`](../roles/developer_react.py) and
[`agent_room/roles/developer.py`](../roles/developer.py) into a
`PromptBuilder` with five named layers.

Output equivalence:
the builder produces the same `[SystemMessage, HumanMessage]` shape that
`_build_initial_messages` produced before the refactor, byte-for-byte
on every state shape exercised by the v0.3 ReAct tests. That
equivalence is pinned by `tests/test_prompt_developer.py`.

Layer ordering (declaration order, also output order):

    1. identity        (system) — the role's system prompt
    2. plan_and_task   (human)  — `# Plan` + `# Task` headers
    3. user_directives (human)  — appended only if state.user_directives
                                  is non-empty
    4. review_feedback (human)  — appended only when reviewer asked for
                                  a revision; pulls in previous code too
    5. extra_context   (human)  — `NodeSpec.extra_context_keys` rendered
                                  via `format_extra_context`

Layer 1 produces a `SystemMessage`; layers 2-5 all produce text in the
`human` band, which the builder merges into a single `HumanMessage`.
That keeps the wire-format identical to the historical hand-stitched
output: providers see exactly two messages on initial entry.
"""

from __future__ import annotations

from agent_room.prompt.builder import Layer, PromptBuilder
from agent_room.roles._message import format_extra_context
from agent_room.schemas import ReviewerDecision
from agent_room.state import TaskState

__all__ = [
    "DEVELOPER_SYSTEM",
    "DEVELOPER_SYSTEM_WITH_TOOLS",
    "developer_prompt_builder",
    "render_review_feedback_block",
]

DEVELOPER_SYSTEM = """You are the developer in a multi-role engineering team.
Implement the plan in clean, idiomatic code. Output a single fenced code block
plus a short rationale. If reviewer feedback is provided, address every issue
explicitly."""

DEVELOPER_SYSTEM_WITH_TOOLS = """You are the developer in a multi-role engineering team.
Implement the plan in clean, idiomatic code. You have tools to read the
filesystem, write files, and discover code. Use them to ground your
implementation in what's actually there — don't invent APIs. When you have
enough information, output a single fenced code block plus a short rationale.
If reviewer feedback is provided, address every issue explicitly."""


def developer_prompt_builder(
    *,
    system_prompt: str = DEVELOPER_SYSTEM_WITH_TOOLS,
    extra_context_keys: list[str] | None = None,
) -> PromptBuilder:
    """Return a `PromptBuilder` configured for the developer role.

    Args:
        system_prompt: The role's system instruction. Default targets
            the ReAct/with-tools developer; pass `DEVELOPER_SYSTEM` for
            the legacy non-tool developer node.
        extra_context_keys: Same as `NodeSpec.extra_context_keys` —
            additional state keys to render as `# Title\\n{value}`
            blocks at the bottom of the human message.
    """
    return PromptBuilder(
        [
            Layer(name="identity", role="system", render=lambda _state: system_prompt),
            Layer(name="plan_and_task", role="human", render=_render_plan_and_task),
            Layer(name="user_directives", role="human", render=_render_user_directives),
            Layer(name="review_feedback", role="human", render=_render_review_feedback),
            Layer(
                name="extra_context",
                role="human",
                render=lambda state: format_extra_context(state, extra_context_keys) or None,
            ),
        ]
    )


def _render_plan_and_task(state: TaskState) -> str:
    """Always present — even if plan/title/description are empty strings.

    The historical `_build_initial_messages` always emitted these two
    headers unconditionally. We preserve that so models trained on the
    consistent shape don't see a structural shift.
    """
    plan = state.get("plan", "") or ""
    title = state.get("title", "") or ""
    description = state.get("description", "") or ""
    return f"# Plan\n{plan}\n\n# Task\n{title}\n{description}"


def _render_user_directives(state: TaskState) -> str | None:
    directives = state.get("user_directives") or []
    if not directives:
        return None
    joined = "\n".join(f"- {d}" for d in directives)
    return f"# User directives (apply in order)\n{joined}"


def _render_review_feedback(state: TaskState) -> str | None:
    review = state.get("review")
    if not review or review.decision != "revision_required":
        return None
    return render_review_feedback_block(
        review, round_no=state.get("revision_round", 0), previous_code=state.get("code")
    )


def render_review_feedback_block(
    review: ReviewerDecision, *, round_no: int, previous_code: str | None = None
) -> str:
    """The one canonical rendering of reviewer feedback for the developer.

    Used both by the initial-prompt layer above and by the reviewer node's
    transcript-push (appending feedback into an in-flight ReAct
    `dev_messages` transcript, where the initial-prompt path never re-runs
    — see roles/reviewer.py).
    """
    issues = "\n".join(f"- {i}" for i in review.issues) or "(see feedback)"
    block = f"# Reviewer feedback (round {round_no})\n{review.feedback}\n\n## Issues\n{issues}"
    if previous_code:
        block += f"\n\n# Previous attempt\n{previous_code}"
    return block
