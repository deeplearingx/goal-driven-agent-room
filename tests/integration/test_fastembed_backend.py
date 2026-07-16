"""Real-model test for `FastEmbedBackend` (v1.x §6.15) — downloads the actual
ONNX model from HuggingFace Hub on first run. Not part of the default
`pytest -q` suite (excluded via `norecursedirs` in pyproject.toml).

Run explicitly: `pytest tests/integration -q`
"""

from __future__ import annotations

import math

from agent_room.memory.fastembed_backend import DEFAULT_MODEL, FastEmbedBackend


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b)


def test_default_model_dim_matches_supported_models_list() -> None:
    backend = FastEmbedBackend(DEFAULT_MODEL)
    assert backend.dim == 768


def test_embed_returns_correct_length_vector() -> None:
    backend = FastEmbedBackend(DEFAULT_MODEL)
    vector = backend.embed("测试一下 embedding 的维度")
    assert vector is not None
    assert len(vector) == backend.dim


def test_empty_text_returns_none() -> None:
    backend = FastEmbedBackend(DEFAULT_MODEL)
    assert backend.embed("") is None
    assert backend.embed("   ") is None


def test_real_semantic_similarity_beats_hashing_on_zero_overlap_synonyms() -> None:
    """The gap `HashingEmbeddingBackend` honestly can't close (see
    agent_room/memory/embedding.py's docstring): a true synonym pair sharing
    almost no characters ("猫" / "喵星人", both mean "cat") should score
    meaningfully higher cosine similarity than an unrelated pair — this is
    the one thing a real transformer embedding buys over character hashing.
    """
    backend = FastEmbedBackend(DEFAULT_MODEL)
    cat = backend.embed("猫是一种常见的宠物")
    meme_cat = backend.embed("喵星人是很多人喜欢养的动物")
    unrelated = backend.embed("股票市场今天大幅下跌")
    assert cat is not None
    assert meme_cat is not None
    assert unrelated is not None

    synonym_similarity = _cosine(cat, meme_cat)
    unrelated_similarity = _cosine(cat, unrelated)
    assert synonym_similarity > unrelated_similarity
    assert synonym_similarity > 0.5
