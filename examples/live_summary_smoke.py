"""v0.4.x live-LLM smoke for SummaryContextEngine + §3.4 spill.

Two questions the offline test suite can't answer:

  Q1. Given `_serialize_for_summary` output as the dropped middle, does
      a real provider model write a usable summary? Or does the prompt
      shape — role-tagged, tool_call_id-annotated, 1500-char-truncated
      blocks — confuse it?

  Q2. Do real models tolerate `[spill:<ref> size=NN]` placeholders in
      the serialized middle? They should: the placeholder is plain text
      with a short preview. But "should" needs a witness.

This script answers both with a tight, scripted smoke — 2 LLM calls,
each compresses a hand-crafted 22-message ReAct middle. Skips cleanly
when no provider is configured (same path as `smoke_real_llm.py`).

Run:
    python -m examples.live_summary_smoke

Reads creds from `.env` (or shell env). Snapshots both runs to
./snapshots/live-summary-{no_spill,with_spill}.json — gitignored.

What good output looks like:

  - Both summaries mention the central decision (auth refactor blocked
    by missing env var) and the file paths visited.
  - The `with_spill` run's summary references the *symptom* of the big
    failure ("pytest reported N failures") even though the actual trace
    was replaced by a placeholder, because the SUMMARY_PREAMBLE asks
    for outcomes and the placeholder preview leaks the head of the body.
  - Neither summary contains the literal string "[spill:" — the model
    should treat the placeholder as opaque, not echo it back.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent_room.config import RoleBindings, load_settings
from agent_room.context.summary import (
    DEFAULT_SUMMARY_PREAMBLE,
    SummaryContextEngine,
    _serialize_for_summary,  # private but stable inside v0.4
)
from agent_room.tools.spill import InMemorySpillStore, spill_messages

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"


# ---------- fixture: 22-msg ReAct middle, hand-crafted ----------

# A realistic-ish history fragment: developer is debugging an auth refactor,
# touches a few files, runs pytest, gets a fat failure dump. The first 2 and
# last 8 messages are the protect window — we shape the input so exactly the
# big tool output lands in the middle that the summarizer will see.

_FAT_PYTEST_DUMP = (
    "============================= test session starts =============================\n"
    "platform linux -- Python 3.13.12, pytest-9.0.3\n"
    "rootdir: /home/dev/svc-auth\n"
    "collected 47 items\n\n" + "FAILED tests/test_login.py::test_admin_session - "
    "RuntimeError: missing AUTH_SECRET env var\n"
    * 50
    + "\n=========================== 47 failed in 12.34s ============================\n"
)


def _build_history() -> list:
    """Return a 22-msg list shaped so the middle includes one big ToolMessage."""
    return [
        # protect_first_n=2 — head of the conversation.
        HumanMessage(content="SYSTEM: developer node, auth-refactor task"),
        HumanMessage(
            content=(
                "# Plan\n1. read existing auth middleware\n2. refactor session "
                "token handling per legal/compliance ask\n3. run tests, fix any breakage\n"
                "# Task\nremove insecure session token storage from auth middleware "
                "and switch to encrypted-cookie based session keys"
            )
        ),
        # ===== middle (will be summarized) =====
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "name": "read_text",
                    "args": {"path": "src/auth/middleware.py"},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=(
                "from flask import session\n\n"
                "def authenticate(req):\n"
                "    token = req.cookies.get('session_token')\n"
                "    session['raw_token'] = token  # NOTE: insecure per audit\n"
                "    return token\n"
            ),
            tool_call_id="c1",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "c2",
                    "name": "write_text",
                    "args": {
                        "path": "src/auth/middleware.py",
                        "content": "...refactored body...",
                    },
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="wrote 1.2KB to src/auth/middleware.py", tool_call_id="c2"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "c3",
                    "name": "shell",
                    "args": {"cmd": "pytest tests/ -v"},
                    "type": "tool_call",
                }
            ],
        ),
        # The big one — ~3 KB, will be over default 4K only when we crank
        # the dump bigger; explicitly size below for the spilled test.
        ToolMessage(content=_FAT_PYTEST_DUMP, tool_call_id="c3"),
        AIMessage(content="The refactor broke existing tests because AUTH_SECRET isn't set in CI."),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "c4",
                    "name": "read_text",
                    "args": {"path": ".env.example"},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content="DATABASE_URL=postgres://...\n# AUTH_SECRET missing here\n",
            tool_call_id="c4",
        ),
        AIMessage(content="Need to add AUTH_SECRET to .env.example and surface it in CI config."),
        # ===== protect_last_n=8 — tail =====
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "c5",
                    "name": "write_text",
                    "args": {"path": ".env.example", "content": "...with AUTH_SECRET..."},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="wrote 200B to .env.example", tool_call_id="c5"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "c6",
                    "name": "shell",
                    "args": {"cmd": "pytest tests/test_login.py -v"},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="2 passed in 0.4s", tool_call_id="c6"),
        AIMessage(content="login tests pass; running broader suite next"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "c7",
                    "name": "shell",
                    "args": {"cmd": "pytest tests/ -q"},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="47 passed in 8.1s", tool_call_id="c7"),
        AIMessage(content="all green; need to update CHANGELOG and docs/auth.md next"),
        HumanMessage(content="reviewer: please also note the AUTH_SECRET migration in CHANGELOG"),
    ]


# ---------- run shape ----------


@dataclass
class RunResult:
    label: str
    elapsed_seconds: float
    summarizer_prompt_chars: int
    summary_chars: int
    summary_text: str
    spill_store_entries: int
    placeholder_in_summary: bool
    error: str | None = None


def _ensure_provider_configured() -> bool:
    settings = load_settings()
    if settings.anthropic_auth_token or settings.anthropic_api_key or settings.openai_api_key:
        return True
    print("No provider configured — copy .env.example → .env and fill creds. Skipping.")
    return False


def _extract_text(resp: object) -> str:
    """Pull plain text out of an `AIMessage.content`, which providers may
    return as either a `str` (Claude / Anthropic native) or a list of
    content blocks (`{type: 'text', text: ...}`, `{type: 'thinking', ...}`)
    on DeepSeek and other reasoning models. Drops `thinking` blocks; we
    want the assistant's *answer*, not its scratchpad."""
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


