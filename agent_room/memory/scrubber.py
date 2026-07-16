"""Streaming redaction for private memory-context prompt blocks."""

from __future__ import annotations

OPEN = "<memory-context>"
CLOSE = "</memory-context>"
REDACTED = "[memory-context redacted]"


class StreamingContextScrubber:
    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        out: list[str] = []
        while self._buffer:
            marker = CLOSE if self._inside else OPEN
            index = self._buffer.find(marker)
            if index >= 0:
                if not self._inside:
                    out.append(self._buffer[:index])
                    out.append(REDACTED)
                self._buffer = self._buffer[index + len(marker) :]
                self._inside = not self._inside
                continue
            if self._inside:
                self._buffer = _partial_suffix(self._buffer, marker)
                break
            keep = _partial_suffix(self._buffer, marker)
            emit_len = len(self._buffer) - len(keep)
            out.append(self._buffer[:emit_len])
            self._buffer = keep
            break
        return "".join(out)

    def flush(self) -> str:
        if self._inside:
            self._buffer = ""
            return ""
        out, self._buffer = self._buffer, ""
        return out


def _partial_suffix(text: str, marker: str) -> str:
    max_len = min(len(text), len(marker) - 1)
    for size in range(max_len, 0, -1):
        if marker.startswith(text[-size:]):
            return text[-size:]
    return ""
