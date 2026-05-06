"""Watch Mode Tools — Control Bionics Watch Mode from CLI / MCP / HTTP.

Watch Mode analyzes screenshots + overlays visual annotations with TTS narration.
These tools let external callers start/stop/status-check the engine.

When the Bionics GUI is running, Watch Mode is in-process and tools can call
directly into the engine. When the GUI is NOT running, tools return the
last-known state from disk (audit/watch_state.json).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from core.bridge import SafetyTier, ToolResult, bionics_tool
from core.watch_registry import get_watch_registry, read_persisted_state

logger = logging.getLogger("bionics.tools.watch_mode")


@bionics_tool(
    name="watch_status",
    category="watch",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    title="Watch Mode Status",
)
def watch_status() -> ToolResult:
    """Return current Watch Mode status (in-process or from disk)."""
    reg = get_watch_registry()
    in_memory = reg.get_state()
    if in_memory.get("registered"):
        return ToolResult.success(
            content=f"Watch Mode: {in_memory['status']} (in-process)",
            data={**in_memory, "source": "in_memory"},
        )
    disk = read_persisted_state()
    return ToolResult.success(
        content=f"Watch Mode: {disk.get('status', 'unknown')} (disk)",
        data=disk,
    )


@bionics_tool(
    name="watch_start",
    category="watch",
    safety_tier=SafetyTier.MODERATE,
    title="Start Watch Mode",
)
def watch_start(
    task: Annotated[str, "Task description — what the user is trying to do"] = "",
) -> ToolResult:
    """Start Watch Mode (requires Bionics GUI running with overlay)."""
    reg = get_watch_registry()
    engine = reg.get_engine()
    if engine is None:
        return ToolResult.failure(
            "No WatchEngine registered. Start the Bionics GUI first, "
            "then enable Watch Mode via the toggle or F8 hotkey."
        )
    try:
        if task:
            engine.set_task(task)
        engine.start()
        reg.update_status("watching", task=task)
        return ToolResult.success(
            content="Watch Mode started" + (f" for task: {task}" if task else ""),
            data={"task": task, "status": "watching"},
        )
    except Exception as e:
        return ToolResult.failure(f"Failed to start: {e}")


@bionics_tool(
    name="watch_stop",
    category="watch",
    safety_tier=SafetyTier.MODERATE,
    title="Stop Watch Mode",
)
def watch_stop() -> ToolResult:
    """Stop Watch Mode."""
    reg = get_watch_registry()
    engine = reg.get_engine()
    if engine is None:
        # Still update disk state
        reg.update_status("idle")
        return ToolResult.success(
            content="No engine running; persisted state set to idle",
            data={"status": "idle"},
        )
    try:
        engine.stop()
        reg.update_status("idle")
        return ToolResult.success(
            content="Watch Mode stopped",
            data={"status": "idle"},
        )
    except Exception as e:
        return ToolResult.failure(f"Stop failed: {e}")


@bionics_tool(
    name="watch_pause",
    category="watch",
    safety_tier=SafetyTier.MODERATE,
    title="Pause Watch Mode",
)
def watch_pause() -> ToolResult:
    """Pause Watch Mode (engine idles but stays warm)."""
    reg = get_watch_registry()
    engine = reg.get_engine()
    if engine is None:
        return ToolResult.failure("No WatchEngine running")
    try:
        engine.pause()
        reg.update_status("paused")
        return ToolResult.success(content="Watch Mode paused", data={"status": "paused"})
    except Exception as e:
        return ToolResult.failure(f"Pause failed: {e}")


@bionics_tool(
    name="watch_resume",
    category="watch",
    safety_tier=SafetyTier.MODERATE,
    title="Resume Watch Mode",
)
def watch_resume() -> ToolResult:
    """Resume Watch Mode from paused state."""
    reg = get_watch_registry()
    engine = reg.get_engine()
    if engine is None:
        return ToolResult.failure("No WatchEngine running")
    try:
        engine.resume()
        reg.update_status("watching")
        return ToolResult.success(content="Watch Mode resumed", data={"status": "watching"})
    except Exception as e:
        return ToolResult.failure(f"Resume failed: {e}")


@bionics_tool(
    name="watch_set_task",
    category="watch",
    safety_tier=SafetyTier.MODERATE,
    title="Set Watch Task",
)
def watch_set_task(
    task: Annotated[str, "Task description to guide Claude's analysis"],
) -> ToolResult:
    """Set the current Watch Mode task (what the user is trying to accomplish)."""
    reg = get_watch_registry()
    engine = reg.get_engine()
    if engine is None:
        return ToolResult.failure("No WatchEngine running")
    try:
        engine.set_task(task)
        reg.update_status(reg.get_state().get("status", "watching"), task=task)
        return ToolResult.success(
            content=f"Task set: {task[:80]}",
            data={"task": task},
        )
    except Exception as e:
        return ToolResult.failure(f"Set task failed: {e}")


@bionics_tool(
    name="watch_set_context",
    category="watch",
    safety_tier=SafetyTier.MODERATE,
    title="Set Watch Knowledge Context",
)
def watch_set_context(
    context: Annotated[str, "Knowledge context text (loaded docs, style guides, etc.)"],
) -> ToolResult:
    """Inject knowledge context into Watch Mode analysis (e.g. from loaded PDFs)."""
    reg = get_watch_registry()
    engine = reg.get_engine()
    if engine is None:
        return ToolResult.failure("No WatchEngine running")
    try:
        engine.set_knowledge_context(context)
        return ToolResult.success(
            content=f"Context set ({len(context)} chars)",
            data={"context_length": len(context)},
        )
    except Exception as e:
        return ToolResult.failure(f"Set context failed: {e}")


@bionics_tool(
    name="watch_session_info",
    category="watch",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Watch Session Info",
)
def watch_session_info() -> ToolResult:
    """Return current Watch Mode session metadata (cycle count, task, metrics)."""
    reg = get_watch_registry()
    engine = reg.get_engine()
    if engine is None:
        return ToolResult.failure("No WatchEngine running")
    try:
        info = {
            "task": getattr(engine, "_task_description", ""),
            "cycle_count": getattr(engine, "_cycle_count", 0),
            "session_id": getattr(engine, "_session_id", ""),
            "state": engine.state.state.name if hasattr(engine, "state") else "unknown",
            "screen_width": getattr(engine, "_screen_width", 0),
            "screen_height": getattr(engine, "_screen_height", 0),
        }
        return ToolResult.success(
            content=f"Watch session {info['session_id']}: cycle #{info['cycle_count']}",
            data=info,
        )
    except Exception as e:
        return ToolResult.failure(f"Info failed: {e}")


@bionics_tool(
    name="watch_list_sessions",
    category="watch",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Watch Sessions",
)
def watch_list_sessions(limit: int = 20) -> ToolResult:
    """List past Watch Mode session audit directories."""
    audit = Path(__file__).parent.parent / "audit" / "watch_sessions"
    if not audit.exists():
        return ToolResult.success(content="No watch sessions", data={"sessions": []})
    sessions = sorted(
        [d.name for d in audit.iterdir() if d.is_dir()],
        reverse=True,
    )[: max(1, min(limit, 100))]
    return ToolResult.success(
        content=f"{len(sessions)} watch sessions",
        data={"sessions": sessions, "count": len(sessions)},
    )
