"""Mode A/B tasks: workflow (subjective review) vs goal (objective verify loop).

Each task is deliberately *underspecified*: the description says "standard
behavior" while the acceptance check enforces a legitimate-but-unusual
convention (lower median, round-half-away-from-zero, drop the tail chunk...).
The natural implementation passes any subjective review — it looks correct —
but fails the convention. That is the discriminator between the two modes:

- workflow: developer ships the natural reading, the reviewer (who has no
  oracle either) approves it, the hidden suite fails.
- goal: the verifier runs `check.py` after every developer pass; the failure
  tail (with a NOTE naming the convention) is transcript-pushed back, so the
  loop converges on the convention the acceptance check actually wants.

Fairness notes:
- Both variants receive the SAME TaskRequest. `verify_command`/`verify_files`
  are inert on the workflow graph (no verifier node) — the executable
  acceptance check is goal mode's defining contract, not an information leak.
- Budgets are equal: max_revisions=6 (workflow's reviewer loop) vs
  max_iterations=6 (goal's verify loop), so the axis under test is
  subjective-vs-objective judgment, not iteration budget.
- `check.py` and the hidden pytest suite are generated from the same case
  list, so the oracle the goal loop converges on and the oracle that scores
  both variants cannot drift apart.
- `test_command` runs ONLY `test_hidden.py`: a developer-authored test file
  (written to the natural convention) must not pollute the verdict.
"""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from agent_room.eval import CodingTask
from agent_room.schemas import TaskRequest

Case = tuple[tuple[Any, ...], Any]


def _check_script(func: str, cases: list[Case], note: str) -> str:
    """Executable acceptance check (goal mode's verify_command target)."""

    return dedent(
        f"""\
        import sys

        from solution import {func}

        CASES = {cases!r}
        bad = []
        for args, expected in CASES:
            try:
                got = {func}(*args)
            except Exception as exc:
                bad.append((args, expected, f"raised {{type(exc).__name__}}: {{exc}}"))
                continue
            if got != expected:
                bad.append((args, expected, repr(got)))
        for args, expected, got in bad:
            print(f"FAIL {func}{{args!r}}: expected {{expected!r}} got {{got}}")
        if bad:
            print({note!r})
        print("all pass" if not bad else f"{{len(bad)}} failures")
        sys.exit(0 if not bad else 1)
        """
    )


def _hidden_test(func: str, cases: list[Case]) -> str:
    """Hidden pytest suite scoring BOTH variants — same cases as check.py."""

    return dedent(
        f'''\
        from solution import {func}

        CASES = {cases!r}


        def test_hidden_convention():
            for args, expected in CASES:
                assert {func}(*args) == expected, f"{func}(*{{args!r}}) != {{expected!r}}"
        '''
    )


def _mode_task(
    *,
    name: str,
    title: str,
    what: str,
    func: str,
    cases: list[Case],
    note: str,
    notes: str = "",
) -> CodingTask:
    description = (
        f"Implement {what} in `solution.py` at the workspace root (a stub file "
        "exists). Use the tools to write the file. Standard behavior. Keep it simple."
    )
    return CodingTask(
        name=name,
        request=TaskRequest(
            title=title,
            description=description,
            max_revisions=6,
            verify_command="python check.py",
            verify_files={"check.py": _check_script(func, cases, note)},
            max_iterations=6,
        ),
        files={"solution.py": f"# Implement {func} here.\n"},
        hidden_tests={"test_hidden.py": _hidden_test(func, cases)},
        test_command=("-m", "pytest", "-q", "test_hidden.py"),
        notes=notes,
        tags=["mode-ab", "underspec-convention"],
    )


