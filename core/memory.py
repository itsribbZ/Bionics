"""Bionics Persistent Memory — SOTA 2026 cross-session recall.

A SQLite-backed memory store that persists task outcomes, user preferences,
application-specific patterns, and failure modes across Bionics sessions.
Closes the "every session starts cold" gap flagged in the 2026-04-16 SOTA
audit (Mem0 / LangMem / MemGPT parity for desktop automation).

Design:
- One SQLite DB at `~/.claude/telemetry/brain/bionics_memory.db`
- Scoped keys: (scope, topic, key) uniquely identify an entry
- Scopes: "task_outcome", "user_preference", "app_pattern", "failure", "learned_fix"
- Search modes:
    * 'semantic' — sqlite-vec cosine similarity (when `sqlite-vec` + embedder
                   are both available). Finds semantically related entries.
    * 'lexical'  — LIKE substring match. Substring-literal, always available.
    * 'auto'     — prefer semantic when available, fall back to lexical.
- All methods are SAFE TO FAIL — memory loss is degraded service, not a crash

Public API:
    store = BionicsMemory()
    store.remember("task_outcome", "ANIMATION", "mm_setup_v1", {"demo_ready": True, ...})
    entries = store.recall("task_outcome", "ANIMATION")
    hits = store.search("motion matching inertialization")               # auto
    hits = store.search("foot sliding fix", mode="semantic")             # force vec
    hits = store.search("mm_setup_v1", mode="lexical")                   # force LIKE
    store.forget("failure", "stale_path_v1")

Vector search opt-in:
    from core.embeddings import HashEmbedder, LocalEmbedder
    store = BionicsMemory(embedder=HashEmbedder())   # zero-dep, fast
    store = BionicsMemory(embedder=LocalEmbedder())  # ~80 MB, better quality
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any

from core.embeddings import Embedder, NullEmbedder

logger = logging.getLogger("bionics.memory")

# sqlite-vec is optional. When present, it registers a `vec0` virtual-table
# type that supports cosine-similarity search via `MATCH` queries.
try:
    import sqlite_vec  # type: ignore
    _HAS_SQLITE_VEC = True
except ImportError:
    _HAS_SQLITE_VEC = False


class BionicsMemory:
    """Persistent cross-session memory backed by SQLite."""

    DB_PATH = Path.home() / ".claude" / "telemetry" / "brain" / "bionics_memory.db"

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS memory (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         REAL NOT NULL,
        scope      TEXT NOT NULL,
        topic      TEXT NOT NULL,
        key        TEXT NOT NULL,
        value      TEXT NOT NULL,
        hits       INTEGER DEFAULT 0,
        last_hit   REAL,
        UNIQUE(scope, topic, key)
    );
    CREATE INDEX IF NOT EXISTS idx_mem_scope_topic ON memory(scope, topic);
    CREATE INDEX IF NOT EXISTS idx_mem_ts ON memory(ts DESC);
    """

    def __init__(
        self,
        db_path: Path | None = None,
        embedder: Embedder | None = None,
    ):
        """Create a memory store.

        Args:
            db_path:  Override the SQLite path (defaults to `~/.claude/...`).
            embedder: Optional embedding backend. When provided AND `sqlite-vec`
                      is installed, semantic search is enabled. If either is
                      missing, the store still works but only supports LIKE.
        """
        self._path = db_path or self.DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._embedder = embedder if embedder is not None else NullEmbedder()
        # Vector search active only when BOTH embedder and sqlite-vec are ready.
        self._vector_enabled = bool(
            self._embedder
            and getattr(self._embedder, "dim", 0)
            and _HAS_SQLITE_VEC
        )
        self._connect()

    def _connect(self):
        """Open connection, ensure schema, optionally load sqlite-vec.

        `check_same_thread=False` + WAL journal mode let concurrent readers +
        a single writer coexist (TaskManager's thread pool is a live writer
        source). The connection object is still serialized internally by
        sqlite3, and the write lock is grabbed per-transaction.
        """
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError as e:
            logger.debug("WAL mode unavailable (%s) — falling back to DELETE journal", e)
        if self._vector_enabled:
            try:
                self._conn.enable_load_extension(True)
                sqlite_vec.load(self._conn)
                self._conn.enable_load_extension(False)
            except Exception as e:
                logger.warning("sqlite-vec load failed, dropping to LIKE: %s", e)
                self._vector_enabled = False
        self._conn.executescript(self.SCHEMA)
        if self._vector_enabled:
            self._init_vec_table()
        self._conn.commit()

    def _init_vec_table(self):
        """Create the vec0 virtual table keyed by the same rowid as `memory`."""
        dim = int(self._embedder.dim)
        try:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
                f"  embedding float[{dim}]"
                f")"
            )
        except sqlite3.OperationalError as e:
            logger.warning("memory_vec create failed, disabling vector search: %s", e)
            self._vector_enabled = False

    def _embed_record(self, topic: str, key: str, value_json: str) -> list[float] | None:
        """Build an embedding from the searchable subset of a record."""
        if not self._vector_enabled:
            return None
        text = f"{topic} {key} {value_json}"
        try:
            return self._embedder.embed(text)
        except Exception as e:
            logger.warning("embed failed: %s", e)
            return None

    @staticmethod
    def _pack_vec(vec: list[float]) -> bytes:
        """Pack a float list to little-endian IEEE-754 bytes (sqlite-vec wire format)."""
        return struct.pack(f"<{len(vec)}f", *vec)

    def remember(self, scope: str, topic: str, key: str, value: Any) -> bool:
        """Store or update a memory entry. Returns True on success.

        Value is JSON-serialized for storage — can be dict, list, str, int, etc.
        When vector search is enabled, the row's embedding is rebuilt on every
        write so it stays consistent with the current `value`.
        """
        try:
            val_json = json.dumps(value, default=str)
            cur = self._conn.execute(
                """INSERT INTO memory (ts, scope, topic, key, value)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(scope, topic, key) DO UPDATE SET
                     value=excluded.value, ts=excluded.ts
                   RETURNING id""",
                (time.time(), scope, topic, key, val_json),
            )
            row = cur.fetchone()
            if self._vector_enabled and row is not None:
                row_id = row[0]
                vec = self._embed_record(topic, key, val_json)
                if vec is not None:
                    try:
                        self._conn.execute(
                            "DELETE FROM memory_vec WHERE rowid=?", (row_id,)
                        )
                        self._conn.execute(
                            "INSERT INTO memory_vec(rowid, embedding) VALUES (?, ?)",
                            (row_id, self._pack_vec(vec)),
                        )
                    except Exception as e:
                        logger.warning("vec index update failed for id=%s: %s", row_id, e)
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"remember failed: {e}")
            return False

    def recall(self, scope: str, topic: str = "", limit: int = 50) -> list[dict]:
        """Return all entries in a scope (optionally filtered by topic).

        Each entry: {id, ts, scope, topic, key, value (deserialized), hits}.
        Updates hit counters as a side effect (for recency/relevance ranking).
        """
        try:
            if topic:
                cur = self._conn.execute(
                    """SELECT id, ts, scope, topic, key, value, hits
                       FROM memory WHERE scope=? AND topic=?
                       ORDER BY ts DESC LIMIT ?""",
                    (scope, topic, limit),
                )
            else:
                cur = self._conn.execute(
                    """SELECT id, ts, scope, topic, key, value, hits
                       FROM memory WHERE scope=?
                       ORDER BY ts DESC LIMIT ?""",
                    (scope, limit),
                )
            rows = cur.fetchall()
            results = []
            for r in rows:
                results.append({
                    "id": r[0], "ts": r[1], "scope": r[2], "topic": r[3],
                    "key": r[4], "value": self._deserialize(r[5]), "hits": r[6],
                })
            # Bump hit counters for returned rows (async-safe via single execute)
            if rows:
                ids = [r[0] for r in rows]
                self._conn.execute(
                    f"UPDATE memory SET hits = hits + 1, last_hit = ? "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    [time.time()] + ids,
                )
                self._conn.commit()
            return results
        except Exception as e:
            logger.warning(f"recall failed: {e}")
            return []

    def search(
        self,
        query: str,
        limit: int = 20,
        mode: str = "auto",
    ) -> list[dict]:
        """Search memory by substring, semantic similarity, or auto-pick.

        Args:
            query: The search text.
            limit: Max results to return.
            mode:  'auto' (default) — semantic if enabled, else lexical.
                   'semantic' — force cosine-similarity; returns [] if vector
                                search isn't configured.
                   'lexical'  — force LIKE; always available.

        Result rows include a `distance` key in semantic mode (lower is closer).
        """
        if mode == "lexical" or (mode == "auto" and not self._vector_enabled):
            return self._search_lexical(query, limit)
        if mode == "semantic" and not self._vector_enabled:
            return []
        return self._search_semantic(query, limit)

    def _search_lexical(self, query: str, limit: int) -> list[dict]:
        try:
            q = f"%{query.lower()}%"
            cur = self._conn.execute(
                """SELECT id, ts, scope, topic, key, value, hits
                   FROM memory
                   WHERE LOWER(topic) LIKE ?
                      OR LOWER(key) LIKE ?
                      OR LOWER(value) LIKE ?
                   ORDER BY hits DESC, ts DESC LIMIT ?""",
                (q, q, q, limit),
            )
            return [{
                "id": r[0], "ts": r[1], "scope": r[2], "topic": r[3],
                "key": r[4], "value": self._deserialize(r[5]), "hits": r[6],
            } for r in cur.fetchall()]
        except Exception as e:
            logger.warning(f"search(lexical) failed: {e}")
            return []

    def _search_semantic(self, query: str, limit: int) -> list[dict]:
        try:
            vec = self._embedder.embed(query)
            if vec is None:
                return []
            # sqlite-vec KNN query: `embedding MATCH ? AND k = ?` ordered by distance.
            cur = self._conn.execute(
                """SELECT memory.id, memory.ts, memory.scope, memory.topic,
                          memory.key, memory.value, memory.hits,
                          memory_vec.distance
                   FROM memory_vec
                   JOIN memory ON memory.id = memory_vec.rowid
                   WHERE memory_vec.embedding MATCH ?
                     AND k = ?
                   ORDER BY memory_vec.distance""",
                (self._pack_vec(vec), limit),
            )
            return [{
                "id": r[0], "ts": r[1], "scope": r[2], "topic": r[3],
                "key": r[4], "value": self._deserialize(r[5]), "hits": r[6],
                "distance": float(r[7]),
            } for r in cur.fetchall()]
        except Exception as e:
            logger.warning("search(semantic) failed, dropping to lexical: %s", e)
            return self._search_lexical(query, limit)

    @property
    def vector_enabled(self) -> bool:
        """True when the store will use sqlite-vec for `mode='auto'` / 'semantic'."""
        return self._vector_enabled

    def forget(self, scope: str, key: str, topic: str = "") -> bool:
        """Delete an entry. Specify scope+key (and optionally topic for scope).

        Also drops the matching embedding row from `memory_vec` when present
        so the vector index doesn't leak.

        Returns True if at least one row was deleted.
        """
        try:
            # Look up IDs first so we can purge memory_vec atomically.
            if topic:
                cur = self._conn.execute(
                    "SELECT id FROM memory WHERE scope=? AND topic=? AND key=?",
                    (scope, topic, key),
                )
            else:
                cur = self._conn.execute(
                    "SELECT id FROM memory WHERE scope=? AND key=?", (scope, key),
                )
            doomed_ids = [r[0] for r in cur.fetchall()]
            if not doomed_ids:
                return False

            if self._vector_enabled:
                placeholders = ",".join("?" * len(doomed_ids))
                try:
                    self._conn.execute(
                        f"DELETE FROM memory_vec WHERE rowid IN ({placeholders})",
                        doomed_ids,
                    )
                except Exception as e:
                    logger.warning("vec index purge failed: %s", e)

            placeholders = ",".join("?" * len(doomed_ids))
            self._conn.execute(
                f"DELETE FROM memory WHERE id IN ({placeholders})", doomed_ids,
            )
            self._conn.commit()
            return True
        except Exception as e:
            logger.warning(f"forget failed: {e}")
            return False

    def count(self, scope: str = "") -> int:
        """Count entries (total or per-scope)."""
        try:
            if scope:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM memory WHERE scope=?", (scope,))
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM memory")
            return int(cur.fetchone()[0])
        except Exception:
            return 0

    def _deserialize(self, raw: str) -> Any:
        """Parse JSON, fall back to raw string on malformed data."""
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


# Module-level singleton for convenience — most callers don't need multiple DBs.
_default_memory: BionicsMemory | None = None
_default_memory_lock = __import__("threading").Lock()


def get_memory() -> BionicsMemory:
    """Lazy singleton. Thread-safe double-checked locking so concurrent first
    callers don't race and open two separate DB connections."""
    global _default_memory
    if _default_memory is None:
        with _default_memory_lock:
            if _default_memory is None:
                _default_memory = BionicsMemory()
    return _default_memory
