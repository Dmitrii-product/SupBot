"""Local, CPU-friendly multilingual embeddings.

Loaded lazily so importing this module is cheap (the model only loads on first
use). e5 models expect 'query:' / 'passage:' prefixes; we add them here.
"""
from functools import lru_cache

from bot import config

_IS_E5 = "e5" in config.EMBED_MODEL.lower()


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(config.EMBED_MODEL)


def dim() -> int:
    return _model().get_sentence_embedding_dimension()


def _encode(texts: list[str]) -> list[list[float]]:
    vecs = _model().encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


def embed_passages(texts: list[str]) -> list[list[float]]:
    prepped = [f"passage: {t}" for t in texts] if _IS_E5 else texts
    return _encode(prepped)


def embed_query(text: str) -> list[float]:
    prepped = f"query: {text}" if _IS_E5 else text
    return _encode([prepped])[0]
