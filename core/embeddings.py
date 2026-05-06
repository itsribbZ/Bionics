"""Embedding backends for Bionics semantic memory.

Embeddings convert variable-length text into fixed-size float vectors so memory
search can find *semantically* similar entries, not just substring matches.

Three implementations ship:

1. `LocalEmbedder`      — sentence-transformers (all-MiniLM-L6-v2, 384-dim, ~80 MB
                          one-time model download). Best quality/cost trade-off
                          for local desktop agents. Optional install:
                          `pip install bionics-agent[embeddings_local]`

2. `HashEmbedder`       — deterministic hash → 384-dim vector fallback.
                          Zero dependencies, works offline. Better than LIKE for
                          fuzzy lexical similarity but NOT semantic (e.g.
                          "cat" ≉ "feline" under HashEmbedder).

3. `NullEmbedder`       — sentinel: returns None. Used to signal "disable vector
                          search, fall back to LIKE." This is the default so
                          `BionicsMemory()` with no args keeps existing behavior.

All embedders conform to the `Embedder` protocol:

    class Embedder(Protocol):
        dim: int
        def embed(self, text: str) -> list[float] | None: ...

The protocol is a Protocol (structural), not an ABC — any object with the
right shape works.
"""
from __future__ import annotations

import hashlib
import logging
import math
import struct
from typing import Protocol, runtime_checkable

logger = logging.getLogger("bionics.embeddings")


@runtime_checkable
class Embedder(Protocol):
    dim: int
    def embed(self, text: str) -> list[float] | None: ...


class NullEmbedder:
    """Sentinel embedder — always returns None. Signals 'use LIKE fallback'."""
    dim: int = 0

    def embed(self, text: str) -> list[float] | None:
        return None


class HashEmbedder:
    """Zero-dep deterministic embedder. Hash-based lexical projection.

    Not semantic but still useful: captures shared n-grams and gives a stable
    feature space so the same text always maps to the same vector. Primarily
    here so tests (and users without local model weights) can exercise the
    vector-search code path.
    """
    dim: int = 384

    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed(self, text: str) -> list[float] | None:
        if not isinstance(text, str) or not text:
            return None
        text = text.lower().strip()
        # Tri-gram bag-of-hashes → project into `dim` buckets.
        # Normalize to unit length so cosine similarity ≡ dot product.
        vec = [0.0] * self.dim
        for i in range(len(text) - 2):
            trigram = text[i : i + 3]
            h = hashlib.md5(trigram.encode("utf-8")).digest()
            # Two independent indices per hash for higher collision resilience.
            idx1 = struct.unpack("<I", h[0:4])[0] % self.dim
            idx2 = struct.unpack("<I", h[4:8])[0] % self.dim
            sign1 = 1.0 if h[8] & 1 else -1.0
            sign2 = 1.0 if h[9] & 1 else -1.0
            vec[idx1] += sign1
            vec[idx2] += sign2
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


class LocalEmbedder:
    """sentence-transformers backed embedder.

    Uses all-MiniLM-L6-v2 by default (384 dim). Model weights download once on
    first use (~80 MB) and cache in `~/.cache/torch/sentence_transformers/`.
    """
    dim: int = 384

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise ImportError(
                "sentence-transformers missing. Install with: "
                "pip install bionics-agent[embeddings_local]"
            ) from e
        self._model = SentenceTransformer(model_name)
        # Pull actual dim from the loaded model in case the user swaps the name.
        try:
            self.dim = int(self._model.get_sentence_embedding_dimension())
        except Exception:
            pass

    def embed(self, text: str) -> list[float] | None:
        if not isinstance(text, str) or not text:
            return None
        try:
            vec = self._model.encode(text, normalize_embeddings=True)
            return [float(x) for x in vec.tolist()]
        except Exception as e:
            logger.warning("LocalEmbedder.embed failed: %s", e)
            return None


def get_default_embedder() -> Embedder:
    """Pick the best available embedder without raising.

    Preference: LocalEmbedder → HashEmbedder → NullEmbedder. Never throws.
    """
    try:
        return LocalEmbedder()
    except ImportError:
        pass
    except Exception as e:  # pragma: no cover
        logger.warning("LocalEmbedder unavailable: %s", e)
    # HashEmbedder is always available (stdlib-only).
    return HashEmbedder()