async def _run_one(label: str, *, with_spill: bool) -> RunResult:
    bindings = RoleBindings()  # resolves summarizer LLM from env
    summarizer = bindings.resolve("delivery")  # any cheap-ish role works

    store = InMemorySpillStore() if with_spill else None
    engine = SummaryContextEngine(
        summarizer=summarizer,
        max_messages=12,
        protect_first_n=2,
        protect_last_n=8,
        spill_store=store,
        spill_threshold=2_000,  # under FAT_PYTEST_DUMP size, over the small ones
    )

    history = _build_history()
    # Use the engine's internal partition so we know exactly what middle the
    # summarizer will see. The engine's public surface is `compress`, which
    # also assembles head + tail; we drive `_summarize` directly to isolate
    # the question we're asking ("does the model handle the prompt shape?").
    head, middle, tail = engine._partition(history)
    assert middle, "fixture should produce a non-empty middle for the chosen window"

    spilled = middle if store is None else spill_messages(middle, store, threshold=2_000)
    serialized = _serialize_for_summary(spilled)

    error: str | None = None
    summary_text = ""
    elapsed = 0.0
    start = time.perf_counter()
    try:
        prompt = [
            SystemMessage(content=DEFAULT_SUMMARY_PREAMBLE),
            HumanMessage(content=serialized),
        ]
        resp = await summarizer.ainvoke(prompt)
        summary_text = _extract_text(resp).strip()
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
    finally:
        elapsed = time.perf_counter() - start

    return RunResult(
        label=label,
        elapsed_seconds=elapsed,
        summarizer_prompt_chars=len(serialized),
        summary_chars=len(summary_text),
        summary_text=summary_text,
        spill_store_entries=len(store) if store is not None else 0,
        placeholder_in_summary=("[spill:" in summary_text),
        error=error,
    )


async def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except ImportError:
        pass

    if not _ensure_provider_configured():
        return 0

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    settings = load_settings()
    print(f"Provider: model={settings.default_model!r} base_url={settings.anthropic_base_url!r}")
    token = settings.anthropic_auth_token or settings.anthropic_api_key or ""
    print(f"Auth: {'***' + token[-6:] if token else '(none)'}\n")

    results: list[RunResult] = []
    for label, with_spill in [("no_spill", False), ("with_spill", True)]:
        print(f"--- {label} ---")
        try:
            r = await _run_one(label, with_spill=with_spill)
        except Exception as exc:
            r = RunResult(
                label=label,
                elapsed_seconds=0.0,
                summarizer_prompt_chars=0,
                summary_chars=0,
                summary_text="",
                spill_store_entries=0,
                placeholder_in_summary=False,
                error=f"{exc.__class__.__name__}: {exc}",
            )
        results.append(r)

        if r.error:
            print(f"  ERROR: {r.error}")
        else:
            print(f"  prompt: {r.summarizer_prompt_chars} chars")
            print(f"  summary ({r.summary_chars} chars, {r.elapsed_seconds:.1f}s):")
            preview = r.summary_text[:300] + ("..." if len(r.summary_text) > 300 else "")
            for line in preview.splitlines():
                print(f"    {line}")
            print(f"  spill store entries: {r.spill_store_entries}")
            print(f"  placeholder leaked into summary: {r.placeholder_in_summary}")
        out = SNAPSHOT_DIR / f"live-summary-{label}.json"
        out.write_text(json.dumps(asdict(r), indent=2, ensure_ascii=False))
        print(f"  → {out.relative_to(SNAPSHOT_DIR.parent)}\n")

    # Summary judgment.
    no_spill, with_spill = results
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    if no_spill.error or with_spill.error:
        print("FAIL — one or both runs raised; check error fields above.")
        return 1
    ratio = no_spill.summarizer_prompt_chars / max(with_spill.summarizer_prompt_chars, 1)
    print(
        f"  prompt size:  {no_spill.summarizer_prompt_chars} → "
        f"{with_spill.summarizer_prompt_chars} ({ratio:.1f}x reduction with spill)"
    )
    print(f"  summary size: {no_spill.summary_chars} vs {with_spill.summary_chars} chars")
    print(
        f"  with-spill summary leaked placeholder text: "
        f"{'YES (problem)' if with_spill.placeholder_in_summary else 'no'}"
    )
    print(
        "  Inspect the summary text above. If it captures the auth-refactor / AUTH_SECRET\n"
        "  story under both runs, v0.4 §4.3 + §3.4 are validated against this provider."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
