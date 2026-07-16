"""Small dependency-free embedding interface and hashing implementation."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol


class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def embed(self, text: str) -> list[float] | None: ...


class NoOpEmbeddingBackend:
    name = "noop"
    dim = 0

    def embed(self, text: str) -> None:
        return None


class HashingEmbeddingBackend:
    name = "hashing"

    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def embed(self, text: str) -> list[float] | None:
        normalized = " ".join(text.strip().lower().split())
        if not normalized:
            return None
        padded = f"  {normalized}  "
        grams = [padded[i : i + 3] for i in range(max(1, len(padded) - 2))]
        vec = [0.0] * self.dim
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            vec[value % self.dim] += 1.0 if value & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0:
            return None
        return [x / norm for x in vec]
