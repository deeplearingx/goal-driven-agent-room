"""Tests for v0.3 §3.3: tool permission policy (read_only / approval / unrestricted).

What's covered:
- is_read_only: built-in name table + metadata override + conservative default
- apply_policy: read_only filters, unrestricted passes through, approval raises
- NodeSpec.tool_mode: validates allowed values, rejects mode-without-tools
- _resolve_tools_with_policy: end-to-end through build_from_spec
- spec build fails when policy filters every listed tool (no silent fallback)
"""

from __future__ import annotations

import pytest
from langchain_core.tools import BaseTool

from agent_room.tools import (
    GlobTool,
    ReadTextTool,
    Registry,
    ShellTool,
    WriteTextTool,
    apply_policy,
    is_read_only,
    register_builtin_tools,
)
from agent_room.tools.policy import ALL_MODES, DEFAULT_MODE

# ---------- is_read_only ----------


def test_is_read_only_builtin_table(tmp_path):
    assert is_read_only(ReadTextTool(root=str(tmp_path))) is True
    assert is_read_only(GlobTool(root=str(tmp_path))) is True
    assert is_read_only(WriteTextTool(root=str(tmp_path))) is False
    assert is_read_only(ShellTool(allowlist=["pytest"])) is False


def test_is_read_only_metadata_override_wins(tmp_path):
    """Author-declared metadata flag overrides the name table both ways."""

    class _CustomTool(BaseTool):
        name: str = "custom_thing"
        description: str = "anything"

        def _run(self) -> str:
            return ""

    # Unknown name → conservative False by default.
    plain = _CustomTool()
    assert is_read_only(plain) is False

    # metadata says True → True.
    ro = _CustomTool(metadata={"read_only": True})
    assert is_read_only(ro) is True

    # metadata says False on a built-in name → False (override wins).
    write_marked = ReadTextTool(root=str(tmp_path), metadata={"read_only": False})
    assert is_read_only(write_marked) is False


def test_is_read_only_unknown_name_is_conservative():
    class _Anon(BaseTool):
        name: str = "no_idea"
        description: str = "x"

        def _run(self) -> str:
            return ""

    assert is_read_only(_Anon()) is False


# ---------- apply_policy ----------


def _all_builtin_tools(tmp_path) -> list[BaseTool]:
    return [
        ReadTextTool(root=str(tmp_path)),
        GlobTool(root=str(tmp_path)),
        WriteTextTool(root=str(tmp_path)),
        ShellTool(allowlist=["pytest"]),
    ]


def test_apply_policy_read_only_keeps_only_reads(tmp_path):
    tools = _all_builtin_tools(tmp_path)
    out = apply_policy(tools, "read_only")
    assert {t.name for t in out} == {"read_text", "glob"}


def test_apply_policy_unrestricted_keeps_everything(tmp_path):
    tools = _all_builtin_tools(tmp_path)
    out = apply_policy(tools, "unrestricted")
    assert {t.name for t in out} == {"read_text", "glob", "write_text", "shell"}
    # Returns a copy, not the original list.
    assert out is not tools


def test_apply_policy_approval_passes_through(tmp_path):
    """Approval mode does NOT filter at build time — the gate is runtime.

    Pre-v0.3.x this raised NotImplementedError. The interrupt-based runtime
    gate (`make_approval_wrapper`) means we now keep every listed tool so
    the user can approve / deny each call individually.
    """
    tools = _all_builtin_tools(tmp_path)
    out = apply_policy(tools, "approval")
    assert {t.name for t in out} == {"read_text", "glob", "write_text", "shell"}
    assert out is not tools


def test_apply_policy_unknown_mode_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown permission mode"):
        apply_policy(_all_builtin_tools(tmp_path), "nope")  # type: ignore[arg-type]


def test_default_mode_is_read_only():
    assert DEFAULT_MODE == "read_only"
    assert set(ALL_MODES) == {"read_only", "approval", "unrestricted"}


# ---------- NodeSpec.tool_mode validation ----------


def test_node_spec_tool_mode_rejected_without_tools():
    from agent_room.spec import GraphSpec

    with pytest.raises(ValueError, match="set but tools="):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "entry": "d",
                "nodes": {
                    "d": {"role": "developer", "tool_mode": "unrestricted"},
                },
                "edges": [{"from": "d", "to": "__end__"}],
            }
        )


def test_node_spec_tool_mode_rejects_unknown_value():
    from agent_room.spec import GraphSpec

    with pytest.raises(ValueError):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "entry": "d",
                "nodes": {
                    "d": {
                        "role": "developer",
                        "tools": ["read_text"],
                        "tool_mode": "yolo",
                    },
                },
                "edges": [{"from": "d", "to": "__end__"}],
            }
        )


