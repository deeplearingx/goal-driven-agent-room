"""Model context-window sizes → budget-relative compression thresholds.

Borrows the hermes shape `threshold_tokens ≈ context_window × percent`: a
context engine should compress when the conversation approaches a fraction of
the model's window, not at an arbitrary absolute count. That needs to know the
window size per model — a small, approximate lookup (extend as new models land;
unknown ids fall back to a conservative modern default).
"""

from __future__ import annotations

# Approximate input context windows (tokens). Substring-matched, so version
# suffixes (`claude-opus-4-8-2026...`) resolve to the family entry.
_CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    "DeepSeek-V4-Pro": 128_000,
    "claude-opus": 200_000,
    "claude-sonnet": 200_000,
    "claude-haiku": 200_000,
    "claude-3": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4.1": 1_000_000,
    "o1": 200_000,
    "o3": 200_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def context_window_tokens(model_id: str) -> int:
    """Best-effort input context window for a model id; conservative default."""

    if model_id in _CONTEXT_WINDOWS:
        return _CONTEXT_WINDOWS[model_id]
    for known, size in _CONTEXT_WINDOWS.items():
        if known in model_id or model_id.startswith(known):
            return size
    return _DEFAULT_CONTEXT_WINDOW


def compression_budget(model_id: str, *, percent: float = 0.5) -> int:
    """Token budget = `context_window × percent` — the compression threshold.

    `percent=0.5` (hermes default) leaves the other half for the model's
    response plus the freshly-built prompt after compression.
    """

    if not 0.0 < percent <= 1.0:
        raise ValueError(f"percent must be in (0, 1], got {percent}")
    return int(context_window_tokens(model_id) * percent)
