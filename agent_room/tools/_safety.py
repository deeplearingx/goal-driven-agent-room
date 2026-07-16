"""Path + command safety helpers for the tool layer (RISK-5)."""

from __future__ import annotations

import shlex
from pathlib import Path


def resolve_within_root(root: str | Path, relative: str) -> Path:
    """Resolve `relative` against `root`, refusing paths outside `root`."""
    root_resolved = Path(root).resolve(strict=False)
    if not root_resolved.exists():
        raise ValueError(f"fs_root does not exist: {root_resolved}")
    candidate = Path(relative)
    target = (root_resolved / candidate) if not candidate.is_absolute() else candidate
    target = target.resolve(strict=False)
    if not target.is_relative_to(root_resolved):
        raise ValueError(f"path {relative!r} escapes root {root_resolved} (resolved: {target})")
    return target


def enforce_command_allowlist(command: str, allowlist: list[str]) -> list[str]:
    """Parse a command and require its executable to match the allowlist.

    POSIX ``shlex`` treats every backslash as an escape, so parsing a Windows
    executable such as ``C:\\Python\\python.exe`` as one whole string corrupts
    the head token. Match an allowlisted head verbatim first, then use shlex
    only for the argument tail. This preserves Windows paths without invoking
    a shell or weakening the existing separator checks.
    """
    if not allowlist:
        raise ValueError("shell tool: allowlist must be non-empty")

    stripped = command.strip()
    if not stripped:
        raise ValueError("shell tool: empty command")

    head = _match_allowlisted_head(stripped, allowlist)
    try:
        if head is None:
            argv = shlex.split(stripped)
        else:
            tail = stripped[len(head) :].lstrip()
            argv = [head, *shlex.split(tail)] if tail else [head]
    except ValueError as exc:
        raise ValueError(f"shell tool: cannot parse command: {exc}") from exc
    if not argv:
        raise ValueError("shell tool: empty command")

    if argv[0] not in allowlist:
        raise ValueError(f"shell tool: command {argv[0]!r} not in allowlist {allowlist!r}")

    forbidden_tokens = {";", "&&", "||", "|", ">", ">>", "<", "&"}
    for token in argv:
        if token in forbidden_tokens:
            raise ValueError(
                f"shell tool: token {token!r} is a shell separator; "
                "use multiple separate tool calls instead"
            )
    return argv


def _match_allowlisted_head(command: str, allowlist: list[str]) -> str | None:
    """Return the longest verbatim allowlist entry at the command boundary."""
    matches = [
        item
        for item in allowlist
        if command == item or (command.startswith(item) and command[len(item)].isspace())
    ]
    return max(matches, key=len, default=None)
