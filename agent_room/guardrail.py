"""Content guardrail: pattern-based scanning at 4 reachable checkpoints
(v1.x §6.9-3 / PLAN.md §6.13).

Four checkpoints, each independently wired where content actually crosses a
trust boundary (not speculative hooks — every one was checked for
reachability before being wired, same discipline as `agent_room/budget.py`):

    input          — server/api.py, before a task's graph starts running.
    tool_call      — tools/fs.py WriteTextTool / tools/shell.py ShellTool,
                     before content leaves the sandbox (write to disk / exec).
    tool_response  — roles/developer_react.py, before a tool's result (from a
                     built-in tool *or* an MCP server) re-enters the LLM's
                     context. This is the highest-value new checkpoint: a
                     malicious/compromised MCP server can prompt-inject via
                     its tool's return value.
    output         — roles/delivery.py, on the final handoff text, the last
                     point before content leaves the system.

`INJECTION_PATTERNS` is the same pattern set `memory/curated.py` has used
since v0.5 (moved here so there's one canonical list, not two that can
drift — `curated.py` now imports it back). `PII_PATTERNS` is new: a small,
deliberately narrow set (email / AWS-shaped key / generic API-key-shaped
string) — lightweight pattern matching, not full PII/NER detection (see
PLAN.md §6.13 "故意不做").

`Guardrail.mode`: `"off"` (default, NoOp — same shape as `ContextEngine`/
`MemoryProvider`/`Budget`) / `"warn"` (scan, record, never blocks) / `"block"`
(scan, raise `GuardrailTripwire` on a hit). A `block` hit propagates out of
whichever role node triggered it exactly like `BudgetExceededError` — nodes
don't catch LLM/guardrail exceptions (CLAUDE.md §3.4) — and `RunManager`
turns it into a terminal `task_error(guardrail_blocked=True)` SSE frame with
zero new server-layer logic. A `warn` hit doesn't raise; the *caller* (each
checkpoint) is responsible for recording an `Event(type="guardrail_triggered",
...)` into `TaskState.events` — the same audit trail every other role event
already uses, no new state field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

GuardrailMode = Literal["off", "warn", "block"]
Checkpoint = Literal["input", "tool_call", "tool_response", "output"]

# Moved from agent_room/memory/curated.py (v0.5) — role-hijack / prompt
# injection / exfil-command shapes. `curated.py` imports this back so there's
# one canonical list. Conservative: false positives are tolerable (caller
# gets "rejected", can rephrase); false negatives are not.
INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?prior\s+(rules|instructions)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+\w+", re.IGNORECASE),
    re.compile(r"\b(curl|wget)\s+\S*://\S+", re.IGNORECASE),
    re.compile(r"\bsh\s+-c\b|\bbash\s+-c\b", re.IGNORECASE),
    re.compile(r"\.ssh/authorized_keys"),
    re.compile(r"\b(cat|less|head|tail)\s+\.env\b", re.IGNORECASE),
    re.compile(r"\$\(\s*cat\s+\.env"),
    re.compile(r"\bbase64\s+--?d(ecode)?\b", re.IGNORECASE),
    re.compile(r"[‪-‮⁦-⁩]"),  # bidi-override invisible chars
    re.compile(r"[​-‏﻿]"),  # zero-width / direction marks
)

# New, deliberately small — lightweight pattern matching, not PII/NER.
PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),  # email
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\b(sk|pk|api[_-]?key)[_-][A-Za-z0-9]{16,}\b", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    category: Literal["injection", "pii"]
    pattern: str
    checkpoint: Checkpoint


class GuardrailTripwire(RuntimeError):
    """Raised by `Guardrail.check()` in `mode="block"` when a scan hits.
    Propagates out of the checkpoint's caller exactly like any other domain
    exception — nodes don't catch it (CLAUDE.md §3.4)."""

    def __init__(self, finding: GuardrailFinding) -> None:
        self.finding = finding
        super().__init__(
            f"guardrail blocked: {finding.category} pattern {finding.pattern!r} "
            f"at checkpoint {finding.checkpoint!r}"
        )


def scan(text: str, *, checkpoint: Checkpoint) -> GuardrailFinding | None:
    """Pure scan — no mode logic, no side effects. First match wins."""
    for pat in INJECTION_PATTERNS:
        if pat.search(text):
            return GuardrailFinding("injection", pat.pattern, checkpoint)
    for pat in PII_PATTERNS:
        if pat.search(text):
            return GuardrailFinding("pii", pat.pattern, checkpoint)
    return None


@dataclass
class Guardrail:
    """`mode="off"` (default) never scans — zero overhead, zero behavior
    change. `"warn"` scans and returns findings without raising. `"block"`
    scans and raises `GuardrailTripwire` on a hit."""

    mode: GuardrailMode = "off"

    def check(self, text: str, *, checkpoint: Checkpoint) -> GuardrailFinding | None:
        if self.mode == "off" or not text:
            return None
        finding = scan(text, checkpoint=checkpoint)
        if finding is None:
            return None
        if self.mode == "block":
            raise GuardrailTripwire(finding)
        return finding


def describe_guardrail(guardrail: Guardrail) -> dict[str, object]:
    """Operator-facing summary (surfaced via /healthz), mirroring
    `react_runtime.describe_tool_envelope` / `budget.describe_budget`."""
    return {
        "mode": guardrail.mode,
        "checkpoints": ["input", "tool_call", "tool_response", "output"],
    }
