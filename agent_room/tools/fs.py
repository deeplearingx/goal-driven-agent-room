"""Filesystem tools: ReadTextTool / WriteTextTool / GlobTool.

All three are LangChain `BaseTool` subclasses (sync `_run`; async path falls
back to `_run` via the BaseTool default). They take a `root: str | None` —
None means "use cwd", which is fine for tests but should be set to a project
root in production.

Path safety: every input goes through `resolve_within_root`, which canonicalizes
the path (resolving `..` and symlinks) and refuses anything that escapes `root`.

Output safety: read/glob output is truncated to ~`max_bytes` to keep prompts
sane. Truncation is marked with a trailing `\n[... truncated, N more bytes]`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agent_room.guardrail import Guardrail
from agent_room.tools._safety import resolve_within_root

DEFAULT_MAX_BYTES = 8 * 1024  # 8 KB per call — enough for code review, small enough for prompt
DEFAULT_MAX_GLOB = 200  # at most 200 matches before we truncate


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    head = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{head}\n[... truncated, {len(encoded) - max_bytes} more bytes]"


class _RootedTool(BaseTool):
    """Mixin: tools accept an optional `root` and resolve paths under it."""

    root: str = Field(default_factory=os.getcwd)

    def _safe_path(self, relative: str) -> Path:
        return resolve_within_root(self.root, relative)


class ReadTextInput(BaseModel):
    path: str = Field(description="Path to read, relative to fs root or absolute under root")


class ReadTextTool(_RootedTool):
    name: str = "read_text"
    description: str = (
        "Read a UTF-8 text file under the project root. "
        "Returns the file contents (truncated to 8 KB if larger). "
        "Use this to inspect existing code before editing."
    )
    args_schema: ClassVar[type[BaseModel]] = ReadTextInput
    max_bytes: int = DEFAULT_MAX_BYTES

    def _run(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"no such file: {path}")
        if not target.is_file():
            raise ValueError(f"not a regular file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        return _truncate(text, self.max_bytes)


class WriteTextInput(BaseModel):
    path: str = Field(description="Path to write, relative to fs root or absolute under root")
    content: str = Field(description="UTF-8 text content to write")
    create_dirs: bool = Field(
        default=False,
        description="Create parent directories if they don't exist",
    )


class WriteTextTool(_RootedTool):
    name: str = "write_text"
    description: str = (
        "Write UTF-8 text to a file under the project root, overwriting any existing content. "
        "Returns the bytes written. Set create_dirs=true if parent directories don't exist."
    )
    args_schema: ClassVar[type[BaseModel]] = WriteTextInput
    # §6.9-3 "tool_call" checkpoint — scans `content` before it leaves the
    # sandbox onto disk. `None` (default) = no scan, zero behavior change.
    guardrail: Guardrail | None = None

    def _run(self, path: str, content: str, create_dirs: bool = False) -> str:
        if self.guardrail is not None:
            self.guardrail.check(content, checkpoint="tool_call")
        target = self._safe_path(path)
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.exists():
            raise FileNotFoundError(
                f"parent directory missing: {target.parent} (use create_dirs=true)"
            )
        encoded = content.encode("utf-8")
        target.write_bytes(encoded)
        return f"wrote {len(encoded)} bytes to {path}"


class GlobInput(BaseModel):
    pattern: str = Field(
        description="Glob pattern, e.g. '**/*.py' or 'tests/test_*.py'. Relative to fs root."
    )


class GlobTool(_RootedTool):
    name: str = "glob"
    description: str = (
        "List files matching a glob pattern under the project root. "
        "Returns up to 200 paths, one per line, sorted lexicographically. "
        "Use this to discover files before reading or editing."
    )
    args_schema: ClassVar[type[BaseModel]] = GlobInput
    max_results: int = DEFAULT_MAX_GLOB

    def _run(self, pattern: str) -> str:
        # `Path.glob` accepts relative patterns; we anchor on root.
        root = Path(self.root).resolve(strict=False)
        if not root.exists():
            raise ValueError(f"fs_root does not exist: {root}")

        # Reject absolute glob patterns: pathlib.Path.glob refuses them anyway,
        # but a clear error message is more helpful than NotImplementedError.
        if pattern.startswith("/"):
            raise ValueError(f"glob pattern must be relative: {pattern!r}")

        matches = sorted(root.glob(pattern))
        # Defense in depth: even though Path.glob shouldn't escape root, drop
        # anything that does (could happen with symlinks pointing outward).
        matches = [m for m in matches if m.resolve(strict=False).is_relative_to(root)]

        truncated = matches[: self.max_results]
        rels = [str(m.relative_to(root)) for m in truncated]
        suffix = ""
        if len(matches) > self.max_results:
            suffix = f"\n[... truncated, {len(matches) - self.max_results} more matches]"
        return "\n".join(rels) + suffix
