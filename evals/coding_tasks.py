"""Seed coding tasks (EVAL-1b).

Each task seeds a sandbox with a deliberately broken (or unimplemented) module
plus a test suite that pins the required behaviour. A run passes iff the
developer ships code that makes the suite go green. Tests are the spec — the
prompt tells the model what to do, the suite decides whether it did.

The suite intentionally mixes easy visible tests with hidden edge cases so the
eval can detect overfitting to the public pytest output.
"""

from __future__ import annotations

from textwrap import dedent

from agent_room.eval import CodingTask
from agent_room.schemas import TaskRequest

_FIX_INSTRUCTIONS = (
    "There is a tiny Python project at the filesystem root. `pytest` currently "
    "fails. Use the tools to discover the layout, read the relevant files, fix "
    "the bug in the source (NOT the tests), and run `pytest` to confirm every "
    "test passes before emitting your final answer. Do NOT modify any test file."
)

_IMPL_INSTRUCTIONS = (
    "There is a tiny Python project at the filesystem root with a failing test "
    "suite describing a function that is not yet implemented. Use the tools to "
    "read the tests, implement the function in the source file so every test "
    "passes, and run `pytest` to confirm. Do NOT modify any test file."
)


CODING_TASKS: list[CodingTask] = [
    CodingTask(
        name="fix_add_subtracts",
        request=TaskRequest(
            title="Fix the failing add() in calc.py",
            description=_FIX_INSTRUCTIONS,
            max_revisions=2,
        ),
        files={
            "calc.py": dedent(
                """\
                def add(a: int, b: int) -> int:
                    # Bug: subtracts instead of adding.
                    return a - b


                def mul(a: int, b: int) -> int:
                    return a * b
                """
            ),
            "test_calc.py": dedent(
                """\
                from calc import add, mul


                def test_add_basic():
                    assert add(2, 3) == 5


                def test_add_negative():
                    assert add(-1, 1) == 0


                def test_mul_basic():
                    assert mul(4, 5) == 20
                """
            ),
        },
        notes="Single-line arithmetic bug; the simplest discriminator.",
        tags=["bugfix", "easy"],
    ),
    CodingTask(
        name="fix_slice_off_by_one",
        request=TaskRequest(
            title="Fix last_n() dropping an element",
            description=_FIX_INSTRUCTIONS,
            max_revisions=2,
        ),
        files={
            "windows.py": dedent(
                """\
                def last_n(items: list[int], n: int) -> list[int]:
                    # Bug: off-by-one — returns one fewer than n.
                    if n <= 0:
                        return []
                    return items[-(n - 1):]
                """
            ),
            "test_windows.py": dedent(
                """\
                from windows import last_n


                def test_last_three():
                    assert last_n([1, 2, 3, 4, 5], 3) == [3, 4, 5]


                def test_n_equals_len():
                    assert last_n([1, 2], 2) == [1, 2]


                def test_n_zero():
                    assert last_n([1, 2, 3], 0) == []
                """
            ),
        },
        notes="Off-by-one in a slice; n==len edge case makes the naive fix fail.",
        tags=["bugfix", "easy"],
    ),
    CodingTask(
        name="impl_roman_numerals",
        request=TaskRequest(
            title="Implement to_roman()",
            description=_IMPL_INSTRUCTIONS,
            max_revisions=2,
        ),
        files={
            "roman.py": dedent(
                """\
                def to_roman(n: int) -> str:
                    raise NotImplementedError
                """
            ),
            "test_roman.py": dedent(
                """\
                import pytest

                from roman import to_roman


                @pytest.mark.parametrize(
                    "n,expected",
                    [
                        (1, "I"),
                        (4, "IV"),
                        (9, "IX"),
                        (14, "XIV"),
                        (40, "XL"),
                        (90, "XC"),
                        (400, "CD"),
                        (900, "CM"),
                        (1994, "MCMXCIV"),
                    ],
                )
                def test_to_roman(n, expected):
                    assert to_roman(n) == expected
                """
            ),
        },
        notes="From-scratch implementation; subtractive cases (IV/IX/XL) catch shortcuts.",
        tags=["implement", "medium"],
    ),
    # --- Discriminating: visible test = happy path, hidden tests = spec edges ---
    CodingTask(
        name="impl_median_edges",
        request=TaskRequest(
            title="Implement median() with even-length and empty handling",
            description=(
                "Implement `median(nums: list[float]) -> float` in stats.py (currently a "
                "stub). For an EVEN-length list, return the average of the two middle "
                "values. Raise ValueError for an empty list. Read the test file, implement, "
                "and run pytest to confirm before answering. Do NOT modify any test file."
            ),
            max_revisions=2,
        ),
        files={
            "stats.py": "def median(nums: list[float]) -> float:\n    raise NotImplementedError\n",
            "test_stats.py": dedent(
                """\
                from stats import median


                def test_odd_length():
                    assert median([3, 1, 2]) == 2
                """
            ),
        },
        hidden_tests={
            "test_stats_hidden.py": dedent(
                """\
                import pytest

                from stats import median


                def test_even_length_averages():
                    assert median([1, 2, 3, 4]) == 2.5


                def test_empty_raises():
                    with pytest.raises(ValueError):
                        median([])
                """
            ),
        },
        notes="Naive sorted()[mid] passes the odd visible test, fails even + empty.",
        tags=["implement", "edges", "discriminating"],
    ),
    CodingTask(
        name="impl_chunk_edges",
        request=TaskRequest(
            title="Implement chunk() with remainder and size guard",
            description=(
                "Implement `chunk(items: list, size: int) -> list[list]` in chunking.py "
                "(currently a stub). Split items into consecutive sublists of length `size`; "
                "the final sublist may be shorter when items don't divide evenly. An empty "
                "items list yields []. Raise ValueError when size <= 0. Read the test file, "
                "implement, and run pytest to confirm. Do NOT modify any test file."
            ),
            max_revisions=2,
        ),
        files={
            "chunking.py": "def chunk(items: list, size: int) -> list:\n    raise NotImplementedError\n",
            "test_chunking.py": dedent(
                """\
                from chunking import chunk


                def test_even_split():
                    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
                """
            ),
        },
        hidden_tests={
            "test_chunking_hidden.py": dedent(
                """\
                import pytest

                from chunking import chunk


                def test_remainder():
                    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


                def test_empty():
                    assert chunk([], 3) == []


                def test_bad_size_raises():
                    with pytest.raises(ValueError):
                        chunk([1, 2, 3], 0)
                """
            ),
        },
        notes="The size<=0 guard is the commonly-dropped requirement.",
        tags=["implement", "edges", "discriminating"],
    ),
    CodingTask(
        name="fix_flatten_one_level",
        request=TaskRequest(
            title="Fix flatten_one() to flatten exactly one level",
            description=(
                "`flatten_one(items: list) -> list` in flatten.py must flatten EXACTLY ONE "
                "level of nesting: each element that is a list is expanded by one level, but "
                "deeper nesting is preserved. It currently flattens recursively — a bug. Fix "
                "the source (NOT the tests), read the test file, and run pytest to confirm. "
                "Do NOT modify any test file."
            ),
            max_revisions=2,
        ),
        files={
            "flatten.py": dedent(
                """\
                def flatten_one(items: list) -> list:
                    out: list = []
                    for item in items:
                        if isinstance(item, list):
                            # Bug: recurses fully instead of expanding one level.
                            out.extend(flatten_one(item))
                        else:
                            out.append(item)
                    return out
                """
            ),
            "test_flatten.py": dedent(
                """\
                from flatten import flatten_one


                def test_one_level():
                    assert flatten_one([[1, 2], [3]]) == [1, 2, 3]
                """
            ),
        },
        hidden_tests={
            "test_flatten_hidden.py": dedent(
                """\
                from flatten import flatten_one


                def test_deep_nesting_preserved():
                    assert flatten_one([[1, [2]], [3]]) == [1, [2], 3]


                def test_already_flat():
                    assert flatten_one([1, 2, 3]) == [1, 2, 3]
                """
            ),
        },
        notes="Visible test passes even unfixed; only the hidden deep-nesting test catches it.",
        tags=["bugfix", "edges", "discriminating"],
    ),
    CodingTask(
        name="impl_normalize_whitespace",
        request=TaskRequest(
            title="Implement normalize_whitespace()",
            description=(
                "Implement `normalize_whitespace(text: str) -> str` in text_utils.py. It "
                "should trim leading/trailing whitespace and collapse any run of whitespace "
                "(spaces, tabs, newlines) into one ASCII space. Read the tests, implement the "
                "function, and run pytest. Do NOT modify any test file."
            ),
            max_revisions=2,
        ),
        files={
            "text_utils.py": "def normalize_whitespace(text: str) -> str:\n    raise NotImplementedError\n",
            "test_text_utils.py": dedent(
                """\
                from text_utils import normalize_whitespace


                def test_collapses_spaces():
                    assert normalize_whitespace("hello   world") == "hello world"
                """
            ),
        },
        hidden_tests={
            "test_text_utils_hidden.py": dedent(
                """\
                from text_utils import normalize_whitespace


                def test_strips_edges():
                    assert normalize_whitespace("  hello world  ") == "hello world"


                def test_collapses_tabs_and_newlines():
                    assert normalize_whitespace("a\\t\\n b") == "a b"


                def test_empty_after_strip():
                    assert normalize_whitespace(" \\n\\t ") == ""
                """
            ),
        },
        notes="Regex/split solution required; simple replace('  ', ' ') misses tabs/newlines.",
        tags=["implement", "strings", "edges"],
    ),
    CodingTask(
        name="fix_parse_bool_false_strings",
        request=TaskRequest(
            title="Fix parse_bool() string handling",
            description=(
                "`parse_bool(value: str) -> bool` in config_parse.py should accept true-ish "
                "strings (`true`, `yes`, `1`, `on`) and false-ish strings (`false`, `no`, "
                "`0`, `off`) case-insensitively after trimming whitespace. Unknown values "
                "must raise ValueError. Fix the source and run pytest. Do NOT modify tests."
            ),
            max_revisions=2,
        ),
        files={
            "config_parse.py": dedent(
                """\
                def parse_bool(value: str) -> bool:
                    # Bug: every non-empty string is True, including "false".
                    return bool(value)
                """
            ),
            "test_config_parse.py": dedent(
                """\
                from config_parse import parse_bool


                def test_true_string():
                    assert parse_bool("yes") is True
                """
            ),
        },
        hidden_tests={
            "test_config_parse_hidden.py": dedent(
                """\
                import pytest

                from config_parse import parse_bool


                def test_false_string():
                    assert parse_bool("false") is False


                def test_case_and_whitespace():
                    assert parse_bool(" OFF ") is False
                    assert parse_bool("On") is True


                def test_unknown_raises():
                    with pytest.raises(ValueError):
                        parse_bool("maybe")
                """
            ),
        },
        notes="Common Python truthiness trap; visible test alone passes unfixed.",
        tags=["bugfix", "strings", "discriminating"],
    ),
    CodingTask(
        name="impl_group_by_key",
        request=TaskRequest(
            title="Implement group_by_key()",
            description=(
                "Implement `group_by_key(rows: list[dict], key: str) -> dict` in grouping.py. "
                "Return a dict mapping each key value to the rows with that value, preserving "
                "input order inside each group. Rows missing the key should be grouped under "
                "`None`. Read tests, implement, and run pytest. Do NOT modify tests."
            ),
            max_revisions=2,
        ),
        files={
            "grouping.py": "def group_by_key(rows: list[dict], key: str) -> dict:\n    raise NotImplementedError\n",
            "test_grouping.py": dedent(
                """\
                from grouping import group_by_key


                def test_groups_rows():
                    rows = [{"team": "a", "id": 1}, {"team": "a", "id": 2}]
                    assert group_by_key(rows, "team") == {"a": rows}
                """
            ),
        },
        hidden_tests={
            "test_grouping_hidden.py": dedent(
                """\
                from grouping import group_by_key


                def test_preserves_order_across_groups():
                    rows = [
                        {"team": "b", "id": 1},
                        {"team": "a", "id": 2},
                        {"team": "b", "id": 3},
                    ]
                    assert group_by_key(rows, "team") == {
                        "b": [rows[0], rows[2]],
                        "a": [rows[1]],
                    }


                def test_missing_key_goes_to_none():
                    rows = [{"id": 1}, {"team": "a", "id": 2}]
                    assert group_by_key(rows, "team") == {None: [rows[0]], "a": [rows[1]]}
                """
            ),
        },
        notes="Requires preserving original row objects and order; missing-key behavior is hidden.",
        tags=["implement", "collections", "edges"],
    ),
    CodingTask(
        name="fix_dedupe_preserve_order",
        request=TaskRequest(
            title="Fix dedupe() to preserve first-seen order",
            description=(
                "`dedupe(items: list) -> list` in dedupe.py should remove duplicates while "
                "preserving the first occurrence order. It currently sorts a set, which loses "
                "order and fails on mixed comparable assumptions. Fix source and run pytest. "
                "Do NOT modify tests."
            ),
            max_revisions=2,
        ),
        files={
            "dedupe.py": dedent(
                """\
                def dedupe(items: list) -> list:
                    # Bug: removes duplicates but destroys first-seen order.
                    return sorted(set(items))
                """
            ),
            "test_dedupe.py": dedent(
                """\
                from dedupe import dedupe


                def test_preserves_first_seen_order():
                    assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]
                """
            ),
        },
        hidden_tests={
            "test_dedupe_hidden.py": dedent(
                """\
                from dedupe import dedupe


                def test_strings_keep_order():
                    assert dedupe(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


                def test_empty():
                    assert dedupe([]) == []
                """
            ),
        },
        notes="Simple but catches set/sort solutions that erase sequence semantics.",
        tags=["bugfix", "collections", "easy"],
    ),
    CodingTask(
        name="impl_deep_get",
        request=TaskRequest(
            title="Implement deep_get() for dotted dict paths",
            description=(
                "Implement `deep_get(data: dict, path: str, default=None)` in deep_get.py. "
                "A dotted path like `user.name` should traverse nested dictionaries. Return "
                "default if any segment is missing or the traversal hits a non-dict. Preserve "
                "falsey found values such as 0 or False. Read tests and run pytest."
            ),
            max_revisions=2,
        ),
        files={
            "deep_get.py": dedent(
                """\
                def deep_get(data: dict, path: str, default=None):
                    raise NotImplementedError
                """
            ),
            "test_deep_get.py": dedent(
                """\
                from deep_get import deep_get


                def test_reads_nested_value():
                    assert deep_get({"user": {"name": "Ada"}}, "user.name") == "Ada"
                """
            ),
        },
        hidden_tests={
            "test_deep_get_hidden.py": dedent(
                """\
                from deep_get import deep_get


                def test_missing_returns_default():
                    assert deep_get({"user": {}}, "user.name", "unknown") == "unknown"


                def test_non_dict_returns_default():
                    assert deep_get({"user": None}, "user.name", "unknown") == "unknown"


                def test_falsey_value_is_not_defaulted():
                    assert deep_get({"stats": {"count": 0}}, "stats.count", 99) == 0
                """
            ),
        },
        notes="Falsey-value case catches `or default` shortcuts.",
        tags=["implement", "dict", "edges"],
    ),
    CodingTask(
        name="fix_merge_intervals_touching",
        request=TaskRequest(
            title="Fix merge_intervals() edge cases",
            description=(
                "`merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]` "
                "in intervals.py should sort intervals and merge overlapping OR touching "
                "intervals, e.g. `(1, 3)` and `(3, 5)` become `(1, 5)`. Fix the source and "
                "run pytest. Do NOT modify tests."
            ),
            max_revisions=2,
        ),
        files={
            "intervals.py": dedent(
                """\
                def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
                    if not intervals:
                        return []
                    ordered = sorted(intervals)
                    merged = [ordered[0]]
                    for start, end in ordered[1:]:
                        last_start, last_end = merged[-1]
                        # Bug: touching intervals should merge too.
                        if start < last_end:
                            merged[-1] = (last_start, max(last_end, end))
                        else:
                            merged.append((start, end))
                    return merged
                """
            ),
            "test_intervals.py": dedent(
                """\
                from intervals import merge_intervals


                def test_merges_overlap():
                    assert merge_intervals([(1, 4), (2, 5)]) == [(1, 5)]
                """
            ),
        },
        hidden_tests={
            "test_intervals_hidden.py": dedent(
                """\
                from intervals import merge_intervals


                def test_merges_touching_intervals():
                    assert merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]


                def test_sorts_before_merging():
                    assert merge_intervals([(10, 12), (1, 2), (2, 4)]) == [(1, 4), (10, 12)]


                def test_empty():
                    assert merge_intervals([]) == []
                """
            ),
        },
        notes="Overlap visible test passes; touching intervals are hidden.",
        tags=["bugfix", "algorithm", "edges"],
    ),
    # --- Multi-file: forces several ReAct rounds (context-engine stress) -------
    CodingTask(
        name="fix_pipeline_multifile",
        request=TaskRequest(
            title="Fix the failing pipeline (bug is in one of several modules)",
            description=(
                "A small pipeline at the filesystem root fails its test. The bug is in "
                "exactly ONE of the step modules (steps_a.py / steps_b.py / steps_c.py). "
                "Read the files to understand the data flow, locate the buggy module, fix "
                "only that module, and run pytest to confirm. Do NOT modify any test file."
            ),
            max_revisions=2,
        ),
        files={
            "pipeline.py": dedent(
                """\
                from steps_a import inc
                from steps_b import dbl
                from steps_c import dec


                def run(x: int) -> int:
                    return dec(dbl(inc(x)))
                """
            ),
            "steps_a.py": "def inc(x: int) -> int:\n    return x + 1\n",
            "steps_b.py": dedent(
                """\
                def dbl(x: int) -> int:
                    # Bug: adds 2 instead of doubling.
                    return x + 2
                """
            ),
            "steps_c.py": "def dec(x: int) -> int:\n    return x - 1\n",
            "test_pipeline.py": dedent(
                """\
                from pipeline import run


                def test_run():
                    # inc(3)=4, dbl(4)=8, dec(8)=7
                    assert run(3) == 7
                """
            ),
        },
        hidden_tests={
            "test_pipeline_hidden.py": dedent(
                """\
                from pipeline import run


                def test_zero():
                    # inc(0)=1, dbl(1)=2, dec(2)=1
                    assert run(0) == 1
                """
            ),
        },
        notes="Multi-file exploration → several read rounds; used by the context ablation.",
        tags=["bugfix", "multifile", "context-stress"],
    ),
]
