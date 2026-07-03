"""ADR-0007 hard-test: agent_room must work without hermes-agent.

This is the runtime sibling of the static grep checks (CI step
`independence` + pre-commit `agent-room-independence`). Static grep catches
the obvious `from`/`import` lines naming `hermes_X`; this test catches
anything the grep can't see — lazy imports inside function bodies,
`importlib.import_module` of computed names, sys.path-injected loads.
See PLAN.md §12.4.

Strategy:
- Spawn a fresh Python subprocess running `tests._independence_driver`.
- The driver installs a `MetaPathFinder` that refuses to resolve any
  `hermes_*` import, then walks every submodule of `agent_room` via
  `pkgutil.walk_packages` and force-imports each one.
- A single transitive load of, say, `hermes_state` anywhere in the package
  tree surfaces as a tagged ImportError before this test exits.

The subprocess is mandatory: by the time pytest is running, the parent
process has already imported `agent_room`, so any blocker we install in
`sys.meta_path` here would be too late.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_agent_room_has_no_hermes_imports():
    """Force-import every agent_room submodule with hermes_* blocked.

    Failure modes this guards:
    - Direct hermes_X imports (also caught by grep).
    - Lazy / nested imports inside function bodies (NOT caught by grep
      if the import line happens to be evaluated).
    - `importlib.import_module("hermes_X")` (NOT caught by grep — no literal).
    - Indirect re-exports via `__getattr__` that trigger hermes loads.

    Submodule count is intentionally NOT asserted: it grows naturally as
    the codebase grows (e.g. v0.4 §4.1 added `agent_room.context*` →
    32 → 34). All we care about is that every reachable submodule
    imports clean under the blocker.

    This is the runtime side of ADR-0007 §"验证（硬指标）" item 3.
    """
    result = subprocess.run(
        [sys.executable, "-m", "tests._independence_driver"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "ADR-0007 independence hard-test failed.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "INDEPENDENCE OK" in result.stdout, result.stdout
