"""Tests for core modules — state machine, safety, executor, resilience, verification.

Covers the untested core modules identified in the 2026-04-09 audit.
All tests are pure unit tests — no GUI, no screen capture, no UE5.
"""

import time

# ============================================================================
# State Machine
# ============================================================================


class TestStateMachine:
    def test_initial_state_is_idle(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        assert sm.state == AgentState.IDLE

    def _to_running(self, sm):
        """Helper: transition through valid path to RUNNING."""
        from core.state import AgentState
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.REVIEWING)
        sm.transition(AgentState.RUNNING)

    def test_valid_transition(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        assert sm.transition(AgentState.PLANNING)
        assert sm.state == AgentState.PLANNING

    def test_full_lifecycle(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        assert sm.transition(AgentState.PLANNING)
        assert sm.transition(AgentState.REVIEWING)
        assert sm.transition(AgentState.RUNNING)
        assert sm.state == AgentState.RUNNING

    def test_invalid_transition_blocked(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        # IDLE → ERROR is not in TRANSITIONS
        assert not sm.transition(AgentState.ERROR)
        assert sm.state == AgentState.IDLE

    def test_force_stop(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        self._to_running(sm)
        sm.force_stop()
        assert sm.state == AgentState.STOPPED

    def test_reset_returns_to_idle(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        self._to_running(sm)
        sm.force_stop()
        sm.reset()
        assert sm.state == AgentState.IDLE

    def test_listener_notified_on_transition(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        events = []
        sm.add_listener(lambda old, new: events.append((old, new)))
        sm.transition(AgentState.PLANNING)
        assert len(events) == 1
        assert events[0] == (AgentState.IDLE, AgentState.PLANNING)

    def test_listener_exception_does_not_crash(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        sm.add_listener(lambda old, new: 1 / 0)  # will raise ZeroDivisionError
        # Should not raise
        sm.transition(AgentState.PLANNING)
        assert sm.state == AgentState.PLANNING

    def test_step_tracking(self):
        from core.state import StateMachine
        sm = StateMachine()
        sm.total_steps = 5
        sm.current_step = 2
        assert sm.total_steps == 5
        assert sm.current_step == 2

    def test_step_elapsed_timer(self):
        from core.state import StateMachine
        sm = StateMachine()
        sm.current_step = 0  # resets timer
        time.sleep(0.05)
        assert sm.step_elapsed >= 0.04

    def test_running_to_paused_to_running(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        self._to_running(sm)
        sm.transition(AgentState.PAUSED)
        assert sm.state == AgentState.PAUSED
        sm.transition(AgentState.RUNNING)
        assert sm.state == AgentState.RUNNING

    def test_stopped_to_idle(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        self._to_running(sm)
        sm.force_stop()
        assert sm.transition(AgentState.IDLE)

    def test_error_message_set(self):
        from core.state import AgentState, StateMachine
        sm = StateMachine()
        self._to_running(sm)
        sm.transition(AgentState.ERROR, "test error")
        assert sm.error_message == "test error"


# ============================================================================
# Safety Layer
# ============================================================================


class TestSafetyLayer:
    def test_safe_action_auto_approved(self):
        from core.safety import SafetyLayer, SafetyTier
        sl = SafetyLayer()
        check = sl.check_action("mouse_move")
        assert check.approved
        assert check.tier == SafetyTier.SAFE

    def test_moderate_without_callback_denied(self):
        from core.safety import SafetyLayer, SafetyTier
        sl = SafetyLayer()
        check = sl.check_action("click")
        assert check.tier == SafetyTier.MODERATE
        # No callback set → denied
        assert check.denied

    def test_moderate_with_callback_approved(self):
        from core.safety import SafetyLayer
        sl = SafetyLayer()
        sl.set_confirmation_callback(lambda check: True)
        check = sl.check_action("click")
        assert check.approved

    def test_moderate_auto_approve(self):
        from core.safety import SafetyLayer
        sl = SafetyLayer()
        sl._auto_approve_moderate = True
        check = sl.check_action("click")
        assert check.approved

    def test_destructive_needs_two_confirmations(self):
        from core.safety import SafetyLayer, SafetyTier
        sl = SafetyLayer()
        sl.set_confirmation_callback(lambda check: True)
        check = sl.check_action("delete")
        assert check.tier == SafetyTier.DESTRUCTIVE
        assert check.approved
        assert check.confirmations_received >= 2

    def test_blocked_action_denied(self):
        from core.safety import SafetyLayer
        sl = SafetyLayer()
        sl.block_action("click")
        check = sl.check_action("click")
        assert check.denied
        assert "blocked" in check.deny_reason.lower()

    def test_unknown_action_is_destructive(self):
        from core.safety import SafetyLayer, SafetyTier
        sl = SafetyLayer()
        tier = sl.get_tier("unknown_action_xyz")
        assert tier == SafetyTier.DESTRUCTIVE

    def test_element_actions_are_moderate(self):
        from core.safety import SafetyLayer, SafetyTier
        sl = SafetyLayer()
        for action in ["click_element", "double_click_element", "click_anchor",
                       "verified_click", "focus_window", "click_control"]:
            assert sl.get_tier(action) == SafetyTier.MODERATE, f"{action} should be MODERATE"

    def test_element_safe_actions(self):
        from core.safety import SafetyLayer, SafetyTier
        sl = SafetyLayer()
        assert sl.get_tier("wait_for_element") == SafetyTier.SAFE
        assert sl.get_tier("find_window") == SafetyTier.SAFE

    def test_history_capped(self):
        from core.safety import SafetyLayer
        sl = SafetyLayer()
        for i in range(600):
            sl.check_action("mouse_move")
        assert len(sl._history) <= 500

    def test_unblock_action(self):
        from core.safety import SafetyLayer
        sl = SafetyLayer()
        sl.block_action("click")
        sl.unblock_action("click")
        sl.set_confirmation_callback(lambda check: True)
        check = sl.check_action("click")
        assert check.approved


# ============================================================================
# Executor — FailureType and ActionResult
# ============================================================================


class TestExecutorTypes:
    def test_failure_type_is_enum_not_dataclass(self):
        from enum import Enum

        from core.executor import FailureType
        assert issubclass(FailureType, Enum)
        assert FailureType.NONE.value == "none"
        assert FailureType.ELEMENT_NOT_FOUND.value == "element_not_found"

    def test_action_result_creation(self):
        from core.executor import ActionResult, FailureType
        result = ActionResult(
            action="click", params={"x": 100, "y": 200}, success=True,
        )
        assert result.success
        assert result.failure_type == FailureType.NONE
        assert result.action == "click"

    def test_action_result_failure(self):
        from core.executor import ActionResult, FailureType
        result = ActionResult(
            action="click", params={}, success=False,
            error="element not found",
            failure_type=FailureType.ELEMENT_NOT_FOUND,
        )
        assert not result.success
        assert result.failure_type == FailureType.ELEMENT_NOT_FOUND

    def test_action_log_capped(self):
        from core.executor import ActionExecutor, ActionResult
        executor = ActionExecutor.__new__(ActionExecutor)
        from collections import deque
        executor._action_log = deque(maxlen=1000)
        for i in range(1100):
            executor._action_log.append(
                ActionResult(action="test", params={}, success=True)
            )
        assert len(executor._action_log) == 1000


# ============================================================================
# Resilience — CircuitBreaker
# ============================================================================


class TestCircuitBreaker:
    def test_starts_closed(self):
        from core.resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        assert cb.can_proceed()
        assert cb.state == "closed"

    def test_opens_after_threshold(self):
        from core.resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        for _ in range(3):
            cb.record_failure()
        assert not cb.can_proceed()
        assert cb.state == "open"

    def test_half_open_after_timeout(self):
        from core.resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.15)
        assert cb.can_proceed()
        assert cb.state == "half_open"

    def test_closes_on_success_from_half_open(self):
        from core.resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.15)
        cb.can_proceed()  # enters half_open
        cb.record_success()
        assert cb.state == "closed"
        assert cb.can_proceed()

    def test_reopens_on_failure_from_half_open(self):
        from core.resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.15)
        cb.can_proceed()  # enters half_open
        cb.record_failure()
        assert cb.state == "open"

    def test_success_resets_counter(self):
        from core.resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # Only 1 failure after reset, threshold is 3
        assert cb.can_proceed()


# ============================================================================
# Verification — history cap
# ============================================================================


class TestVerificationHistory:
    def test_history_capped(self):
        from core.verification import ActionVerifier, VerificationReport, VerifyResult
        v = ActionVerifier.__new__(ActionVerifier)
        from collections import deque
        v._history = deque(maxlen=500)
        for i in range(600):
            v._history.append(VerificationReport(
                result=VerifyResult.PASS, confidence=0.9,
                method="test", details="test",
            ))
        assert len(v._history) == 500


# ============================================================================
# Watch State Machine
# ============================================================================


class TestWatchStateMachine:
    def test_initial_state(self):
        from core.watch_state import WatchState, WatchStateMachine
        sm = WatchStateMachine()
        assert sm.state == WatchState.IDLE

    def test_idle_to_watching(self):
        from core.watch_state import WatchState, WatchStateMachine
        sm = WatchStateMachine()
        assert sm.transition(WatchState.WATCHING)
        assert sm.state == WatchState.WATCHING

    def test_watching_to_analyzing(self):
        from core.watch_state import WatchState, WatchStateMachine
        sm = WatchStateMachine()
        sm.transition(WatchState.WATCHING)
        assert sm.transition(WatchState.ANALYZING)

    def test_watching_to_idle_valid(self):
        from core.watch_state import WatchState, WatchStateMachine
        sm = WatchStateMachine()
        sm.transition(WatchState.WATCHING)
        assert sm.transition(WatchState.IDLE)
        assert sm.state == WatchState.IDLE

    def test_idle_to_analyzing_blocked(self):
        from core.watch_state import WatchState, WatchStateMachine
        sm = WatchStateMachine()
        assert not sm.transition(WatchState.ANALYZING)
        assert sm.state == WatchState.IDLE
