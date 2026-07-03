"""Task suites for the eval harness.

The harness mechanism lives in `agent_room.eval`; this package holds the task
*data* — concrete bug-fix / implementation problems with their seed files and
oracles. Kept separate so the shipped package stays generic.
"""

from __future__ import annotations

from evals.coding_tasks import CODING_TASKS

__all__ = ["CODING_TASKS"]
