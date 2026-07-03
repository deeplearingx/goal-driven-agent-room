"""Path + command safety helpers for the tool layer (RISK-5)."""

from __future__ import annotations

import shlex
from pathlib import Path


def resolve_within_root(root: str | Path, relative: str) -> Path:
    """Resolve `relative` against `root`, refusing paths that escape the root.

    `relative` may be a string with `..` segments, an absolute path, or a
    symlink target — we resolve to the canonical absolute form and then
    confirm it stays under `root`. Both `root` and the result are
    fully-resolved (symlinks followed).

    Raises `ValueError` if the path escapes `root` or `root` doesn't exist.
    """
    root_resolved = Path(root).resolve(strict=False)
    if not root_resolved.exists():
        raise ValueError(f"fs_root does not exist: {root_resolved}")

    # Anchor relative paths under root; absolute paths stay absolute and we
    # simply re-check containment.
    candidate = Path(relative)
    target = (root_resolved / candidate) if not candidate.is_absolute() else candidate
    target = target.resolve(strict=False)

    if not target.is_relative_to(root_resolved):
        raise ValueError(f"path {relative!r} escapes root {root_resolved} (resolved: {target})")
    return target


def enforce_command_allowlist(command: str, allowlist: list[str]) -> list[str]:
    """Parse `command` with `shlex` and verify head token is in `allowlist`.

    Returns the parsed argv. Raises `ValueError` if:
    - `command` is empty after shlex,
    - the head token isn't in `allowlist`,
    - the command contains shell metacharacters that shlex didn't strip
      (we run via `subprocess.run([...])` not `shell=True`, so metacharacters
      are passed through *literally* to the program — but we still reject
      the obvious shell-redirection cases to make the security envelope easy
      to reason about).

    `allowlist` is matched against the head token verbatim — no path stripping.
    Pass `["pytest"]` to allow `pytest`, not `/usr/bin/pytest`.
    """
    if not allowlist:
        raise ValueError("shell tool: allowlist must be non-empty")

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"shell tool: cannot parse command: {exc}") from exc
    if not argv:
        raise ValueError("shell tool: empty command")

    head = argv[0]
    if head not in allowlist:
        raise ValueError(f"shell tool: command {head!r} not in allowlist {allowlist!r}")

    forbidden_tokens = {";", "&&", "||", "|", ">", ">>", "<", "&"}
    for token in argv:
        if token in forbidden_tokens:
            raise ValueError(
                f"shell tool: token {token!r} is a shell separator; "
                "use multiple separate tool calls instead"
            )
    return argv