MODE_TASKS: list[CodingTask] = [
    _mode_task(
        name="median_lower",
        title="median function",
        what="a function `median(nums)` that returns the median of a non-empty list of numbers",
        func="median",
        cases=[
            (([5],), 5),
            (([1, 2, 3],), 2),
            (([3, 1, 2],), 2),
            (([1, 2, 3, 4],), 2),
            (([10, 20, 30, 40],), 20),
            (([7, 7, 7, 7],), 7),
            (([1, 2, 3, 4, 5, 6],), 3),
        ],
        note=(
            "NOTE: for even-length lists this project takes the LOWER of the two "
            "middle values, not their average."
        ),
        notes="Natural median averages the middles; the convention takes the lower.",
    ),
    _mode_task(
        name="round_half_away",
        title="round to nearest int",
        what="a function `round_to_int(x)` that rounds a number to the nearest integer",
        func="round_to_int",
        cases=[
            ((2.5,), 3),
            ((3.5,), 4),
            ((-2.5,), -3),
            ((2.4,), 2),
            ((2.6,), 3),
            ((-0.5,), -1),
            ((0.0,), 0),
            ((7.0,), 7),
        ],
        note=(
            "NOTE: halves round AWAY FROM ZERO in this project (2.5 -> 3, "
            "-2.5 -> -3), not banker's rounding."
        ),
        notes="Python round() is banker's (2.5 -> 2); the convention is half-away-from-zero.",
    ),
    _mode_task(
        name="chunk_drop_tail",
        title="chunk a list",
        what="a function `chunk(items, size)` that splits a list into chunks of the given size",
        func="chunk",
        cases=[
            (([1, 2, 3, 4, 5, 6], 2), [[1, 2], [3, 4], [5, 6]]),
            (([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4]]),
            (([1, 2, 3], 5), []),
            (([], 3), []),
            (([1, 2, 3, 4, 5, 6, 7, 8], 3), [[1, 2, 3], [4, 5, 6]]),
        ],
        note="NOTE: incomplete trailing chunks are DROPPED, not kept.",
        notes="Natural chunking keeps the partial tail; the convention drops it.",
    ),
    _mode_task(
        name="dedupe_keep_last",
        title="dedupe a list",
        what="a function `dedupe(items)` that removes duplicates from a list",
        func="dedupe",
        cases=[
            (([1, 2, 1, 3],), [2, 1, 3]),
            (([1, 1, 1],), [1]),
            (([1, 2, 3],), [1, 2, 3]),
            (([3, 1, 2, 3],), [1, 2, 3]),
            ((["a", "b", "a"],), ["b", "a"]),
            (([],), []),
        ],
        note=(
            "NOTE: when a value appears multiple times, keep its LAST occurrence "
            "(output ordered by last occurrence), not the first."
        ),
        notes="Natural dedupe keeps first occurrences; the convention keeps the last.",
    ),
    _mode_task(
        name="slugify_underscore",
        title="slugify a title",
        what="a function `slugify(text)` that turns a title into a URL slug",
        func="slugify",
        cases=[
            (("Hello World",), "hello_world"),
            (("  Spaces  Everywhere ",), "spaces_everywhere"),
            (("Rock & Roll!",), "rock_roll"),
            (("Already_snake",), "already_snake"),
            (("A--B--C",), "a_b_c"),
            (("Multiple   spaces",), "multiple_spaces"),
        ],
        note=(
            "NOTE: this project's slugs join words with UNDERSCORES (_), not "
            "hyphens; every other non-alphanumeric character is a separator."
        ),
        notes="Natural slugify joins with hyphens; the convention is underscores.",
    ),
    _mode_task(
        name="parse_ints_skip",
        title="parse comma-separated ints",
        what=(
            "a function `parse_ints(text)` that parses a comma-separated string "
            "of integers into a list"
        ),
        func="parse_ints",
        cases=[
            (("1,2,3",), [1, 2, 3]),
            (("1, 2, 3",), [1, 2, 3]),
            (("1,a,2",), [1, 2]),
            (("",), []),
            (("4,,5",), [4, 5]),
            ((" 7 ",), [7]),
            (("x,y",), []),
        ],
        note="NOTE: tokens that are not valid integers are silently SKIPPED, not errors.",
        notes="Natural parse raises on bad tokens; the convention skips them.",
    ),
]
