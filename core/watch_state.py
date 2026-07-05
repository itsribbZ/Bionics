"""Bionics Watch Mode State Machine — controls Watch Mode lifecycle."""

import threading
from collections.abc import Callable
from enum import Enum, auto


class WatchState(Enum):
    IDLE = auto()          # Watch Mode off
    WATCHING = auto()      # Capturing, waiting for screen change (SSIM gate)
    ANALYZING = auto()     # Claude analyzing screenshot
    ANNOTATING = auto()    # Showing annotations on overlay
    PAUSED = auto()        # User paused Watch Mode
    ERROR = auto()         # Something went wrong


WATCH_TRANSITIONS: dict[WatchState, set[WatchState]] = {
    WatchState.IDLE:       {WatchState.WATCHING, WatchState.ERROR},
    WatchState.WATCHING:   {WatchState.ANALYZING, WatchState.PAUSED, WatchState.IDLE, WatchState.ERROR},
    WatchState.ANALYZING:  {WatchState.ANNOTATING, WatchState.WATCHING, WatchState.IDLE, WatchState.ERROR},
    WatchState.ANNOTATING: {WatchState.WATCHING, WatchState.PAUSED, WatchState.IDLE, WatchState.ERROR},
    WatchState.PAUSED:     {WatchState.WATCHING, WatchState.IDLE},
    WatchState.ERROR:      {WatchState.IDLE, WatchState.WATCHING},
}


class WatchStateMachine:
    """Thread-safe state machine for Watch Mode."""

    def __init__(self):
        self._state = WatchState.IDLE
        self._lock = threading.RLock()
        self._listeners: list[Callable[[WatchState, WatchState], None]] = []

    @property
    def state(self) -> WatchState:
        with self._lock:
            return self._state

    def transition(self, new_state: WatchState) -> bool:
        with self._lock:
            if new_state not in WATCH_TRANSITIONS.get(self._state, set()):
                return False
            old = self._state
            self._state = new_state
        for listener in self._listeners:
            listener(old, new_state)
        return True

    def add_listener(self, listener: Callable[[WatchState, WatchState], None]):
        self._listeners.append(listener)

    @property
    def is_active(self) -> bool:
        return self.state in {WatchState.WATCHING, WatchState.ANALYZING, WatchState.ANNOTATING}