def test_node_spec_tool_mode_default_is_none():
    """spec doesn't set a literal default — graph layer applies DEFAULT_MODE."""
    from agent_room.spec import NodeSpec

    s = NodeSpec(role="developer", tools=["read_text"])
    assert s.tool_mode is None


# ---------- end-to-end through build_from_spec ----------


def _spec_with_dev_tools(*, tool_names: list[str], mode: str | None = None):
    from agent_room.spec import GraphSpec

    dev: dict = {"role": "developer", "tools": tool_names}
    if mode is not None:
        dev["tool_mode"] = mode
    return GraphSpec.model_validate(
        {
            "name": "policy-test",
            "entry": "developer",
            "nodes": {"developer": dev, "reviewer": {"role": "reviewer"}},
            "edges": [
                {"from": "developer", "to": "reviewer"},
                {"from": "reviewer", "to": "__end__"},
            ],
        }
    )


def _bindings_stub():
    """Minimal RoleBindings — none of the model invocations actually fire in
    these build-time tests, but RoleBindings.resolve() validates wiring."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from agent_room.config import RoleBindings
    from agent_room.schemas import ReviewerDecision
    from tests.fakes import FakeReviewerLLM

    return RoleBindings(
        planner=FakeListChatModel(responses=["1"]),
        developer=FakeListChatModel(responses=["code"]),
        reviewer=FakeReviewerLLM(
            responses=["unused"],
            decisions=[
                ReviewerDecision(decision="approved", feedback="ok", issues=[], confidence=0.9)
            ],
        ),
        delivery=FakeListChatModel(responses=["done"]),
    )


def test_build_from_spec_read_only_filters_write_text(tmp_path):
    """read_only mode passes only read_text to ToolNode even when spec lists writes."""
    from agent_room.graph import build_uncompiled_from_spec

    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))

    spec = _spec_with_dev_tools(
        tool_names=["read_text", "write_text"],
        mode="read_only",
    )
    graph = build_uncompiled_from_spec(spec, _bindings_stub(), registry=reg)
    # Inspect the ToolNode's tools_by_name to confirm what made it through.
    tool_node = graph.nodes["developer_tools"].runnable
    assert set(tool_node.tools_by_name) == {"read_text"}


def test_build_from_spec_unrestricted_keeps_writes(tmp_path):
    from agent_room.graph import build_uncompiled_from_spec

    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path), shell_allowlist=["pytest"])

    spec = _spec_with_dev_tools(
        tool_names=["read_text", "write_text", "shell"],
        mode="unrestricted",
    )
    graph = build_uncompiled_from_spec(spec, _bindings_stub(), registry=reg)
    tool_node = graph.nodes["developer_tools"].runnable
    assert set(tool_node.tools_by_name) == {"read_text", "write_text", "shell"}


def test_build_from_spec_default_mode_is_read_only(tmp_path):
    """tool_mode unset → DEFAULT_MODE → write_text gets filtered."""
    from agent_room.graph import build_uncompiled_from_spec

    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))

    spec = _spec_with_dev_tools(tool_names=["read_text", "write_text"], mode=None)
    graph = build_uncompiled_from_spec(spec, _bindings_stub(), registry=reg)
    tool_node = graph.nodes["developer_tools"].runnable
    assert set(tool_node.tools_by_name) == {"read_text"}


def test_build_from_spec_approval_attaches_wrapper(tmp_path):
    """Approval mode no longer raises — it wires `awrap_tool_call` instead.

    The runtime gate is exercised in `tests/test_tool_approval.py`. Here we
    just prove the wiring: ToolNode keeps every listed tool AND has the
    approval wrapper attached.
    """
    from agent_room.graph import build_uncompiled_from_spec

    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path))

    spec = _spec_with_dev_tools(tool_names=["read_text", "write_text"], mode="approval")
    graph = build_uncompiled_from_spec(spec, _bindings_stub(), registry=reg)
    tool_node = graph.nodes["developer_tools"].runnable
    assert set(tool_node.tools_by_name) == {"read_text", "write_text"}
    assert tool_node._awrap_tool_call is not None


def test_build_from_spec_all_filtered_raises(tmp_path):
    """Listing only write tools under read_only must fail loudly, not silently."""
    from agent_room.graph import build_uncompiled_from_spec

    reg = Registry()
    register_builtin_tools(reg, fs_root=str(tmp_path), shell_allowlist=["pytest"])

    spec = _spec_with_dev_tools(
        tool_names=["write_text", "shell"],
        mode="read_only",
    )
    with pytest.raises(ValueError, match="filtered out every listed tool"):
        build_uncompiled_from_spec(spec, _bindings_stub(), registry=reg)
