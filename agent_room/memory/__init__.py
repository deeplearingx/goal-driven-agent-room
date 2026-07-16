"""Cross-session memory public API."""

from agent_room.memory.embedding import (
    EmbeddingBackend,
    HashingEmbeddingBackend,
    NoOpEmbeddingBackend,
)
from agent_room.memory.file_fts import FileFtsMemoryProvider
from agent_room.memory.provider import Memory, MemoryProvider, NoOpMemoryProvider, TranscriptRole
from agent_room.memory.vector_index import VectorIndex

__all__ = [
    "EmbeddingBackend",
    "FileFtsMemoryProvider",
    "HashingEmbeddingBackend",
    "Memory",
    "MemoryProvider",
    "NoOpEmbeddingBackend",
    "NoOpMemoryProvider",
    "TranscriptRole",
    "VectorIndex",
]
