"""Schemas: typed contracts for tasks, reviewer decisions, artifacts, events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["planner", "developer", "reviewer", "delivery", "verifier", "supervisor"]
ReviewDecisionLiteral = Literal["approved", "revision_required", "need_user_decision"]
StuckAction = Literal["continue", "replan", "ask_user", "abort"]


class TaskRequest(BaseModel):
    title: str
    description: str
    max_revisions: int = Field(default=2, ge=0, le=10)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Physical persistence isolation (v1.x §6.15) — routes this task's
    # checkpointer/memory to a tenant-exclusive `agent_room.db` +
    # curated/transcript store. "default" (unset) preserves single-tenant
    # behavior exactly. Not verified/authenticated by agent-room itself —
    # see SECURITY.md's tenant trust-boundary note.
    tenant_id: str = "default"

    # Goal mode (PLAN.md goal-mode section). All three unset (defaults) on a
    # non-goal preset = zero behavior change; the fields only matter when the
    # graph contains a `verifier` node (AGENT_ROOM_GRAPH=goal).
    verify_command: str | None = None
    """Objective goal oracle: run this command in the task workspace after
    each developer pass; exit 0 = goal achieved. `None` falls back to the
    reviewer's subjective `approved`. Passes the same shell allowlist as the
    developer's shell tool — see SECURITY.md."""
    verify_files: dict[str, str] = Field(default_factory=dict)
    """Workspace-relative path → content, re-written before EVERY verification
    run. This is the anti-tamper mechanism: a developer that edits the test
    files to make them pass gets its edits overwritten before the oracle
    runs. Tasks that rely on files the developer can touch carry that risk
    themselves — see SECURITY.md."""
    max_iterations: int = Field(default=10, ge=1, le=50)
    """Goal-mode loop ceiling — a runaway brake, not the expected exit. The
    normal exit is the oracle passing (or reviewer approval); hitting this
    ceiling surfaces as status='failed'."""

    graph: str | None = None
    """Per-request graph preset override (workflow=`full_react` vs `goal`).
    `None` uses the server's configured default (`AGENT_ROOM_GRAPH`) — zero
    behavior change. The server only honors an allowlisted set (see
    `server/api.py::USER_SELECTABLE_GRAPHS`); other values are rejected so a
    client can't select a preset that skips review."""


class VerificationResult(BaseModel):
    """One objective verification run's outcome (goal mode)."""

    passed: bool
    exit_code: int | None = None
    """`None` = the command never produced an exit code (timeout, allowlist
    rejection, spawn failure) — details in `output_tail`."""
    output_tail: str = ""
    round: int = 0


class StuckDecision(BaseModel):
    """The supervisor's one-shot strategy call when the goal loop is stuck
    (every STUCK_EVERY consecutive verification failures) — deliberately NOT
    a general-purpose router decision; the goal loop's mechanical routing
    stays deterministic (see agent_room/routers.py::goal_router)."""

    action: StuckAction
    reasoning: str
    question: str = ""
    """For action='ask_user': the question to surface to the human. Reused
    through the ReviewerDecision need_user_decision contract so the existing
    pause/resume machinery applies unchanged."""


class ReviewerDecision(BaseModel):
    """Strict reviewer protocol — replaces the TS parseReviewerJson fallback parser."""

    decision: ReviewDecisionLiteral
    feedback: str
    issues: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class PlannerGateOutput(BaseModel):
    """Schema for the planner-gate variant — see ADR-0008 / F2 escalation lab.

    The planner is asked to commit to a numbered plan AND to surface ambiguity
    explicitly. A non-empty `open_questions` list short-circuits the run into
    `awaiting_user` before any code is written, addressing F2's failure mode
    where an under-specified task got handed to the developer who invented
    its own product decisions.
    """

    plan: str
    open_questions: list[str] = Field(default_factory=list)
    rationale: str = ""


class FocusCheck(BaseModel):
    """First-call output of the two-call reviewer variant.

    Inverts the framing the model trained on: instead of "find issues in this
    code", we ask "is the task fully specified?". The bias toward finding
    things to fix doesn't apply when the question is about specification
    completeness, which is what the v0.1 smoke run revealed (F2 attempt-2).
    """

    is_fully_specified: bool
    open_questions: list[str] = Field(default_factory=list)
    rationale: str = ""


class Artifact(BaseModel):
    kind: Literal["plan", "code", "review", "delivery"]
    role: Role
    content: str
    round: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Event(BaseModel):
    """Lightweight event record persisted alongside artifacts."""

    type: str
    role: Role | None = None
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskResult(BaseModel):
    task_id: str
    status: Literal["completed", "failed", "awaiting_user", "running"]
    plan: str | None = None
    code: str | None = None
    review: ReviewerDecision | None = None
    delivery: str | None = None
    rounds: int = 0
    verification: VerificationResult | None = None
    stuck_decision: StuckDecision | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    """Populated when status='awaiting_user' because the developer ReAct
    loop hit `tool_mode: approval`. Each entry is the interrupt payload
    `{"action": "approve_tool_call", "name", "args", "id"}`. Resume with
    `service.resume(task_id, decision, at_node="tool_call")`."""
