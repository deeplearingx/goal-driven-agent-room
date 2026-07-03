"""Tool registry — 借鉴 hermes-agent 的 ToolEntry + RLock 模式，本仓库自研。

设计取舍：
- hermes 用 AST 自动发现 + module-import-time 注册。我们工具数量小（v0.3 起 4 个），
  显式 `register_builtin_tools()` 调用更可读，省掉 AST 扫描和顺序敏感 import 的复杂度。
- hermes 的 `availability_check` / `toolset` / `model_tools binding` 都保留概念，但本版
  只实现 `toolset`（按预设拉取一组工具）；其余等真实需求出现再加。
- 线程安全：`RLock` + 快照拷贝。注册期一般在启动时一次性完成，读期高频。
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """A registered tool: a LangChain BaseTool plus metadata.

    `name` is the identifier the LLM sees and the registry uses for lookup.
    `toolset` groups tools (e.g., "filesystem", "shell") so callers can pull
    a category at once: `registry.entries(toolset="filesystem")`.
    """

    name: str
    toolset: str
    tool: BaseTool


class Registry:
    """Thread-safe tool registry.

    Typical use:
        reg = Registry()
        register_builtin_tools(reg, fs_root="/tmp/work")
        tools = reg.entries(toolset="filesystem")  # for a dev that only reads/writes
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        with self._lock:
            if entry.name in self._entries:
                raise ValueError(f"tool {entry.name!r} already registered")
            self._entries[entry.name] = entry

    def get(self, name: str) -> ToolEntry:
        with self._lock:
            try:
                return self._entries[name]
            except KeyError as exc:
                raise KeyError(f"unknown tool: {name!r}") from exc

    def entries(self, *, toolset: str | None = None) -> list[ToolEntry]:
        """Return a snapshot of registered entries, optionally filtered by toolset."""
        with self._lock:
            snapshot = list(self._entries.values())
        if toolset is None:
            return snapshot
        return [e for e in snapshot if e.toolset == toolset]

    def names(self) -> list[str]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        """Drop all entries. Mainly for tests."""
        with self._lock:
            self._entries.clear()


_default = Registry()


def default_registry() -> Registry:
    """Return the process-wide default registry singleton."""
    return _default
