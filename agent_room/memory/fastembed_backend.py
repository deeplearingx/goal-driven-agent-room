"""Optional real semantic embedding backend powered by fastembed."""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-zh"
_KNOWN_DIMS = {DEFAULT_MODEL: 768}


class FastEmbedBackend:
    name = "fastembed"

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self.dim = _KNOWN_DIMS.get(model_name, 768)
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeError(
                    "FastEmbedBackend requires the vector extra: pip install 'agent-room[vector]'"
                ) from exc
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, text: str) -> list[float] | None:
        clean = text.strip()
        if not clean:
            return None
        vector = next(iter(self._load().embed([clean])))
        result = [float(value) for value in vector]
        if len(result) != self.dim:
            raise ValueError(
                f"embedding dimension mismatch for {self.model_name!r}: "
                f"expected {self.dim}, got {len(result)}"
            )
        return result
