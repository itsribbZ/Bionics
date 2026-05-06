"""Bionics Session Manager - Persistence, crash recovery, and state serialization.

Saves agent state to disk so execution can resume after:
- User-initiated pause/stop + close
- Application crash
- System restart

Session files are stored in the audit directory alongside screenshots.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from core.planner import ExecutionPlan, PlanStep
from core.state import StateMachine

logger = logging.getLogger("bionics.session")

# Session IDs are generated via strftime("%Y%m%d_%H%M%S_%f")[:19] → digits + underscores
# only. Reject anything else BEFORE path construction so an adversarial id like
# "../../etc/passwd" cannot traverse out of _session_dir.
_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_\-]{1,64}$")


def _is_safe_session_id(session_id: str) -> bool:
    return bool(session_id) and bool(_SESSION_ID_PATTERN.fullmatch(session_id))


class SessionManager:
    """Manages session persistence and crash recovery."""

    def __init__(self, session_dir: str | Path = "audit/sessions"):
        self._session_dir = Path(session_dir)
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._current_session_id: str = ""

    @property
    def session_id(self) -> str:
        return self._current_session_id

    def create_session(
        self,
        plan: ExecutionPlan,
        state: StateMachine,
    ) -> str:
        """Create a new session and save initial state."""
        self._current_session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        session_data = self._build_session_data(plan, state)
        self._save(session_data)
        logger.info(f"Session created: {self._current_session_id}")
        return self._current_session_id

    def save_state(
        self,
        plan: ExecutionPlan,
        state: StateMachine,
        extra: dict | None = None,
        conversation_history: list[dict] | None = None,
    ):
        """Save current state to disk (call periodically and on pause/stop).

        `conversation_history` is the agent's running Claude message list —
        stripped of image payloads since those aren't useful after resume and
        bloat the session JSON.

        Also writes a slim progress.json for out-of-process observers (MCP
        clients, statuslines, web dashboards) that just need the percent
        complete without parsing the full session file.
        """
        if not self._current_session_id:
            return

        session_data = self._build_session_data(plan, state)
        if extra:
            session_data["extra"] = extra
        if conversation_history is not None:
            session_data["conversation_history"] = _strip_images_from_history(conversation_history)
        self._save(session_data)
        self._write_progress(plan, state)
        logger.debug(f"Session saved: {self._current_session_id}")

    def _write_progress(self, plan: ExecutionPlan, state: StateMachine) -> None:
        """Write a slim progress snapshot to audit/progress.json (atomic)."""
        try:
            total = max(int(state.total_steps), 1)
            current = int(state.current_step)
            snapshot = {
                "session_id": self._current_session_id,
                "plan_name": plan.name if plan else "",
                "state": state.state.name,
                "current_step": current,
                "total_steps": total,
                "percent": round(100.0 * current / total, 2),
                "updated_at": datetime.now().isoformat(),
            }
            target = self._session_dir.parent / "progress.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            tmp.replace(target)
        except Exception as e:
            logger.debug(f"progress.json write failed: {e}")

    def get_progress(self) -> dict | None:
        """Read the most recent progress snapshot. Returns None if not present."""
        try:
            target = self._session_dir.parent / "progress.json"
            if not target.exists():
                return None
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"progress.json read failed: {e}")
            return None

    def list_running_sessions(self) -> list[dict]:
        """List sessions currently in RUNNING state (subset of list_sessions)."""
        return [s for s in self.list_sessions() if s.get("state") == "RUNNING"]

    def load_session(self, session_id: str | None = None) -> dict | None:
        """Load a session from disk. If no ID given, loads the most recent.

        Rejects any session_id that doesn't match the safe-id pattern — a
        malicious id like "../../etc/passwd" cannot read arbitrary .json files
        even if it's passed in from MCP input via resume_from_session.
        """
        if session_id:
            if not _is_safe_session_id(session_id):
                logger.warning(f"Rejected session_id with invalid chars: {session_id!r}")
                return None
            filepath = self._session_dir / f"{session_id}.json"
            # Defense in depth: after constructing, verify the resolved path is
            # still a child of _session_dir. Guards against symlink shenanigans
            # and any regex miss.
            try:
                resolved = filepath.resolve()
                if resolved.parent != self._session_dir.resolve():
                    logger.warning(f"Session path traversal attempt: {session_id!r}")
                    return None
            except (OSError, ValueError):
                return None
        else:
            # Find most recent session
            sessions = sorted(self._session_dir.glob("*.json"), reverse=True)
            if not sessions:
                return None
            filepath = sessions[0]

        if not filepath.exists():
            return None

        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            logger.info(f"Session loaded: {filepath.stem}")
            return data
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            return None

    def restore_plan(self, session_data: dict) -> ExecutionPlan | None:
        """Restore an ExecutionPlan from session data."""
        try:
            plan_data = session_data.get("plan", {})
            steps = [
                PlanStep(
                    index=s["index"],
                    description=s["description"],
                    detailed_instructions=s.get("detailed_instructions", ""),
                    verification=s.get("verification", ""),
                    is_destructive=s.get("is_destructive", False),
                    requires_app=s.get("requires_app", ""),
                    category=s.get("category", "navigation"),
                    status=s.get("status", "pending"),
                )
                for s in plan_data.get("steps", [])
            ]

            return ExecutionPlan(
                name=plan_data.get("name", "Restored Plan"),
                description=plan_data.get("description", ""),
                steps=steps,
                prerequisites=plan_data.get("prerequisites", []),
                warnings=plan_data.get("warnings", []),
                source_file=plan_data.get("source_file", ""),
            )
        except Exception as e:
            logger.error(f"Failed to restore plan: {e}")
            return None

    def list_sessions(self) -> list[dict]:
        """List all saved sessions with summary info."""
        sessions = []
        for f in sorted(self._session_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append({
                    "id": f.stem,
                    "plan_name": data.get("plan", {}).get("name", "Unknown"),
                    "state": data.get("state", "unknown"),
                    "current_step": data.get("current_step", 0),
                    "total_steps": data.get("total_steps", 0),
                    "saved_at": data.get("saved_at", ""),
                    "can_resume": data.get("state") in ("PAUSED", "STOPPED", "RUNNING"),
                })
            except Exception as e:
                logger.warning(f"list_sessions: skipped corrupt session file {f.name} ({e})")
                continue
        return sessions

    def delete_session(self, session_id: str):
        """Delete a saved session. Rejects unsafe session_ids.

        Mirrors load_session's defense-in-depth: regex reject + resolve()-based
        child-check so a same-named symlink pointing outside _session_dir
        cannot cause the wrong file to be unlinked.
        """
        if not _is_safe_session_id(session_id):
            logger.warning(f"Rejected delete_session with invalid chars: {session_id!r}")
            return
        filepath = self._session_dir / f"{session_id}.json"
        try:
            resolved = filepath.resolve()
            if resolved.parent != self._session_dir.resolve():
                logger.warning(f"delete_session traversal attempt: {session_id!r} resolved to {resolved}")
                return
        except (OSError, ValueError):
            return
        if filepath.exists():
            filepath.unlink()
            logger.info(f"Session deleted: {session_id}")

    def has_resumable_session(self) -> bool:
        """Check if there's a session that can be resumed."""
        sessions = self.list_sessions()
        return any(s["can_resume"] for s in sessions)

    def get_latest_resumable(self) -> dict | None:
        """Get the most recent resumable session."""
        sessions = self.list_sessions()
        for s in sessions:
            if s["can_resume"]:
                return self.load_session(s["id"])
        return None

    def _build_session_data(self, plan: ExecutionPlan, state: StateMachine) -> dict:
        return {
            "session_id": self._current_session_id,
            "saved_at": datetime.now().isoformat(),
            "state": state.state.name,
            "current_step": state.current_step,
            "total_steps": state.total_steps,
            "plan": plan.to_dict() if plan else {},
        }

    def _save(self, data: dict):
        filepath = self._session_dir / f"{self._current_session_id}.json"
        tmp = filepath.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(filepath)  # Atomic on same volume

    def adopt_session(self, session_id: str) -> None:
        """Mark `session_id` as the current session so subsequent save_state calls
        overwrite its file. Used by the agent's resume flow to continue writing
        to the loaded session instead of creating a new one."""
        self._current_session_id = session_id


def _strip_images_from_history(history: list[dict]) -> list[dict]:
    """Remove base64 image blocks from a conversation history snapshot.

    Agent conversation history holds one screenshot per user turn for vision
    context. Persisting those to disk bloats session files (often 10-100x) and
    the images aren't useful after resume (the agent captures fresh ones).
    """
    clean: list[dict] = []
    for msg in history:
        content = msg.get("content")
        if isinstance(content, list):
            filtered = [b for b in content if b.get("type") != "image"]
            clean.append({**msg, "content": filtered})
        else:
            clean.append(msg)
    return clean
