"""Server ReAct runtime: spec/registry reconciliation + security envelope.

These cover the default tool-using developer the server wires up (`full_react`),
and prove the envelope holds: workspace-confined fs tools, allowlisted shell
pinned to the workspace, deny-by-default when no allowlist, and spec/registry
staying consistent so a disabled tool can't be referenced by the graph build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.tools import BaseTool

from agent_room.config import Settings, _parse_allowlist, _parse_mcp_servers
from agent_room.memory import FileFtsMemoryProvider, NoOpMemoryProvider
from agent_room.server.react_runtime import (
    build_server_memory,
    build_server_registry,
    build_server_spec,
    describe_tool_envelope,
    load_server_mcp_tools,
    task_workspace,
)


class _FakeMcpTool(BaseTool):
    """Stand-in for a tool `langchain-mcp-adapters` would load from a real MCP
    server — avoids spawning a subprocess/network connection in unit tests."""

    name: str = "fake_mcp_tool"
    description: str = "a tool loaded from an MCP server"

    def _run(self) -> str:
        return "ok"


def _settings(tmp_path: Path, **kw) -> Settings:
    return Settings(workspace_dir=str(tmp_path / "workspace"), **kw)


# --- spec reconciliation -------------------------------------------------


def test_default_spec_developer_is_tool_using(tmp_path: Path):
    spec = build_server_spec(_settings(tmp_path))
    dev = spec.nodes["developer"]
    # Default has memory on, so the developer also gets the `memory` tool.
    assert dev.tools == ["glob", "read_text", "write_text", "memory"]
    assert dev.tool_mode == "read_only"


def test_tool_mode_is_configurable(tmp_path: Path):
    spec = build_server_spec(_settings(tmp_path, tool_mode="approval"))
    assert spec.nodes["developer"].tool_mode == "approval"


def test_empty_allowlist_drops_shell_from_spec(tmp_path: Path):
    """Deny-by-default must keep spec consistent with the registry: with no
    allowlist the registry has no `shell`, so the spec must not list it either
    (otherwise the graph build references a missing tool)."""
    # memory off here to isolate the shell-drop behaviour from the memory tool.
    spec = build_server_spec(_settings(tmp_path, shell_allowlist=(), memory_enabled=False))
    dev = spec.nodes["developer"]
    assert "shell" not in dev.tools
    assert dev.tools == ["glob", "read_text", "write_text"]
    # tool_mode survives because read/write tools remain.
    assert dev.tool_mode == "read_only"


def test_non_react_preset_passthrough(tmp_path: Path):
    spec = build_server_spec(_settings(tmp_path, graph_preset="full"))
    assert spec.nodes["developer"].tools == []


def test_graph_preset_override_selects_goal(tmp_path: Path):
    """Per-request mode selection: `graph_preset=` overrides settings, so the
    server can serve a goal-mode task without an AGENT_ROOM_GRAPH restart."""
    # Server configured for workflow mode...
    settings = _settings(tmp_path, graph_preset="full_react")
    default_spec = build_server_spec(settings)
    assert "verify" not in default_spec.nodes
    # ...but a request can ask for goal mode.
    goal_spec = build_server_spec(settings, graph_preset="goal")
    assert "verify" in goal_spec.nodes
    assert "supervisor" in goal_spec.nodes
    # Developer tools still reconciled with the envelope in the overridden preset.
    assert "shell" not in goal_spec.nodes["developer"].tools


def test_graph_preset_none_uses_settings_default(tmp_path: Path):
    """`graph_preset=None` is the no-override path — identical to omitting it."""
    settings = _settings(tmp_path, graph_preset="goal")
    assert build_server_spec(settings, graph_preset=None).nodes.keys() == (
        build_server_spec(settings).nodes.keys()
    )
    assert "verify" in build_server_spec(settings, graph_preset=None).nodes


def test_solo_preset_passthrough(tmp_path: Path):
    spec = build_server_spec(_settings(tmp_path, graph_preset="solo"))
    assert spec.nodes["developer"].tools == []


# --- registry / envelope -------------------------------------------------


def test_registry_creates_workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    assert not ws.exists()
    build_server_registry(_settings(tmp_path))
    assert ws.is_dir()


def test_task_workspace_rejects_unsafe_segments(tmp_path: Path):
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="task_id"):
        task_workspace(settings, "../other-task")
    with pytest.raises(ValueError, match="tenant_id"):
        task_workspace(settings, "task-1", "../other-tenant")


def test_registry_has_all_tools_with_allowlist(tmp_path: Path):
    reg = build_server_registry(
        _settings(tmp_path, shell_allowlist=("pytest",), allow_direct_shell=True)
    )
    assert sorted(reg.names()) == ["glob", "read_text", "shell", "write_text"]


def test_registry_omits_shell_without_allowlist(tmp_path: Path):
    reg = build_server_registry(_settings(tmp_path, shell_allowlist=()))
    assert "shell" not in reg.names()
    assert sorted(reg.names()) == ["glob", "read_text", "write_text"]


def test_shell_cwd_pinned_to_workspace(tmp_path: Path):
    settings = _settings(
        tmp_path, shell_allowlist=("pytest",), allow_direct_shell=True
    )
    reg = build_server_registry(settings)
    shell = reg.get("shell").tool
    assert shell.cwd == str((tmp_path / "workspace").resolve())
    assert shell.allowlist == list(settings.shell_allowlist)


def test_fs_tools_rooted_at_workspace(tmp_path: Path):
    reg = build_server_registry(_settings(tmp_path))
    ws = str((tmp_path / "workspace").resolve())
    for name in ("read_text", "write_text", "glob"):
        assert reg.get(name).tool.root == ws


def test_write_tool_rejects_traversal_escape(tmp_path: Path):
    """The confinement is real, not cosmetic: a `..` escape is refused by the
    workspace-rooted tool the server actually registers."""
    reg = build_server_registry(_settings(tmp_path))
    write = reg.get("write_text").tool
    with pytest.raises(ValueError, match="escapes root"):
        write.invoke({"path": "../leak.txt", "content": "leak"})


def test_write_tool_stays_inside_workspace(tmp_path: Path):
    reg = build_server_registry(_settings(tmp_path))
    write = reg.get("write_text").tool
    write.invoke({"path": "ok.txt", "content": "fine"})
    assert (tmp_path / "workspace" / "ok.txt").read_text() == "fine"


def test_describe_envelope_reports_active_config(tmp_path: Path):
    env = describe_tool_envelope(_settings(tmp_path))
    assert env["graph_preset"] == "full_react"
    assert env["shell_enabled"] is False
    assert env["direct_shell_escape_hatch"] is False
    assert env["tool_mode"] == "read_only"
    assert env["workspace_dir"] == str((tmp_path / "workspace").resolve())


def test_describe_envelope_shell_disabled(tmp_path: Path):
    env = describe_tool_envelope(_settings(tmp_path, shell_allowlist=()))
    assert env["shell_enabled"] is False
    assert env["shell_allowlist"] == []


# --- cross-session memory enablement -------------------------------------


def test_memory_provider_enabled_by_default(tmp_path: Path):
    assert isinstance(build_server_memory(_settings(tmp_path)), FileFtsMemoryProvider)


def test_memory_provider_disabled_is_noop(tmp_path: Path):
    mem = build_server_memory(_settings(tmp_path, memory_enabled=False))
    assert isinstance(mem, NoOpMemoryProvider)


def test_memory_enabled_adds_memory_tool_to_spec(tmp_path: Path):
    dev = build_server_spec(_settings(tmp_path)).nodes["developer"]
    assert "memory" in dev.tools


def test_memory_disabled_omits_memory_tool_from_spec(tmp_path: Path):
    dev = build_server_spec(_settings(tmp_path, memory_enabled=False)).nodes["developer"]
    assert "memory" not in dev.tools


def test_memory_tool_registered_against_shared_provider(tmp_path: Path):
    """The registry's `memory` tool must dispatch to the exact provider the
    bindings hold, or a write wouldn't surface next session."""
    settings = _settings(tmp_path)
    provider = build_server_memory(settings)
    assert isinstance(provider, FileFtsMemoryProvider)
    reg = build_server_registry(settings, memory_provider=provider)
    assert "memory" in reg.names()
    assert reg.get("memory").tool.provider is provider


