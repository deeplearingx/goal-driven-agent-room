"""Tests for v0.2 GraphSpec — schema validation, presets, build_from_spec.

Mirrors the test list in [docs/adr/0008-graph-spec.md] §"测试覆盖".
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import ValidationError

from agent_room.config import RoleBindings
from agent_room.graph import build_agent_room_graph, build_from_spec
from agent_room.schemas import ReviewerDecision, TaskRequest
from agent_room.service import AgentRoomService
from agent_room.spec import (
    GraphSpec,
    load_graph_spec,
    load_preset,
    resolve_router,
    resolve_spec_source,
)
from agent_room.state import TaskState
from tests.fakes import CapturingFakeChatModel, bindings_with_fakes

# --- 1. schema validation -------------------------------------------------


def test_load_full_preset_round_trips() -> None:
    spec = load_preset("full")
    assert spec.name == "full"
    assert spec.entry == "planner"
    assert set(spec.nodes) == {"planner", "developer", "reviewer", "delivery"}
    # Edge count: planner→dev, dev→reviewer, reviewer(branches), delivery→END
    assert len(spec.edges) == 4


def test_invalid_spec_unknown_node_target() -> None:
    bad = {
        "name": "bad",
        "entry": "developer",
        "nodes": {"developer": {"role": "developer"}},
        "edges": [{"from": "developer", "to": "ghost"}],
    }
    with pytest.raises(ValidationError, match="ghost"):
        GraphSpec.model_validate(bad)


def test_invalid_spec_entry_not_in_nodes() -> None:
    bad = {
        "name": "bad",
        "entry": "ghost",
        "nodes": {"developer": {"role": "developer"}},
        "edges": [{"from": "developer", "to": "__end__"}],
    }
    with pytest.raises(ValidationError, match="entry='ghost'"):
        GraphSpec.model_validate(bad)


def test_invalid_spec_both_to_and_branches() -> None:
    bad = {
        "name": "bad",
        "entry": "developer",
        "nodes": {"developer": {"role": "developer"}},
        "edges": [
            {
                "from": "developer",
                "to": "__end__",
                "branches": [{"on": "approved", "to": "__end__"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="exactly one"):
        GraphSpec.model_validate(bad)


def test_invalid_spec_state_class_reserved_for_v03() -> None:
    bad = {
        "name": "bad",
        "entry": "developer",
        "nodes": {"developer": {"role": "developer"}},
        "edges": [{"from": "developer", "to": "__end__"}],
        "state_class": "my.custom:State",
    }
    with pytest.raises(ValidationError, match="state_class"):
        GraphSpec.model_validate(bad)


def test_load_graph_spec_from_yaml_string() -> None:
    src = """
    name: tiny
    entry: developer
    nodes:
      developer: { role: developer }
    edges:
      - { from: developer, to: __end__ }
    """
    spec = load_graph_spec(src)
    assert spec.name == "tiny"


def test_load_graph_spec_rejects_yaml_load_attack_surface() -> None:
    """`yaml.load` would let `!!python/object` instantiate classes — must use safe_load.

    We don't try to exploit it here; we just confirm an attempted Python tag
    is rejected (yaml.safe_load raises a constructor error on `!!python/...`).
    """

    hostile = """
    !!python/object/new:os.system
    args: ['echo pwned']
    """
    with pytest.raises(yaml.YAMLError):
        load_graph_spec(hostile)


# --- 2. preset / source resolution ---------------------------------------


def test_resolve_spec_source_preset_name() -> None:
    spec = resolve_spec_source("solo")
    assert spec.name == "solo"


def test_resolve_spec_source_unknown_preset_lists_available(tmp_path: Any) -> None:
    with pytest.raises(FileNotFoundError, match="full"):
        resolve_spec_source("nonexistent_preset")


# --- 3. build_from_spec equivalence with legacy builder -------------------


def _approved_bindings() -> RoleBindings:
    return bindings_with_fakes(
        plan_response="plan",
        code_responses=["code"],
        decisions=[ReviewerDecision(decision="approved", feedback="ok", confidence=0.9)],
        delivery_response="# Delivered",
    )


@pytest.mark.asyncio
async def test_full_preset_equivalent_to_legacy_builder() -> None:
    """`build_from_spec(full)` and the public `build_agent_room_graph` produce
    identical task outcomes on the happy path."""

    spec = load_preset("full")

    bindings_a = _approved_bindings()
    graph_a = build_from_spec(spec, bindings_a)
    res_a = await AgentRoomService(graph_a).run(TaskRequest(title="t", description="d"))

    bindings_b = _approved_bindings()
    graph_b = build_agent_room_graph(bindings_b)
    res_b = await AgentRoomService(graph_b).run(TaskRequest(title="t", description="d"))

    assert res_a.status == res_b.status == "completed"
    assert res_a.delivery == res_b.delivery
    assert res_a.rounds == res_b.rounds
    # Plan/code text must match — same fakes, same order.
    assert res_a.plan == res_b.plan
    assert res_a.code == res_b.code


# --- 4. solo preset -------------------------------------------------------


@pytest.mark.asyncio
async def test_solo_preset_runs_without_reviewer_or_delivery() -> None:
    spec = load_preset("solo")
    bindings = RoleBindings(
        developer=FakeListChatModel(responses=["solo code"]),
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)
    result = await service.run(TaskRequest(title="t", description="d"))

    # No reviewer, no delivery → solo is the only node, terminal at END.
    assert result.code == "solo code"
    assert result.delivery is None
    assert result.review is None
    # Status is "running" because no terminal review/delivery signal — that
    # is the documented behaviour of `_materialize_status` when neither
    # delivery nor reviewer have spoken.
    assert result.status == "running"


# --- 5. dev_review preset --------------------------------------------------


@pytest.mark.asyncio
async def test_dev_review_preset_loops_then_approves() -> None:
    spec = load_preset("dev_review")
    bindings = bindings_with_fakes(
        code_responses=["v1", "v2"],
        decisions=[
            ReviewerDecision(decision="revision_required", feedback="fix", issues=["x"]),
            ReviewerDecision(decision="approved", feedback="ok", confidence=0.9),
        ],
        delivery_response="(unused — preset has no delivery)",
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)
    result = await service.run(TaskRequest(title="t", description="d", max_revisions=2))

    # Two reviewer rounds: revision_required then approved → END.
    assert result.rounds == 2
    assert result.code == "v2"
    assert result.delivery is None
    assert result.review is not None
    assert result.review.decision == "approved"


# --- 6. router dotted-path ------------------------------------------------


def my_test_router(state: TaskState) -> str:  # noqa: ARG001 — used by dotted-path test
    return "halt"


def test_router_dotted_path_resolves() -> None:
    fn = resolve_router(f"{__name__}:my_test_router")
    assert fn is my_test_router


def test_router_dotted_path_missing_attr_raises() -> None:
    with pytest.raises(AttributeError):
        resolve_router(f"{__name__}:does_not_exist")


def test_router_dotted_path_missing_module_raises() -> None:
    with pytest.raises(ModuleNotFoundError):
        resolve_router("agent_room.does_not_exist:fn")


def test_router_dotted_path_missing_colon_raises() -> None:
    with pytest.raises(ValueError, match="dotted path"):
        resolve_router("agent_room.routers.review_router")


def test_router_default_is_review_router() -> None:
    from agent_room.routers import review_router

    assert resolve_router(None) is review_router


# --- 7. NodeSpec features wire through ------------------------------------


@pytest.mark.asyncio
async def test_node_prompt_override_replaces_system_message() -> None:
    """A spec-level `prompt_override` reaches the SystemMessage of that node."""

    capturing_dev = CapturingFakeChatModel(responses=["code"])
    bindings = bindings_with_fakes(developer=capturing_dev)

    spec = GraphSpec.model_validate(
        {
            "name": "override-test",
            "entry": "developer",
            "nodes": {
                "developer": {
                    "role": "developer",
                    "prompt_override": "OVERRIDDEN-DEV-PROMPT",
                }
            },
            "edges": [{"from": "developer", "to": "__end__"}],
        }
    )
    graph = build_from_spec(spec, bindings)
    service = AgentRoomService(graph)
    await service.run(TaskRequest(title="t", description="d"))

    assert capturing_dev.captured_prompts, "developer was never invoked"
    assert "OVERRIDDEN-DEV-PROMPT" in capturing_dev.captured_prompts[0]


@pytest.mark.asyncio
async def test_extra_context_keys_reach_human_prompt() -> None:
    """`extra_context_keys` declared in the spec land in the HUMAN message — F2 fix path."""

    capturing_dev = CapturingFakeChatModel(responses=["code"])
    bindings = bindings_with_fakes(developer=capturing_dev)

    spec = GraphSpec.model_validate(
        {
            "name": "extra-ctx-test",
            "entry": "developer",
            "nodes": {
                "developer": {
                    "role": "developer",
                    # `max_revisions` is a present integer field on TaskState
                    # — using it as a probe avoids needing custom state.
                    "extra_context_keys": ["max_revisions"],
                }
            },
            "edges": [{"from": "developer", "to": "__end__"}],
        }
    )
    graph = build_from_spec(spec, bindings)
    await AgentRoomService(graph).run(TaskRequest(title="t", description="d", max_revisions=7))

    prompt = capturing_dev.captured_prompts[0]
    assert "# Max Revisions" in prompt
    assert "7" in prompt


# --- 8. custom router via dotted path executes at run time ---------------


def _always_halt(state: TaskState) -> str:  # noqa: ARG001
    return "halt"


@pytest.mark.asyncio
async def test_custom_router_via_dotted_path_executes() -> None:
    """A user-supplied dotted-path router gets called and its signal mapped."""

    spec = GraphSpec.model_validate(
        {
            "name": "custom-router-test",
            "entry": "developer",
            "nodes": {
                "developer": {"role": "developer"},
                "reviewer": {"role": "reviewer"},
            },
            "edges": [
                {"from": "developer", "to": "reviewer"},
                {
                    "from": "reviewer",
                    "router": f"{__name__}:_always_halt",
                    "branches": [{"on": "halt", "to": "__end__"}],
                },
            ],
        }
    )

    bindings = bindings_with_fakes(
        decisions=[ReviewerDecision(decision="approved", feedback="ok")],
    )
    graph = build_from_spec(spec, bindings)
    # Should terminate via the custom router's "halt" signal even though the
    # reviewer says "approved" — proves the override is in force.
    result = await AgentRoomService(graph).run(TaskRequest(title="t", description="d"))
    assert result.delivery is None  # never reached delivery (which doesn't exist anyway)
    assert result.review is not None
    assert result.review.decision == "approved"
