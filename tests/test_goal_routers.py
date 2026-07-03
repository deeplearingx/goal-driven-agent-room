"""goal_router + stuck_router — the goal mode's deterministic routing rules."""

from __future__ import annotations

from agent_room.routers import STUCK_EVERY, goal_router, stuck_router
from agent_room.schemas import StuckDecision, VerificationResult


def _failed(round_no: int) -> VerificationResult:
    return VerificationResult(passed=False, exit_code=1, output_tail="fail", round=round_no)


# ---------- goal_router ----------


def test_no_verification_routes_to_reviewer() -> None:
    assert goal_router({"verification": None}) == "reviewer"
    assert goal_router({}) == "reviewer"


def test_passed_routes_to_delivery() -> None:
    state = {"verification": VerificationResult(passed=True, exit_code=0), "verify_round": 1}
    assert goal_router(state) == "delivery"


def test_failed_routes_back_to_developer() -> None:
    state = {"verification": _failed(1), "verify_round": 1, "max_iterations": 10}
    assert goal_router(state) == "developer"


def test_ceiling_halts() -> None:
    state = {"verification": _failed(10), "verify_round": 10, "max_iterations": 10}
    assert goal_router(state) == "halt"


def test_stuck_threshold_routes_to_supervisor() -> None:
    state = {"verification": _failed(STUCK_EVERY), "verify_round": STUCK_EVERY}
    assert goal_router(state) == "supervisor"


def test_stuck_threshold_multiples_route_to_supervisor() -> None:
    state = {"verification": _failed(STUCK_EVERY * 2), "verify_round": STUCK_EVERY * 2}
    assert goal_router(state) == "supervisor"


def test_non_multiple_failure_rounds_do_not_hit_supervisor() -> None:
    for r in range(1, STUCK_EVERY * 3):
        if r % STUCK_EVERY == 0:
            continue
        state = {"verification": _failed(r), "verify_round": r, "max_iterations": 100}
        assert goal_router(state) == "developer", f"round {r} should iterate, not escalate"


def test_ceiling_wins_over_stuck_threshold() -> None:
    """When the ceiling and the stuck threshold coincide, the brake wins —
    no point asking the supervisor for a strategy the budget won't fund."""
    state = {
        "verification": _failed(STUCK_EVERY),
        "verify_round": STUCK_EVERY,
        "max_iterations": STUCK_EVERY,
    }
    assert goal_router(state) == "halt"


# ---------- stuck_router ----------


def test_stuck_continue_routes_to_developer() -> None:
    state = {"stuck_decision": StuckDecision(action="continue", reasoning="progress visible")}
    assert stuck_router(state) == "developer"


def test_stuck_replan_routes_to_planner() -> None:
    state = {"stuck_decision": StuckDecision(action="replan", reasoning="same error repeats")}
    assert stuck_router(state) == "planner"


def test_stuck_ask_user_halts() -> None:
    state = {
        "stuck_decision": StuckDecision(
            action="ask_user", reasoning="goal ambiguous", question="which behavior?"
        )
    }
    assert stuck_router(state) == "halt"


def test_stuck_abort_halts() -> None:
    state = {"stuck_decision": StuckDecision(action="abort", reasoning="impossible")}
    assert stuck_router(state) == "halt"


def test_stuck_missing_decision_halts_defensively() -> None:
    assert stuck_router({}) == "halt"
