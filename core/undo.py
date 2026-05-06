"""Bionics Undo System - Action rollback and step reversal.

Three undo strategies:
1. App-level Ctrl+Z (works in most apps including UE5)
2. UE5 transaction undo (via Remote Control API)
3. Step-level undo (Ctrl+Z × N actions in that step)

Every executed action is pushed to an undo stack with metadata
about how to reverse it.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pyautogui

logger = logging.getLogger("bionics.undo")


@dataclass
class UndoEntry:
    """A single undoable action."""
    action_name: str
    params: dict = field(default_factory=dict)
    step_index: int = -1
    timestamp: float = field(default_factory=time.time)
    undo_count: int = 1  # How many Ctrl+Z needed to undo this
    can_undo: bool = True
    description: str = ""
    undone: bool = False


class UndoManager:
    """Manages undo stack and rollback operations."""

    # Actions that can be undone with Ctrl+Z
    CTRL_Z_ACTIONS = {
        "type_text", "click", "double_click", "drag",
        "hotkey", "template",
    }

    # Actions that cannot be undone
    NO_UNDO_ACTIONS = {
        "mouse_move", "scroll", "wait", "screenshot",
        "read_screen", "switch_window", "focus_window",
    }

    def __init__(self):
        self._stack: list[UndoEntry] = []
        self._max_stack: int = 200
        self._on_undo: Callable[[str], None] | None = None  # Log callback

    def set_log_callback(self, callback: Callable[[str], None]):
        self._on_undo = callback

    def _log(self, msg: str):
        logger.info(msg)
        if self._on_undo:
            self._on_undo(msg)

    def push(self, action_name: str, params: dict, step_index: int = -1, description: str = ""):
        """Record an action for potential undo."""
        can_undo = action_name not in self.NO_UNDO_ACTIONS

        # Determine how many Ctrl+Z this action needs
        undo_count = 1
        if action_name == "type_text":
            # Each character is a separate undo in some apps, but UE5 groups them
            undo_count = 1
        elif action_name == "template":
            # Templates may perform multiple undoable sub-actions
            undo_count = params.get("undo_count", 3)

        entry = UndoEntry(
            action_name=action_name,
            params=params,
            step_index=step_index,
            undo_count=undo_count,
            can_undo=can_undo,
            description=description or f"{action_name} {params}",
        )

        self._stack.append(entry)
        if len(self._stack) > self._max_stack:
            self._stack.pop(0)

    def undo_last(self) -> bool:
        """Undo the most recent undoable action using Ctrl+Z."""
        # Find the last undoable entry
        for i in range(len(self._stack) - 1, -1, -1):
            entry = self._stack[i]
            if entry.can_undo and not entry.undone:
                return self._undo_entry(entry)

        self._log("Nothing to undo")
        return False

    def undo_step(self, step_index: int) -> int:
        """Undo all actions from a specific step. Returns count of undos performed."""
        # Collect all undoable entries for this step, in reverse order
        entries = [
            e for e in reversed(self._stack)
            if e.step_index == step_index and e.can_undo and not e.undone
        ]

        if not entries:
            self._log(f"No undoable actions for step {step_index}")
            return 0

        count = 0
        for entry in entries:
            if self._undo_entry(entry):
                count += 1

        self._log(f"Undid {count} actions from step {step_index}")
        return count

    def _undo_entry(self, entry: UndoEntry) -> bool:
        """Execute undo for a single entry."""
        try:
            self._log(f"UNDO: {entry.description}")

            for _ in range(entry.undo_count):
                pyautogui.hotkey("ctrl", "z")
                time.sleep(0.15)

            entry.undone = True
            return True
        except Exception as e:
            self._log(f"Undo failed: {e}")
            return False

    def can_undo(self) -> bool:
        """Check if there are any undoable actions."""
        return any(e.can_undo and not e.undone for e in self._stack)

    @property
    def undo_count(self) -> int:
        """Number of actions that can be undone."""
        return sum(1 for e in self._stack if e.can_undo and not e.undone)

    def get_history(self, limit: int = 20) -> list[UndoEntry]:
        """Get recent undo stack entries."""
        return list(reversed(self._stack[-limit:]))

    def clear(self):
        self._stack.clear()
