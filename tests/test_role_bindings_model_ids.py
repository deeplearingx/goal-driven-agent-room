"""Tests for `RoleBindings.model_ids()` — used by `/healthz` (ADR-0011)."""

from __future__ import annotations

from agent_room.config import RoleBindings, Settings
from tests.fakes import FakeListChatModel, FakeReviewerLLM


def _settings(default: str = "stub-default", **role_models: str) -> Settings:
    return Settings(
        db_path=":memory:",
        host="127.0.0.1",
        port=8765,
        default_model=default,
        role_models=dict(role_models),
    )


def test_model_ids_falls_back_to_default_for_unbound_roles() -> None:
    bindings = RoleBindings(settings=_settings(default="model-D"))
    assert bindings.model_ids() == {
        "planner": "model-D",
        "developer": "model-D",
        "reviewer": "model-D",
        "delivery": "model-D",
        "supervisor": "model-D",
    }


def test_model_ids_uses_role_models_when_present() -> None:
    bindings = RoleBindings(
        settings=_settings(
            default="model-D",
            planner="model-P",
            reviewer="model-R",
        )
    )
    out = bindings.model_ids()
    assert out["planner"] == "model-P"
    assert out["developer"] == "model-D"
    assert out["reviewer"] == "model-R"
    assert out["delivery"] == "model-D"


def test_model_ids_describes_explicit_bound_models() -> None:
    plan_llm = FakeListChatModel(responses=["plan"])
    review_llm = FakeReviewerLLM(responses=["unused"], decisions=[])
    bindings = RoleBindings(
        planner=plan_llm,
        reviewer=review_llm,
        settings=_settings(default="fallback"),
    )
    out = bindings.model_ids()
    assert out["planner"] != "fallback"
    assert out["developer"] == "fallback"
    assert out["delivery"] == "fallback"
    assert isinstance(out["reviewer"], str) and out["reviewer"]


def test_model_ids_does_not_leak_keys() -> None:
    bindings = RoleBindings(
        settings=Settings(
            db_path=":memory:",
            host="x",
            port=1,
            default_model="model-D",
            role_models={"developer": "model-Dev"},
            anthropic_auth_token="secret-token",
            anthropic_api_key="secret-key",
            openai_api_key="secret-openai",
        )
    )
    out = bindings.model_ids()
    flat = " ".join(out.values())
    assert "secret-token" not in flat
    assert "secret-key" not in flat
    assert "secret-openai" not in flat