def test_memory_tool_absent_when_disabled(tmp_path: Path):
    reg = build_server_registry(_settings(tmp_path, memory_enabled=False))
    assert "memory" not in reg.names()


def test_memory_survives_shell_disabled(tmp_path: Path):
    dev = build_server_spec(_settings(tmp_path, shell_allowlist=())).nodes["developer"]
    assert dev.tools == ["glob", "read_text", "write_text", "memory"]


def test_envelope_reports_memory_state(tmp_path: Path):
    assert describe_tool_envelope(_settings(tmp_path))["memory_enabled"] is True
    off = describe_tool_envelope(_settings(tmp_path, memory_enabled=False))
    assert off["memory_enabled"] is False


# --- hybrid vector recall (v1.x §6.9-4) ------------------------------------


def test_memory_vector_off_by_default(tmp_path: Path):
    env = describe_tool_envelope(_settings(tmp_path))
    assert env["memory_vector"] is False
    assert env["memory_vector_backend"] == "noop"


def test_memory_vector_on_reports_hashing_backend(tmp_path: Path):
    env = describe_tool_envelope(_settings(tmp_path, memory_vector=True))
    assert env["memory_vector"] is True
    assert env["memory_vector_backend"] == "hashing"


def test_memory_vector_ignored_when_memory_disabled(tmp_path: Path):
    """`memory_vector` only matters if cross-session memory itself is on."""
    env = describe_tool_envelope(_settings(tmp_path, memory_vector=True, memory_enabled=False))
    assert env["memory_vector"] is False


def test_build_server_memory_wires_embedding_backend(tmp_path: Path):
    provider = build_server_memory(_settings(tmp_path, memory_vector=True))
    assert isinstance(provider, FileFtsMemoryProvider)
    assert provider._transcript._embedding is not None
    assert provider._transcript._embedding.name == "hashing"


