"""Agent-room tool system (v0.3).

借鉴 hermes-agent 的 registry 模式（ToolEntry + 线程安全 + 按 toolset 取），
但是本仓库自研，**不 import** hermes-agent 任何东西（ADR-0007）。

公开入口：
- `Registry`、`ToolEntry`、`default_registry()` —— see `registry.py`
- `register_builtin_tools(registry)` —— 注册 4 个内置工具
- 内置工具 class：`ReadTextTool` / `WriteTextTool` / `GlobTool` / `ShellTool`
- 安全 helper：`resolve_within_root`、`enforce_command_allowlist`
"""

from typing import TYPE_CHECKING, Any

from agent_room.guardrail import Guardrail
from agent_room.tools._safety import enforce_command_allowlist, resolve_within_root
from agent_room.tools.fs import GlobTool, ReadTextTool, WriteTextTool
from agent_room.tools.policy import (
    ALL_MODES,
    DEFAULT_MODE,
    PermissionMode,
    apply_policy,
    is_read_only,
)
from agent_room.tools.registry import Registry, ToolEntry, default_registry
from agent_room.tools.sandbox import SandboxExecTool
from agent_room.tools.shell import ShellTool
from agent_room.tools.spec_resolver import resolve_tool_names
from agent_room.tools.spill import (
    DEFAULT_SPILL_THRESHOLD,
    InMemorySpillStore,
    SpillRef,
    SpillStore,
    maybe_spill_tool_message,
    spill_messages,
)

if TYPE_CHECKING:
    from agent_room.memory.file_fts import FileFtsMemoryProvider


def register_builtin_tools(
    registry: Registry,
    *,
    fs_root: str | None = None,
    shell_allowlist: list[str] | None = None,
    shell_timeout_s: float = 30.0,
    shell_cwd: str | None = None,
    memory_provider: "FileFtsMemoryProvider | None" = None,
    guardrail: Guardrail | None = None,
    sandbox_runner_url: str | None = None,
    sandbox_runner_token: str | None = None,
    sandbox_tenant_id: str = "",
    sandbox_task_id: str = "",
) -> None:
    """注册 4 个内置工具到 registry。

    `fs_root`：read/write/glob 的路径根。None 表示走 cwd（仅供测试）。
    `shell_allowlist`：允许的命令头，比如 `["pytest", "ruff", "git"]`。
        None 表示**禁用 ShellTool**（最小权限默认值）。
    `shell_cwd`：ShellTool 的执行目录。None 表示走进程 cwd。生产环境应
        pin 到与 `fs_root` 同一个 workspace，避免命令在仓库/家目录里跑。
    `memory_provider`：v0.5 cross-session memory backend。None 时跳过
        `memory` 工具注册（默认行为，零变化）。传入 `FileFtsMemoryProvider`
        实例后，dev/reviewer LLM 可主动调 `memory(action=..., target=...,
        content=...)` 写入 MEMORY.md / USER.md。
    `guardrail`：§6.9-3 "tool_call" 检查点。None（默认）时 write_text/shell
        不扫描调用内容，零变化。传入 `Guardrail(mode=...)` 后，`content`/
        `command` 会在真正写盘/执行前过一遍威胁扫描。
    """
    fs_kwargs: dict[str, Any] = {"root": fs_root} if fs_root is not None else {}

    registry.register(
        ToolEntry(
            name="read_text",
            toolset="filesystem",
            tool=ReadTextTool(**fs_kwargs),
        )
    )
    registry.register(
        ToolEntry(
            name="write_text",
            toolset="filesystem",
            tool=WriteTextTool(**fs_kwargs, guardrail=guardrail),
        )
    )
    registry.register(
        ToolEntry(
            name="glob",
            toolset="filesystem",
            tool=GlobTool(**fs_kwargs),
        )
    )
    if shell_allowlist:
        registry.register(
            ToolEntry(
                name="shell",
                toolset="shell",
                tool=ShellTool(
                    allowlist=shell_allowlist,
                    timeout_s=shell_timeout_s,
                    cwd=shell_cwd,
                    guardrail=guardrail,
                ),
            )
        )
    if memory_provider is not None:
        # Imported lazily so that registry users without the memory module
        # (or those who explicitly disable it) don't pay the import cost.
        from agent_room.memory.tool import MemoryTool

        registry.register(
            ToolEntry(
                name="memory",
                toolset="memory",
                tool=MemoryTool(provider=memory_provider),
            )
        )
    if sandbox_runner_url and sandbox_runner_token:
        registry.register(
            ToolEntry(
                name="sandbox_exec",
                toolset="sandbox",
                tool=SandboxExecTool(
                    runner_url=sandbox_runner_url,
                    runner_token=sandbox_runner_token,
                    tenant_id=sandbox_tenant_id,
                    task_id=sandbox_task_id,
                ),
            )
        )


__all__ = [
    "ALL_MODES",
    "DEFAULT_MODE",
    "DEFAULT_SPILL_THRESHOLD",
    "GlobTool",
    "InMemorySpillStore",
    "PermissionMode",
    "ReadTextTool",
    "Registry",
    "SandboxExecTool",
    "ShellTool",
    "SpillRef",
    "SpillStore",
    "ToolEntry",
    "WriteTextTool",
    "apply_policy",
    "default_registry",
    "enforce_command_allowlist",
    "is_read_only",
    "maybe_spill_tool_message",
    "register_builtin_tools",
    "resolve_tool_names",
    "resolve_within_root",
    "spill_messages",
]
