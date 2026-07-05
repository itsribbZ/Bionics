"""Bionics Safety Layer - Tiered confirmation system for action execution.

CRITICAL: Destructive actions require 2 confirmations. No exceptions.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger("bionics.safety")


class SafetyTier(Enum):
    SAFE = auto()        # Auto-execute: mouse_move, scroll, screenshot
    MODERATE = auto()    # 1 confirmation: click, type, open_file
    DESTRUCTIVE = auto() # 2 confirmations: delete, close_unsaved, overwrite


ACTION_TIERS: dict[str, SafetyTier] = {
    # Tier 1 - Safe (auto-execute)
    "mouse_move": SafetyTier.SAFE,
    "scroll": SafetyTier.SAFE,
    "screenshot": SafetyTier.SAFE,
    "read_screen": SafetyTier.SAFE,
    "wait": SafetyTier.SAFE,

    # Tier 2 - Moderate (1 confirmation)
    "click": SafetyTier.MODERATE,
    "double_click": SafetyTier.MODERATE,
    "right_click": SafetyTier.MODERATE,
    "type_text": SafetyTier.MODERATE,
    "hotkey": SafetyTier.MODERATE,
    "drag": SafetyTier.MODERATE,
    "open_file": SafetyTier.MODERATE,
    "switch_window": SafetyTier.MODERATE,

    # Element-based actions (v2) - same tier as their base action
    "click_element": SafetyTier.MODERATE,
    "double_click_element": SafetyTier.MODERATE,
    "drag_to_element": SafetyTier.MODERATE,
    "wait_for_element": SafetyTier.SAFE,
    "click_anchor": SafetyTier.MODERATE,
    "verified_click": SafetyTier.MODERATE,
    "focus_window": SafetyTier.MODERATE,
    "find_window": SafetyTier.SAFE,
    "click_control": SafetyTier.MODERATE,

    # Tier 3 - Destructive (2 confirmations - CRITICAL)
    "delete": SafetyTier.DESTRUCTIVE,
    "close_window": SafetyTier.DESTRUCTIVE,
    "overwrite_file": SafetyTier.DESTRUCTIVE,
    "uninstall": SafetyTier.DESTRUCTIVE,
    "format": SafetyTier.DESTRUCTIVE,
    "terminal_command": SafetyTier.DESTRUCTIVE,
}

CONFIRMATION_REQUIRED: dict[SafetyTier, int] = {
    SafetyTier.SAFE: 0,
    SafetyTier.MODERATE: 1,
    SafetyTier.DESTRUCTIVE: 2,
}


@dataclass
class SafetyCheck:
    """Result of a safety check for an action."""
    action_name: str
    tier: SafetyTier
    confirmations_needed: int
    confirmations_received: int = 0
    approved: bool = False
    denied: bool = False
    deny_reason: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def fully_confirmed(self) -> bool:
        return self.confirmations_received >= self.confirmations_needed

    @property
    def pending(self) -> bool:
        return not self.approved and not self.denied


class SafetyLayer:
    """Manages safety checks and confirmations for all agent actions.

    The confirmation_callback is provided by the GUI and handles user interaction.
    It receives the SafetyCheck and returns True (approved) or False (denied).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._confirmation_callback: Callable[[SafetyCheck], bool] | None = None
        self._auto_approve_moderate: bool = False  # User can toggle for batch operations
        self._history: deque[SafetyCheck] = deque(maxlen=500)
        self._blocked_actions: set[str] = set()  # Actions the user has permanently blocked

    def set_confirmation_callback(self, callback: Callable[[SafetyCheck], bool]):
        """Set the callback function that handles user confirmations (provided by GUI)."""
        self._confirmation_callback = callback

    def block_action(self, action_name: str):
        """Permanently block an action type for this session."""
        with self._lock:
            self._blocked_actions.add(action_name)
        logger.warning(f"Action permanently blocked for session: {action_name}")

    def unblock_action(self, action_name: str):
        with self._lock:
            self._blocked_actions.discard(action_name)

    def get_tier(self, action_name: str) -> SafetyTier:
        """Get the safety tier for an action. Unknown actions default to DESTRUCTIVE."""
        return ACTION_TIERS.get(action_name, SafetyTier.DESTRUCTIVE)

    def check_action(self, action_name: str, details: str = "") -> SafetyCheck:
        """Evaluate an action and request confirmations as needed.

        Returns a SafetyCheck with approved=True if the action is cleared to execute.
        """
        tier = self.get_tier(action_name)
        confirmations_needed = CONFIRMATION_REQUIRED[tier]

        check = SafetyCheck(
            action_name=action_name,
            tier=tier,
            confirmations_needed=confirmations_needed,
        )

        # Blocked actions are always denied
        with self._lock:
            is_blocked = action_name in self._blocked_actions
            auto_approve = self._auto_approve_moderate
        if is_blocked:
            check.denied = True
            check.deny_reason = f"Action '{action_name}' is blocked for this session"
            logger.info(f"BLOCKED: {action_name} - {check.deny_reason}")
            self._history.append(check)
            return check

        # Safe actions auto-approve
        if tier == SafetyTier.SAFE:
            check.approved = True
            self._history.append(check)
            return check

        # Moderate actions with auto-approve enabled
        if tier == SafetyTier.MODERATE and auto_approve:
            check.confirmations_received = 1
            check.approved = True
            logger.info(f"AUTO-APPROVED (moderate): {action_name}")
            self._history.append(check)
            return check

        # Need user confirmation
        if self._confirmation_callback is None:
            check.denied = True
            check.deny_reason = "No confirmation handler registered"
            logger.error(f"DENIED (no handler): {action_name}")
            self._history.append(check)
            return check

        # Request confirmations
        for i in range(confirmations_needed):
            confirmation_num = i + 1
            logger.info(
                f"Requesting confirmation {confirmation_num}/{confirmations_needed} "
                f"for {action_name} ({tier.name}) - {details}"
            )

            approved = self._confirmation_callback(check)

            if approved:
                check.confirmations_received += 1
            else:
                check.denied = True
                check.deny_reason = f"User denied at confirmation {confirmation_num}/{confirmations_needed}"
                logger.info(f"DENIED: {action_name} - {check.deny_reason}")
                self._history.append(check)
                return check

        check.approved = True
        logger.info(f"APPROVED: {action_name} ({tier.name}) after {confirmations_needed} confirmation(s)")
        with self._lock:
            self._history.append(check)
        return check

    def set_auto_approve_moderate(self, enabled: bool):
        """Toggle auto-approval for moderate actions (batch mode)."""
        with self._lock:
            self._auto_approve_moderate = enabled
        logger.info(f"Auto-approve moderate actions: {enabled}")

    @property
    def history(self) -> list[SafetyCheck]:
        with self._lock:
            return list(self._history)

    def clear_history(self):
        with self._lock:
            self._history.clear()
