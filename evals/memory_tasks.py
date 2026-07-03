"""Memory ablation task (EVAL-2 memory axis).

The trick to a *strictly* memory-determined task: the correct value exists
**only** in memory, and the check is a HIDDEN test (written at verify time, so
the developer never sees it). A `FileFtsMemoryProvider` injects the seeded
marker into the system prompt → the developer recalls it; `NoOpMemoryProvider`
can't → it guesses and fails the hidden check.

Memory lives under a shared dir so the task's seed and the variant's provider
(initialized by `sqlite_graph_builder` via the db's parent) point at the same
place. Curated reads `<root>/memory/MEMORY.md` (see `memory/curated.py`).
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from agent_room.eval.coding import CodingTask
from agent_room.schemas import TaskRequest

MEMORY_EVAL_DIR = Path(tempfile.gettempdir()) / "agent_room_memory_eval"
MEMORY_EVAL_DB = str(MEMORY_EVAL_DIR / "mem.db")

_SECRET_MARKER = "XK7P-2291-QF"


@dataclass
class MemoryRecallTask(CodingTask):
    """A CodingTask that also seeds a fact into curated memory before the run."""

    memory_fact: str = ""

    async def setup(self) -> None:
        await super().setup()  # seeds the code sandbox
        memdir = MEMORY_EVAL_DIR / "memory"
        memdir.mkdir(parents=True, exist_ok=True)
        (memdir / "MEMORY.md").write_text(self.memory_fact, encoding="utf-8")

    async def teardown(self) -> None:
        await super().teardown()
        shutil.rmtree(MEMORY_EVAL_DIR, ignore_errors=True)


MEMORY_TASKS: list[MemoryRecallTask] = [
    MemoryRecallTask(
        name="recall_secret_marker",
        request=TaskRequest(
            title="Implement marker() returning the project secret marker",
            description=(
                "Implement `marker() -> str` in marker.py so it returns the project's "
                "secret marker string. The marker is a fixed project convention recorded "
                "in your memory — do NOT invent one; recall and return the EXACT value. "
                "Run pytest to confirm marker() returns a non-empty string, then emit your "
                "answer. Do NOT modify any test file."
            ),
            max_revisions=2,
        ),
        files={
            "marker.py": "def marker() -> str:\n    raise NotImplementedError\n",
            "test_marker.py": dedent(
                """\
                from marker import marker


                def test_returns_nonempty():
                    value = marker()
                    assert isinstance(value, str) and value
                """
            ),
        },
        hidden_tests={
            "test_marker_hidden.py": dedent(
                f"""\
                from marker import marker


                def test_exact_marker():
                    assert marker() == "{_SECRET_MARKER}"
                """
            ),
        },
        memory_fact=(
            f"Project convention: the secret marker is `{_SECRET_MARKER}`. "
            f"marker() must return exactly this value."
        ),
        notes="Only memory carries the exact marker; the unseen hidden test checks it.",
        tags=["memory", "recall"],
    ),
]
