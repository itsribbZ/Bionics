"""Tests for sqlite-vec backed semantic memory (`core/memory.py`).

Covers:
  - HashEmbedder determinism
  - NullEmbedder returns None
  - BionicsMemory with no embedder stays LIKE-only (no regression)
  - BionicsMemory + HashEmbedder + sqlite-vec → vector search works
  - search(mode="semantic") returns [] when vector disabled, does not raise
  - forget() purges vector rows too
"""
from __future__ import annotations

import pytest

from core.embeddings import HashEmbedder, NullEmbedder, get_default_embedder
from core.memory import BionicsMemory

# ----- Embedder tests (no DB) -----

def test_null_embedder_returns_none():
    e = NullEmbedder()
    assert e.embed("anything") is None
    assert e.dim == 0


def test_hash_embedder_deterministic():
    e = HashEmbedder(dim=32)
    v1 = e.embed("motion matching locomotion")
    v2 = e.embed("motion matching locomotion")
    assert v1 == v2
    assert len(v1) == 32


def test_hash_embedder_empty_returns_none():
    e = HashEmbedder()
    assert e.embed("") is None
    assert e.embed(None) is None  # type: ignore[arg-type]


def test_hash_embedder_normalized_to_unit_length():
    e = HashEmbedder(dim=64)
    v = e.embed("hello world")
    import math
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-5


def test_get_default_embedder_never_raises():
    # Always returns something usable. May be LocalEmbedder if
    # sentence-transformers is installed, else HashEmbedder.
    e = get_default_embedder()
    assert hasattr(e, "embed")
    assert e.dim >= 32


# ----- Memory tests (isolated DB per test) -----

@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "bionics_test.db"


def test_default_memory_has_vector_disabled(tmp_db):
    """Default BionicsMemory() keeps LIKE-only behavior (no surprise opt-in)."""
    store = BionicsMemory(db_path=tmp_db)
    assert store.vector_enabled is False
    store.close()


def test_memory_with_hash_embedder_enables_vector(tmp_db):
    pytest.importorskip("sqlite_vec")
    store = BionicsMemory(db_path=tmp_db, embedder=HashEmbedder(dim=64))
    assert store.vector_enabled is True
    store.close()


def test_semantic_search_returns_empty_when_disabled(tmp_db):
    store = BionicsMemory(db_path=tmp_db)  # no embedder → vec disabled
    store.remember("task_outcome", "anim", "foo", {"detail": "motion matching"})
    assert store.search("motion matching", mode="semantic") == []
    store.close()


def test_lexical_search_works_without_embedder(tmp_db):
    store = BionicsMemory(db_path=tmp_db)
    store.remember("task_outcome", "anim", "foo", {"detail": "motion matching"})
    hits = store.search("motion matching", mode="lexical")
    assert len(hits) == 1
    assert hits[0]["key"] == "foo"
    store.close()


def test_auto_mode_falls_back_to_lexical_when_vec_disabled(tmp_db):
    store = BionicsMemory(db_path=tmp_db)
    store.remember("task_outcome", "anim", "foo", {"detail": "motion matching"})
    hits = store.search("motion", mode="auto")
    assert len(hits) == 1
    # Lexical hits don't have distance keys.
    assert "distance" not in hits[0]
    store.close()


def test_vector_search_returns_nearest_first(tmp_db):
    pytest.importorskip("sqlite_vec")
    store = BionicsMemory(db_path=tmp_db, embedder=HashEmbedder(dim=128))
    store.remember("task_outcome", "anim", "mm_setup", {"desc": "motion matching locomotion setup"})
    store.remember("task_outcome", "anim", "ik_setup", {"desc": "foot ik rig chain"})
    store.remember("task_outcome", "anim", "vfx_setup", {"desc": "niagara spawn emitter"})

    hits = store.search("motion matching locomotion", mode="semantic", limit=3)
    assert len(hits) == 3
    # Closest hit must be the motion-matching record (shares the most trigrams).
    assert hits[0]["key"] == "mm_setup"
    # Every returned hit must carry a non-negative cosine distance.
    assert all(h["distance"] >= 0 for h in hits)
    # Distances monotone non-decreasing (nearest-first ordering).
    distances = [h["distance"] for h in hits]
    assert distances == sorted(distances)
    # The "vfx" record (niagara spawn emitter) should be the farthest —
    # it shares no content tokens with the query.
    assert hits[-1]["key"] == "vfx_setup"
    store.close()


def test_forget_purges_vector_row(tmp_db):
    pytest.importorskip("sqlite_vec")
    store = BionicsMemory(db_path=tmp_db, embedder=HashEmbedder(dim=64))
    store.remember("task_outcome", "anim", "foo", {"desc": "bar"})
    hits = store.search("bar", mode="semantic", limit=1)
    assert len(hits) == 1

    assert store.forget("task_outcome", "foo") is True

    # After forget, semantic search must not return the row.
    post = store.search("bar", mode="semantic", limit=1)
    assert post == []

    # And the underlying vec table must have no orphan rows.
    cur = store._conn.execute("SELECT COUNT(*) FROM memory_vec")
    assert cur.fetchone()[0] == 0
    store.close()


def test_update_refreshes_embedding(tmp_db):
    """Re-remember() of the same key must rewrite the embedding to match new value."""
    pytest.importorskip("sqlite_vec")
    store = BionicsMemory(db_path=tmp_db, embedder=HashEmbedder(dim=64))
    store.remember("task_outcome", "anim", "foo", {"desc": "apple"})
    store.remember("task_outcome", "anim", "foo", {"desc": "banana"})

    # "apple" query should NOT find the row now; "banana" should.
    apple_hits = store.search("apple", mode="semantic", limit=3)
    banana_hits = store.search("banana", mode="semantic", limit=3)

    # Both return the same row (only one stored), but banana should be closer.
    assert len(banana_hits) == 1
    assert banana_hits[0]["key"] == "foo"
    if apple_hits:
        assert banana_hits[0]["distance"] < apple_hits[0]["distance"]
    store.close()


def test_vector_and_lexical_coexist(tmp_db):
    """Same store can serve both lexical and semantic queries independently."""
    pytest.importorskip("sqlite_vec")
    store = BionicsMemory(db_path=tmp_db, embedder=HashEmbedder(dim=64))
    store.remember("task_outcome", "topic_a", "key_a", {"desc": "apple banana"})

    lex = store.search("apple", mode="lexical")
    sem = store.search("apple", mode="semantic")
    assert len(lex) == 1
    assert len(sem) == 1
    assert lex[0]["id"] == sem[0]["id"]
    store.close()
