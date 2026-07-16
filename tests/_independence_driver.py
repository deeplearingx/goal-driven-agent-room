"""Subprocess-side driver for the ADR-0007 independence hard-test.

Run as `python -m tests._independence_driver`. Designed to be invoked from
[`test_independence.py`](test_independence.py) in a fresh interpreter so the
import-blocking `MetaPathFinder` is installed *before* `agent_room` is loaded
(otherwise the parent test process's existing `agent_room` import would mask
any leak).

What it does:

1. Install a finder at `sys.meta_path[0]` that raises on any `hermes_*`
   top-level import (matches the patterns ADR-0007 forbids).
2. Walk every submodule of `agent_room` via `pkgutil.walk_packages` and force
   `importlib.import_module(...)` on each one — the blocker fires on the first
   transitive hermes_* load it encounters, even if the import is lazy / nested
   inside a function we never normally call.
3. Exit 0 on success, non-zero with a structured error on leak.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from typing import Any


class _HermesBlocker(MetaPathFinder):
    """Refuses to resolve any `hermes_*` top-level import.

    Installed at sys.meta_path[0] before agent_room is touched so the very
    first nested hermes_* load (e.g. `hermes_state`) raises ImportError
    with a tagged message we can grep for in CI logs.
    """

    @staticmethod
    def _is_hermes(fullname: str) -> bool:
        head = fullname.split(".", 1)[0]
        return head.startswith("hermes_") or head == "hermes_agent"

    def find_spec(
        self,
        fullname: str,
        path: Any | None = None,
        target: Any | None = None,
    ) -> ModuleSpec | None:
        if self._is_hermes(fullname):
            raise ImportError(
                f"ADR-0007 violation: agent_room transitively imported "
                f"forbidden module {fullname!r}"
            )
        return None


def _walk_agent_room() -> list[str]:
    pkg = importlib.import_module("agent_room")
    names: list[str] = ["agent_room"]
    for info in pkgutil.walk_packages(pkg.__path__, prefix="agent_room."):
        names.append(info.name)
    return names


def main() -> int:
    sys.meta_path.insert(0, _HermesBlocker())
    failures: list[tuple[str, str]] = []
    try:
        names = _walk_agent_room()
    except ImportError as exc:
        sys.stderr.write(f"FAIL: importing agent_room itself: {exc}\n")
        return 2

    for name in names:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            failures.append((name, str(exc)))
        except Exception as exc:
            tb = traceback.format_exc(limit=4)
            failures.append((name, f"non-import error: {exc!r}\n{tb}"))

    if failures:
        sys.stderr.write(
            "INDEPENDENCE FAIL: agent_room submodules failed import under hermes-blocker:\n"
        )
        for name, err in failures:
            sys.stderr.write(f"  - {name}: {err}\n")
        return 1

    sys.stdout.write(f"INDEPENDENCE OK: {len(names)} submodules clean\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
