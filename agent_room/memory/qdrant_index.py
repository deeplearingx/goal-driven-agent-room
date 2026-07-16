"""Optional embedded/remote Qdrant implementation of the vector boundary."""

from __future__ import annotations

from typing import Any


class QdrantVectorIndex:
    def __init__(
        self,
        *,
        path: str | None = None,
        url: str | None = None,
        collection: str = "agent_room_memory_vectors",
    ) -> None:
        if bool(path) == bool(url):
            raise ValueError("exactly one of path= or url= is required")
        self.path = path
        self.url = url
        self.collection = collection
        self._client: Any = None
        self._dim: int | None = None

    def _make_client(self) -> Any:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError(
                "QdrantVectorIndex requires the vector extra: pip install 'agent-room[vector]'"
            ) from exc
        return QdrantClient(path=self.path) if self.path else QdrantClient(url=self.url)

    async def initialize(self, dim: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        if self._client is None:
            self._client = self._make_client()
        names = {item.name for item in self._client.get_collections().collections}
        if self.collection not in names:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
        else:
            config = self._client.get_collection(self.collection)
            vectors = config.config.params.vectors
            existing_dim = getattr(vectors, "size", None)
            if existing_dim is not None and int(existing_dim) != dim:
                raise ValueError(
                    f"vector size mismatch: collection has {existing_dim}, requested {dim}"
                )
        self._dim = dim

    async def upsert(self, row_id: int, vector: list[float]) -> None:
        if self._client is None or self._dim is None:
            raise RuntimeError("QdrantVectorIndex is not initialized")
        from qdrant_client.models import PointStruct

        self._client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=row_id, vector=vector)],
            wait=True,
        )

    async def search(self, vector: list[float], k: int = 5) -> list[tuple[int, float]]:
        if k <= 0:
            return []
        if self._client is None:
            raise RuntimeError("QdrantVectorIndex is not initialized")
        if hasattr(self._client, "query_points"):
            response = self._client.query_points(
                collection_name=self.collection, query=vector, limit=k
            )
            points = response.points
        else:
            points = self._client.search(
                collection_name=self.collection, query_vector=vector, limit=k
            )
        return [(int(point.id), float(point.score)) for point in points]

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._dim = None
