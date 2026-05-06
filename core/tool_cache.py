"""Bionics Tool-Use Success Cache — Voyager-pattern recall + self-verification.

Caches successful tool-call sequences for recurring tasks. When AutoPlanner
encounters a prompt similar to a past successful run, it can retrieve the
proven tool chain and use it as a warm-start template instead of planning
cold from scratch.

Inspired by the Voyager agent (Wang et al., 2023) — accumulates a growing
skill library over time. Each session, Bionics gets incrementally better at
tasks it's solved before.

Design:
- SQLite at `~/.claude/telemetry/brain/bionics_tool_cache.db`
- Schema: (topic, prompt_sig, sequence_json, success, duration_ms, confidence, ts)
- `prompt_sig` = lowercase content words sorted — rough similarity hash
- Retrieval: exact sig match first, then keyword overlap fallback
- Success-weighted ranking: only returns sequences with success rate >= threshold
- **Self-verification gate**: replay_with_verification() cycles through up to
  N proven sequences, marking each attempt's outcome via
  record_replay_outcome(). Sequences that keep failing get their confidence
  decayed and eventually filtered out of find_proven() — the cache self-heals
  instead of replaying a stale sequence forever.

Public API:
    cache = ToolUseCache()
    cache.record(topic, prompt, tool_sequence, success=True, duration_ms=1500)
    proven = cache.find_proven(topic, prompt, min_success_count=2)
    similar = cache.find_similar(prompt, limit=5)

    # Self-verification loop (Voyager § retry-and-update)
    hit = cache.replay_with_verification(
        topic, prompt,
        execute_fn=lambda seq: my_executor.run(seq),   # returns bool
        max_attempts=3,
    )
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("bionics.tool_cache")

# Confidence thresholds for the self-verification gate.
# Sequences below DEMOTE_THRESHOLD are filtered out of `find_proven`.
DEMOTE_THRESHOLD = 0.3
CONFIDENCE_DECAY = 0.35   # multiplier on failure (0.5 → 0.175 → filtered out)
CONFIDENCE_BOOST = 1.1    # multiplier on success (capped at 1.0)

# Stopwords for prompt signature hashing — remove low-signal tokens.
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "at", "for",
    "with", "i", "is", "are", "be", "this", "that", "it", "my", "your",
    "please", "fix", "add", "make", "do", "can", "should", "would",
    "will", "want", "need", "get", "set", "run",
})


class ToolUseCache:
    """Persistent cache of successful tool-call sequences (Voyager pattern)."""

    DB_PATH = Path.home() / ".claude" / "telemetry" / "brain" / "bionics_tool_cache.db"

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sequences (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL,
        topic           TEXT NOT NULL,
        prompt_sig      TEXT NOT NULL,
        prompt_head     TEXT NOT NULL,
        sequence_json   TEXT NOT NULL,
        success         INTEGER NOT NULL,
        duration_ms     INTEGER,
        confidence      REAL DEFAULT 1.0,
        times_reused    INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_tc_topic_sig ON sequences(topic, prompt_sig);
    CREATE INDEX IF NOT EXISTS idx_tc_success ON sequences(success);
    """

    def __init__(self, db_path: Path | None = None):
        self._path = db_path or self.DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + WAL — TaskManager's ThreadPoolExecutor can
        # record_replay_outcome() from any worker thread; WAL mode allows
        # concurrent readers + one writer instead of SQLITE_BUSY failures.
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError as e:
            logger.debug("WAL mode unavailable (%s)", e)
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    @staticmethod
    def prompt_signature(prompt: str) -> str:
        """Normalize a prompt into a content-word signature for similarity matching.

        Lowercase -> tokenize -> strip stopwords -> sort unique -> join with spaces.
        Two prompts with the same content words (regardless of order/phrasing)
        produce the same signature.
        """
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", prompt.lower())
        content = sorted({t for t in tokens if t not in _STOPWORDS and len(t) > 1})
        return " ".join(content)

    def record(self, topic: str, prompt: str, tool_sequence: list,
                success: bool, duration_ms: int = 0, confidence: float = 1.0) -> bool:
        """Store a tool-call sequence with its success outcome.

        tool_sequence: list of dicts like [{"tool": "ue5_compile_blueprint", "args": {...}}, ...]
        """
        try:
            sig = self.prompt_signature(prompt)
            self._conn.execute(
                """INSERT INTO sequences
                   (ts, topic, prompt_sig, prompt_head, sequence_json,
                    success, duration_ms, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), topic, sig, prompt[:200],
                 json.dumps(tool_sequence, default=str),
                 1 if success else 0, duration_ms, confidence),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"record failed: {e}")
            return False

    def find_proven(self, topic: str, prompt: str, min_success_count: int = 2,
                     limit: int = 3) -> list[dict]:
        """Return proven sequences for this topic/prompt signature.

        A sequence is "proven" if it has succeeded min_success_count or more times
        on the same (topic, prompt_sig). Ordered by confidence DESC, recency DESC.
        Sequences whose confidence has decayed below DEMOTE_THRESHOLD are
        filtered out — replayed-and-failed sequences self-heal out of the pool.
        """
        try:
            sig = self.prompt_signature(prompt)
            cur = self._conn.execute(
                """SELECT id, sequence_json, success, duration_ms, confidence,
                          times_reused, ts, prompt_head
                   FROM sequences
                   WHERE topic=? AND prompt_sig=? AND success=1
                     AND confidence >= ?
                   ORDER BY confidence DESC, ts DESC LIMIT ?""",
                (topic, sig, DEMOTE_THRESHOLD, limit),
            )
            rows = cur.fetchall()
            if len(rows) < min_success_count:
                return []
            # Bump reuse counter
            ids = [r[0] for r in rows]
            self._conn.execute(
                f"UPDATE sequences SET times_reused = times_reused + 1 "
                f"WHERE id IN ({','.join('?' * len(ids))})", ids,
            )
            self._conn.commit()
            return [{
                "id": r[0], "sequence": json.loads(r[1]), "success": bool(r[2]),
                "duration_ms": r[3], "confidence": r[4], "times_reused": r[5],
                "ts": r[6], "prompt_head": r[7],
            } for r in rows]
        except Exception as e:
            logger.warning(f"find_proven failed: {e}")
            return []

    def record_replay_outcome(self, sequence_id: int, success: bool) -> bool:
        """Update `confidence` on a prior sequence based on a replay result.

        Success boosts confidence (capped at 1.0). Failure decays it by
        CONFIDENCE_DECAY. When confidence drops below DEMOTE_THRESHOLD the
        sequence stops appearing in `find_proven` — the cache forgets bad
        advice automatically.
        """
        try:
            multiplier = CONFIDENCE_BOOST if success else CONFIDENCE_DECAY
            self._conn.execute(
                """UPDATE sequences
                   SET confidence = MIN(1.0, confidence * ?)
                   WHERE id = ?""",
                (multiplier, sequence_id),
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"record_replay_outcome failed: {e}")
            return False

    def replay_with_verification(
        self,
        topic: str,
        prompt: str,
        execute_fn: Callable[[list], bool],
        max_attempts: int = 3,
        min_success_count: int = 1,
    ) -> dict | None:
        """Voyager-style self-verification: try up to `max_attempts` proven
        sequences, record each outcome, return the first that succeeds.

        Returns the winning sequence dict on success, or None if every attempt
        failed. Failures decay the sequence's confidence via
        `record_replay_outcome` so repeated misses eventually demote the
        sequence out of `find_proven`.

        `execute_fn` receives the `sequence` list (a list of dicts shaped like
        `[{"tool": ..., "args": ...}, ...]`) and must return a boolean. It is
        the caller's responsibility to define "success" — Bionics only knows
        whether the callback returned True.
        """
        proven = self.find_proven(
            topic, prompt,
            min_success_count=min_success_count,
            limit=max_attempts,
        )
        if not proven:
            return None
        attempts_logged: list[dict] = []
        for seq in proven[:max_attempts]:
            started = time.time()
            try:
                ok = bool(execute_fn(seq["sequence"]))
            except Exception as e:
                logger.warning(
                    "replay_with_verification execute_fn raised: %s", e
                )
                ok = False
            elapsed_ms = int((time.time() - started) * 1000)
            self.record_replay_outcome(seq["id"], ok)
            attempts_logged.append({
                "id": seq["id"],
                "success": ok,
                "elapsed_ms": elapsed_ms,
            })
            if ok:
                seq["verified_attempts"] = attempts_logged
                return seq
        logger.info(
            "replay_with_verification: %d proven sequences tried, all failed",
            len(attempts_logged),
        )
        return None

    def find_similar(self, prompt: str, limit: int = 5) -> list[dict]:
        """Rough similarity search via overlapping content tokens.

        Returns sequences whose prompt_sig shares tokens with the query.
        Weights by success (1.0) vs failure (0.3) and recency.
        """
        try:
            tokens = set(self.prompt_signature(prompt).split())
            if not tokens:
                return []
            cur = self._conn.execute(
                "SELECT id, topic, prompt_sig, sequence_json, success, prompt_head, ts FROM sequences "
                "ORDER BY ts DESC LIMIT 200"
            )
            scored = []
            for r in cur.fetchall():
                other = set(r[2].split())
                if not other:
                    continue
                jaccard = len(tokens & other) / len(tokens | other)
                if jaccard == 0:
                    continue
                score = jaccard * (1.0 if r[4] else 0.3)
                scored.append((score, r))
            scored.sort(key=lambda x: -x[0])
            return [{
                "score": s, "id": r[0], "topic": r[1], "prompt_sig": r[2],
                "sequence": json.loads(r[3]), "success": bool(r[4]),
                "prompt_head": r[5], "ts": r[6],
            } for s, r in scored[:limit]]
        except Exception as e:
            logger.warning(f"find_similar failed: {e}")
            return []

    def stats(self) -> dict:
        """Summary stats for introspection / reporting."""
        try:
            cur = self._conn.execute(
                "SELECT COUNT(*), SUM(success), SUM(times_reused) FROM sequences"
            )
            total, success, reused = cur.fetchone()
            return {
                "total_sequences": int(total or 0),
                "successful": int(success or 0),
                "total_reuses": int(reused or 0),
                "db_path": str(self._path),
            }
        except Exception:
            return {"error": "stats unavailable"}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


import threading as _threading

_default_cache: ToolUseCache | None = None
_default_cache_lock = _threading.Lock()


def get_tool_cache() -> ToolUseCache:
    """Lazy singleton — shared across Bionics modules. Thread-safe double-checked
    locking so concurrent first callers don't open two separate DB connections."""
    global _default_cache
    if _default_cache is None:
        with _default_cache_lock:
            if _default_cache is None:
                _default_cache = ToolUseCache()
    return _default_cache
