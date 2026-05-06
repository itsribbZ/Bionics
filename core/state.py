"""Bionics State Machine - Controls agent lifecycle."""

import threading
import time
from collections.abc import Callable
from enum import Enum, auto


class AgentState(Enum):
    IDLE = auto()        # No plan loaded, waiting
    PLANNING = auto()    # PDF loaded, Claude extracting steps
    REVIEWING = auto()   # Steps extracted, user reviewing before execution
    RUNNING = auto()     # Actively executing steps
    PAUSED = auto()      # User paused execution
    STOPPED = auto()     # User stopped execution (can restart)
    ERROR = auto()       # Something went wrong, needs user attention


# Valid state transitions
TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE:      {AgentState.PLANNING},
    AgentState.PLANNING:  {AgentState.REVIEWING, AgentState.ERROR, AgentState.IDLE},
    AgentState.REVIEWING: {AgentState.RUNNING, AgentState.IDLE, AgentState.ERROR},
    AgentState.RUNNING:   {AgentState.PAUSED, AgentState.STOPPED, AgentState.ERROR, AgentState.IDLE},
    AgentState.PAUSED:    {AgentState.RUNNING, AgentState.STOPPED, AgentState.IDLE},
    AgentState.STOPPED:   {AgentState.IDLE, AgentState.REVIEWING},
    AgentState.ERROR:     {AgentState.IDLE, AgentState.STOPPED},
}


class StateMachine:
    """Thread-safe state machine for the Bionics agent."""

    def __init__(self):
        self._state = AgentState.IDLE
        self._lock = threading.RLock()
        self._listeners: list[Callable[[AgentState, AgentState], None]] = []
        self._current_step: int = 0
        self._total_steps: int = 0
        self._step_start_time: float | None = None
        self._error_message: str = ""

    @property
    def state(self) -> AgentState:
        with self._lock:
            return self._state

    @property
    def current_step(self) -> int:
        with self._lock:
            return self._current_step

    @current_step.setter
    def current_step(self, value: int):
        with self._lock:
            self._current_step = value
            self._step_start_time = time.time()

    @property
    def total_steps(self) -> int:
        with self._lock:
            return self._total_steps

    @total_steps.setter
    def total_steps(self, value: int):
        with self._lock:
            self._total_steps = value

    @property
    def error_message(self) -> str:
        with self._lock:
            return self._error_message

    @property
    def step_elapsed(self) -> float:
        with self._lock:
            if self._step_start_time is None:
                return 0.0
            return time.time() - self._step_start_time

    def transition(self, new_state: AgentState, error_msg: str = "") -> bool:
        """Attempt a state transition. Returns True if successful."""
        with self._lock:
            old_state = self._state
            if new_state not in TRANSITIONS.get(old_state, set()):
                return False

            self._state = new_state
            if new_state == AgentState.ERROR:
                self._error_message = error_msg
            elif new_state == AgentState.IDLE:
                self._current_step = 0
                self._total_steps = 0
                self._step_start_time = None
                self._error_message = ""

            # Snapshot listeners under lock to avoid iteration-mutation race
            listeners = list(self._listeners)

        # Notify listeners outside lock to avoid deadlocks
        for listener in listeners:
            try:
                listener(old_state, new_state)
            except Exception:
                pass

        return True

    def add_listener(self, callback: Callable[[AgentState, AgentState], None]):
        """Register a callback for state changes. Called with (old_state, new_state)."""
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[AgentState, AgentState], None]):
        with self._lock:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

    def can_transition(self, new_state: AgentState) -> bool:
        with self._lock:
            return new_state in TRANSITIONS.get(self._state, set())

    def force_stop(self):
        """Emergency stop - forces transition to STOPPED regardless of current state."""
        with self._lock:
            old_state = self._state
            if old_state == AgentState.STOPPED:
                return  # Already stopped, don't fire listeners with old==new
            self._state = AgentState.STOPPED
            listeners = list(self._listeners)

        for listener in listeners:
            try:
                listener(old_state, AgentState.STOPPED)
            except Exception:
                pass

    def reset(self):
        """Reset to IDLE state. Forces through if normal transition not allowed."""
        if not self.transition(AgentState.IDLE):
            self.force_stop()
            self.transition(AgentState.IDLE)
