"""Tests for the Voyager self-verification gate in `core/tool_cache.py`.

Covers:
  - record_replay_outcome decays confidence on failure, boosts on success
  - find_proven filters out sequences with confidence < DEMOTE_THRESHOLD
  - replay_with_verification returns the first sequence whose execute_fn
    returns True, and None when all attempts fail
  - Exceptions inside execute_fn are treated as failures (not bubbled up)
"""
from __future__ import annotations

import pytest

from core.tool_cache import CONFIDENCE_DECAY, DEMOTE_THRESHOLD, ToolUseCache


@pytest.fixture
def cache(tmp_path):
    return ToolUseCache(db_path=tmp_path / "tc.db")


def test_record_replay_outcome_decays_on_failure(cache):
    cache.record("t", "do the thing", [{"tool": "a", "args": {}}], success=True)
    seq = cache._conn.execute(
        "SELECT id, confidence FROM sequences ORDER BY id DESC LIMIT 1"
    ).fetchone()
    before = seq[1]
    cache.record_replay_outcome(seq[0], success=False)
    after = cache._conn.execute(
        "SELECT confidence FROM sequences WHERE id=?", (seq[0],)
    ).fetchone()[0]
    assert after < before
    assert after == pytest.approx(before * CONFIDENCE_DECAY, abs=1e-6)


def test_record_replay_outcome_boosts_on_success_but_caps_at_1(cache):
    cache.record("t", "do the thing", [{"tool": "a", "args": {}}], success=True)
    seq_id = cache._conn.execute(
        "SELECT id FROM sequences ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    for _ in range(10):
        cache.record_replay_outcome(seq_id, success=True)
    capped = cache._conn.execute(
        "SELECT confidence FROM sequences WHERE id=?", (seq_id,)
    ).fetchone()[0]
    assert capped <= 1.0
    assert capped == pytest.approx(1.0, abs=1e-6)


def test_find_proven_filters_demoted_sequences(cache):
    """After enough failures a sequence drops below DEMOTE_THRESHOLD."""
    cache.record("t", "mm locomotion", [{"tool": "a"}], success=True)
    seq_id = cache._conn.execute("SELECT id FROM sequences").fetchone()[0]

    # Pre-demotion: find_proven returns it.
    hits = cache.find_proven("t", "mm locomotion", min_success_count=1, limit=3)
    assert len(hits) == 1

    # Decay past threshold (CONFIDENCE_DECAY=0.35, start 1.0 → 0.35 → 0.1225).
    cache.record_replay_outcome(seq_id, success=False)
    cache.record_replay_outcome(seq_id, success=False)

    confidence = cache._conn.execute(
        "SELECT confidence FROM sequences WHERE id=?", (seq_id,)
    ).fetchone()[0]
    assert confidence < DEMOTE_THRESHOLD

    # Post-demotion: find_proven filters it out.
    hits = cache.find_proven("t", "mm locomotion", min_success_count=1, limit=3)
    assert hits == []


def test_replay_with_verification_returns_first_success(cache):
    cache.record("t", "mm locomotion", [{"tool": "a"}], success=True)
    cache.record("t", "mm locomotion", [{"tool": "b"}], success=True)

    calls = []
    def exec_fn(seq):
        calls.append(seq)
        # First attempt fails, second succeeds.
        return len(calls) >= 2

    result = cache.replay_with_verification(
        "t", "mm locomotion", exec_fn, max_attempts=3, min_success_count=1,
    )
    assert result is not None
    assert len(calls) == 2
    assert "verified_attempts" in result
    assert [a["success"] for a in result["verified_attempts"]] == [False, True]


def test_replay_with_verification_returns_none_on_all_fail(cache):
    cache.record("t", "mm locomotion", [{"tool": "a"}], success=True)
    cache.record("t", "mm locomotion", [{"tool": "b"}], success=True)
    cache.record("t", "mm locomotion", [{"tool": "c"}], success=True)

    def exec_fn(seq):
        return False

    result = cache.replay_with_verification(
        "t", "mm locomotion", exec_fn, max_attempts=3, min_success_count=1,
    )
    assert result is None


def test_replay_with_verification_treats_exceptions_as_failure(cache):
    cache.record("t", "mm locomotion", [{"tool": "a"}], success=True)
    cache.record("t", "mm locomotion", [{"tool": "b"}], success=True)

    call_count = {"n": 0}
    def exec_fn(seq):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("executor crashed")
        return True

    result = cache.replay_with_verification(
        "t", "mm locomotion", exec_fn, max_attempts=2, min_success_count=1,
    )
    assert result is not None
    assert result["verified_attempts"][0]["success"] is False
    assert result["verified_attempts"][1]["success"] is True


def test_replay_with_verification_no_proven_returns_none(cache):
    """No prior successes → nothing to replay."""
    result = cache.replay_with_verification(
        "t", "never seen before", lambda seq: True,
        max_attempts=3, min_success_count=1,
    )
    assert result is None