def test_build_server_memory_no_vector_by_default(tmp_path: Path):
    provider = build_server_memory(_settings(tmp_path))
    assert isinstance(provider, FileFtsMemoryProvider)
    assert provider._transcript._embedding is None
    assert provider._transcript._vector is None


# --- MCP tool wiring -------------------------------------------------------


@pytest.mark.asyncio
async def test_load_server_mcp_tools_empty_by_default(tmp_path: Path):
    """No AGENT_ROOM_MCP_SERVERS configured → no MCP client connection attempted,
    zero behavior change (matches the NoOp-default shape of memory/shell)."""
    assert await load_server_mcp_tools(_settings(tmp_path)) == []


def test_mcp_tools_registered_under_mcp_toolset(tmp_path: Path):
    reg = build_server_registry(_settings(tmp_path), mcp_tools=[_FakeMcpTool()])
    entry = reg.get("fake_mcp_tool")
    assert entry.toolset == "mcp"
    assert "fake_mcp_tool" in reg.names()


def test_mcp_tool_names_added_to_developer_spec(tmp_path: Path):
    spec = build_server_spec(_settings(tmp_path), mcp_tool_names=("fake_mcp_tool",))
    dev = spec.nodes["developer"]
    assert "fake_mcp_tool" in dev.tools
    assert dev.tool_mode == "read_only"


def test_direct_shell_requires_explicit_escape_hatch(tmp_path: Path):
    with pytest.raises(ValueError, match="direct shell is disabled"):
        build_server_registry(_settings(tmp_path, shell_allowlist=("python",)))


def test_no_mcp_tool_names_means_no_mcp_tools_in_spec(tmp_path: Path):
    spec = build_server_spec(_settings(tmp_path))
    assert "fake_mcp_tool" not in spec.nodes["developer"].tools


def test_mcp_tools_survive_shell_disabled(tmp_path: Path):
    """MCP tools are independent of the shell allowlist envelope."""
    spec = build_server_spec(
        _settings(tmp_path, shell_allowlist=()), mcp_tool_names=("fake_mcp_tool",)
    )
    dev = spec.nodes["developer"]
    assert "shell" not in dev.tools
    assert "fake_mcp_tool" in dev.tools


def test_envelope_reports_mcp_servers_and_tools(tmp_path: Path):
    settings = Settings(
        workspace_dir=str(tmp_path / "workspace"),
        mcp_servers={"fs": {"command": "npx", "args": [], "transport": "stdio"}},
    )
    env = describe_tool_envelope(settings, mcp_tool_names=("read_file", "write_file"))
    assert env["mcp_servers"] == ["fs"]
    assert env["mcp_tools"] == ["read_file", "write_file"]


def test_envelope_mcp_empty_by_default(tmp_path: Path):
    env = describe_tool_envelope(_settings(tmp_path))
    assert env["mcp_servers"] == []
    assert env["mcp_tools"] == []


def test_sandbox_tool_is_opt_in_and_bound_to_trusted_task_context(tmp_path: Path):
    settings = _settings(
        tmp_path,
        sandbox_runner_url="https://runner.internal",
        sandbox_runner_token="secret",
        tool_mode="approval",
    )
    spec = build_server_spec(settings)
    assert "sandbox_exec" in spec.nodes["developer"].tools
    assert spec.nodes["developer"].tool_mode == "approval"
    registry = build_server_registry(
        settings, sandbox_tenant_id="tenant-a", sandbox_task_id="task-1"
    )
    tool = registry.get("sandbox_exec").tool
    assert tool.tenant_id == "tenant-a"
    assert tool.task_id == "task-1"
    envelope = describe_tool_envelope(settings)
    assert envelope["sandbox_runner_enabled"] is True


def test_partial_sandbox_configuration_fails_closed(tmp_path: Path):
    settings = _settings(tmp_path, sandbox_runner_url="https://runner.internal")
    assert "sandbox_exec" not in build_server_spec(settings).nodes["developer"].tools
    assert "sandbox_exec" not in build_server_registry(settings).names()


# --- env parsing ---------------------------------------------------------


def test_parse_allowlist_unset_uses_default():
    assert _parse_allowlist(None) == ()


def test_parse_allowlist_empty_disables_shell():
    assert _parse_allowlist("") == ()


def test_parse_allowlist_trims_and_splits():
    assert _parse_allowlist("pytest, ruff , python") == ("pytest", "ruff", "python")


def test_parse_mcp_servers_unset_is_empty():
    assert _parse_mcp_servers(None) == {}
    assert _parse_mcp_servers("") == {}


def test_parse_mcp_servers_valid_json():
    raw = '{"fs": {"command": "npx", "args": ["-y", "pkg"], "transport": "stdio"}}'
    assert _parse_mcp_servers(raw) == {
        "fs": {"command": "npx", "args": ["-y", "pkg"], "transport": "stdio"}
    }


def test_parse_mcp_servers_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        _parse_mcp_servers("[1, 2, 3]")


def test_parse_mcp_servers_rejects_malformed_json():
    with pytest.raises(json.JSONDecodeError):
        _parse_mcp_servers("not json")
