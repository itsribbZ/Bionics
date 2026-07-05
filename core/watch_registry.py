"""Watch Mode Registry — shared reference to the live WatchEngine instance.

When the Bionics GUI starts Watch Mode, it registers the active WatchEngine
here. Tools (via MCP/CLI/HTTP) can then discover and control it.

This is a lightweight in-process registry — it does NOT persist across
processes. For cross-process control, tools also read/write
`audit/watch_state.json` as a fallback.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger("bionics.watch_registry")

from core.paths import PROJECT_ROOT as _PROJECT_ROOT

_STATE_FILE = _PROJECT_ROOT / "audit" / "watch_state.json"


class _WatchRegistry:
    """Singleton holder for the current WatchEngine instance + last state."""

    _instance: _WatchRegistry | None = None
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._engine = None
                    inst._rlock = threading.RLock()
                    inst._last_state = {
                        "status": "idle",
                        "registered": False,
                        "updated_at": datetime.now().isoformat(),
                    }
                    cls._instance = inst
        return cls._instance

    def register_engine(self, engine: Any) -> None:
        """Called by GUI when Watch Mode initializes."""
        with self._rlock:
            self._engine = engine
            self._last_state["registered"] = True
            self._last_state["updated_at"] = datetime.now().isoformat()
        self._persist_state()
        logger.info("WatchEngine registered")

    def unregister_engine(self) -> None:
        with self._rlock:
            self._engine = None
            self._last_state["registered"] = False
            self._last_state["updated_at"] = datetime.now().isoformat()
        self._persist_state()
        logger.info("WatchEngine unregistered")

    def get_engine(self) -> Any | None:
        with self._rlock:
            return self._engine

    def update_status(self, status: str, **extra) -> None:
        """Called by WatchEngine state transitions."""
        with self._rlock:
            self._last_state["status"] = status
            self._last_state["updated_at"] = datetime.now().isoformat()
            self._last_state.update(extra)
        self._persist_state()

    def get_state(self) -> dict:
        with self._rlock:
            return dict(self._last_state)

    def _persist_state(self) -> None:
        """Write state to disk for cross-process tool access.

        Uses temp-file-then-replace so a SIGKILL mid-write can't leave a
        zero-byte or partial JSON that breaks the next `read_persisted_state()`.
        """
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._last_state, indent=2, default=str),
                encoding="utf-8",
            )
            tmp.replace(_STATE_FILE)  # Atomic on same volume
        except Exception as e:
            logger.debug(f"State persist failed: {e}")


_registry: _WatchRegistry | None = None
_registry_lock = threading.Lock()


def get_watch_registry() -> _WatchRegistry:
    """Return the singleton WatchRegistry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = _WatchRegistry()
    return _registry


def read_persisted_state() -> dict:
    """Read the last-known Watch Mode state from disk.

    Useful for tools running in a separate process from the GUI.
    """
    if not _STATE_FILE.exists():
        return {"status": "idle", "registered": False, "source": "no_state_file"}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        data["source"] = "disk"
        return data
    except Exception as e:
        return {"status": "unknown", "error": str(e), "source": "parse_failed"}
