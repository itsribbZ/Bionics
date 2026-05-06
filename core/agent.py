"""Bionics Agent Core v2 - Hybrid routing, verification, retry, UE5 integration.

Upgrades from v1:
- Hybrid action routing: UE5 API → templates → vision+click
- Verification after every action (before/after screenshot comparison)
- Retry logic with configurable attempts
- Element-based actions (click_element instead of raw coordinates)
- UE5 bridge integration for programmatic operations
- Enhanced system prompt with new action types
"""

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable

from PIL import Image

from core.capture import ScreenCapture
from core.executor import Action, ActionExecutor, ActionResult
from core.planner import ExecutionPlan, PlanStep
from core.precision import ElementDetector
from core.resilience import CircuitBreaker, RetryConfig
from core.safety import SafetyLayer, SafetyTier
from core.session import SessionManager
from core.state import AgentState, StateMachine
from core.templates import get_template, list_templates
from core.ue5_bridge import ConnectionStatus, UE5Bridge
from core.undo import UndoManager
from core.verification import ActionVerifier, VerifyResult

# Re-exported for the test suite. The Anthropic class is no longer instantiated
# directly here (lazy init goes through core.anthropic_client.get_shared_client),
# but tests in test_agent.py patch `core.agent.Anthropic` as the mock surface
# for the AgentCore client construction. Removing this import would break the
# test contract that's been stable since v0.5.x. Don't strip — even ruff would
# (and did, in v0.7.7's first cleanup pass — restored here).
from anthropic import Anthropic  # noqa: F401

logger = logging.getLogger("bionics.agent")

AGENT_SYSTEM_PROMPT = """You are Bionics, an AI agent that controls a user's PC to execute a planned sequence of steps. You can see the user's screen via screenshots and you control their mouse, keyboard, windows, and UE5 Editor.

## Execution Hierarchy (use the most precise method available)
1. **UE5 API** (if UE5 is connected): Programmatic operations via Remote Control
2. **Templates**: Pre-built action sequences for common UE5 operations
3. **Element Detection**: Click on detected UI elements by template name
4. **Vision + Coordinates**: Direct coordinate-based clicking (least precise, last resort)

## Available Actions

Return a JSON object with the next action:

```json
{
    "reasoning": "What you see and why you're taking this action",
    "action": {
        "name": "<action_name>",
        "params": { ... }
    },
    "step_complete": false,
    "confidence": 0.95,
    "verify": {
        "method": "screen_changed|element_present|element_absent|region_changed|none",
        "params": {}
    }
}
```

### UE5 Template Actions (PREFERRED for UE5 work):
- template: {"template_name": "ue5.connect_pins", "params": {"blueprint_path": "...", "source_node": "...", "source_pin": "...", "target_node": "...", "target_pin": "..."}}
- template: {"template_name": "ue5.open_asset", "params": {"asset_path": "/Game/..."}}
- template: {"template_name": "ue5.save_asset", "params": {}}
- template: {"template_name": "ue5.compile", "params": {"blueprint_path": "..."}}
- template: {"template_name": "ue5.create_node", "params": {"blueprint_path": "...", "node_class": "...", "pos_x": 0, "pos_y": 0}}
- template: {"template_name": "ue5.set_property", "params": {"object_path": "...", "property_name": "...", "value": ...}}
- template: {"template_name": "ue5.console_command", "params": {"command": "..."}}
- template: {"template_name": "ue5.navigate_graph", "params": {"fit_all": true}}

### Element-Based Actions (click on detected UI elements):
- click_element: {"template_name": "button_compile", "button": "left", "offset_x": 0, "offset_y": 0}
- double_click_element: {"template_name": "node_header", "offset_x": 0, "offset_y": 0}
- drag_to_element: {"source_template": "pin_output", "target_template": "pin_input", "duration": 0.5}
- wait_for_element: {"template_name": "dialog_save", "timeout": 10.0}

### Precision Actions:
- click_anchor: {"reference_template": "node_header", "offset_x": 150, "offset_y": 30}
- verified_click: {"x": 500, "y": 300, "verify_template": "menu_open", "retries": 2}

### Basic Actions (fallback):
- mouse_move: {"x": int, "y": int, "duration": 0.3}
- click: {"x": int, "y": int, "button": "left"}
- double_click: {"x": int, "y": int}
- right_click: {"x": int, "y": int}
- drag: {"start_x": int, "start_y": int, "end_x": int, "end_y": int}
- scroll: {"clicks": int}
- type_text: {"text": "string", "interval": 0.02}
- hotkey: {"keys": ["ctrl", "s"]}
- wait: {"seconds": 1.0}
- switch_window: {"title": "window name"}

### Verification Methods (optional, in "verify" field):
- screen_changed: Verify something changed on screen
- element_present: {"template_name": "..."} — verify element appeared
- element_absent: {"template_name": "..."} — verify element disappeared
- region_changed: {"x": int, "y": int, "width": int, "height": int}
- none: skip verification

## Rules
1. PREFER templates and element-based actions over raw coordinates
2. Set step_complete=true ONLY when verification criteria from the plan are met
3. If confidence < 0.5, explain what's wrong — the agent will pause for user review
4. For UE5 work: use ue5.* templates whenever possible
5. Verify destructive actions explicitly before and after
6. If an element isn't found, try scrolling or navigating before giving up
7. Report what you see accurately — never guess or assume

## UE5 Connection Status: {ue5_status}
## Available Templates: {templates}
"""


# Native Anthropic tool-use schema for `_get_next_action`. Replaces the prior
# "please return JSON in a code fence" pattern with a structured tool call the
# SDK parses for us. Schema mirrors the JSON shape described in
# AGENT_SYSTEM_PROMPT so the prompt guidance stays accurate.
_TAKE_ACTION_TOOL = {
    "name": "take_action",
    "description": (
        "Emit the next agent action. You MUST call this tool exactly once per "
        "turn to describe what to do next. The action name + params follow the "
        "Available Actions section of the system prompt."
    ),
    "input_schema": {
        "type": "object",
        "required": ["reasoning", "action", "step_complete", "confidence"],
        "additionalProperties": False,
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "What you see on screen and why you're taking this action (1-3 sentences).",
            },
            "action": {
                "type": "object",
                "required": ["name", "params"],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Action name: template, click, click_element, double_click, right_click, "
                            "type_text, hotkey, press_key, scroll, mouse_move, drag, focus_window, "
                            "read_screen, wait, or empty string if no action needed this turn."
                        ),
                    },
                    "params": {
                        "type": "object",
                        "description": "Action parameters — shape depends on the action name.",
                    },
                },
            },
            "step_complete": {
                "type": "boolean",
                "description": "True if this step's goal is fully achieved after this action.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in this decision (0.0-1.0). Below 0.5 pauses the agent for human review.",
            },
            "verify": {
                "type": "object",
                "description": "Optional post-action verification spec.",
                "additionalProperties": False,
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "screen_changed, element_present, element_absent, region_changed, or ''",
                    },
                    "params": {
                        "type": "object",
                        "description": "Verify method parameters (shape depends on method).",
                    },
                },
            },
        },
    },
}


class AgentCore:
    """The main Bionics agent — hybrid routing with verification."""

    def __init__(
        self,
        state_machine: StateMachine,
        safety: SafetyLayer,
        capture: ScreenCapture,
        executor: ActionExecutor,
        api_key: str | None = None,
        model: str | None = None,
        loop_delay_ms: int = 500,
        step_timeout_s: int = 300,
        max_retries: int = 3,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self._state = state_machine
        self._safety = safety
        self._capture = capture
        self._executor = executor
        # Config.yaml read on init so callers that pass nothing get the
        # project-wide defaults. Explicit kwargs always override.
        api_cfg = self._load_api_config()
        self._model = model or api_cfg.get("model", "claude-sonnet-4-6")
        self._temperature = temperature if temperature is not None else api_cfg.get("temperature", 0.0)
        self._max_tokens = max_tokens if max_tokens is not None else api_cfg.get("max_tokens", 4096)
        self._loop_delay = loop_delay_ms / 1000.0
        self._step_timeout = step_timeout_s
        self._max_retries = max_retries

        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Anthropic API key not found. Set the ANTHROPIC_API_KEY environment variable "
                "(get one at https://console.anthropic.com/) or pass api_key= to AgentCore(). "
                "Bionics' agent loop cannot call Claude without it."
            )
        from core.anthropic_client import get_shared_client
        self._client = get_shared_client(api_key)

        # v2 components
        self._detector = ElementDetector()
        self._ue5 = UE5Bridge()
        self._verifier = ActionVerifier(self._detector)
        self._undo = UndoManager()

        # Resilience: circuit breaker for Claude API, session persistence for crash recovery
        self._circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        self._retry_config = RetryConfig(max_retries=3, base_delay=1.0, max_delay=30.0)
        self._session = SessionManager()
        self._ue5_recheck_interval = 5  # Re-check UE5 connection every N steps

        # Wire capture function into executor for element detection
        self._executor.set_capture_function(self._capture_pil)
        self._executor.set_capture_ref(self._capture)  # Coord unscaling pathway
        self._executor._detector = self._detector

        self._plan: ExecutionPlan | None = None
        self._thread: threading.Thread | None = None
        self._conversation_history: list[dict] = []

        # Callbacks for GUI updates
        self._on_action: Callable[[str, Action, ActionResult | None], None] | None = None
        self._on_step_change: Callable[[int, PlanStep], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._on_log: Callable[[str], None] | None = None

    def _capture_pil(self) -> Image.Image:
        """Capture screen as PIL Image (for element detection)."""
        return self._capture.capture()

    @staticmethod
    def _load_api_config() -> dict:
        """Read config.yaml['api'] so model / temperature / max_tokens defaults
        come from one source of truth. Returns {} on any error — the caller's
        hardcoded defaults then apply.
        """
        try:
            import yaml

            from core.paths import PROJECT_ROOT
            cfg_path = PROJECT_ROOT / "config.yaml"
            if not cfg_path.exists():
                return {}
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return cfg.get("api", {}) or {}
        except Exception as e:
            logger.debug(f"config.yaml api section unreadable: {e}")
            return {}

    @property
    def undo(self) -> UndoManager:
        return self._undo

    @property
    def ue5_bridge(self) -> UE5Bridge:
        return self._ue5

    @property
    def detector(self) -> ElementDetector:
        return self._detector

    @property
    def verifier(self) -> ActionVerifier:
        return self._verifier

    def set_callbacks(
        self,
        on_action: Callable | None = None,
        on_step_change: Callable | None = None,
        on_error: Callable | None = None,
        on_log: Callable | None = None,
    ):
        self._on_action = on_action
        self._on_step_change = on_step_change
        self._on_error = on_error
        self._on_log = on_log

    def _log(self, msg: str):
        logger.info(msg)
        if self._on_log:
            self._on_log(msg)

    def load_plan(self, plan: ExecutionPlan):
        # Reset all step statuses to pending (in case loaded from a previous run)
        for step in plan.steps:
            step.status = "pending"
        self._plan = plan
        self._state.total_steps = plan.total_steps
        self._state.current_step = 0
        self._conversation_history = []
        self._undo.clear()
        self._log(f"Plan loaded: '{plan.name}' ({plan.total_steps} steps)")

    def resume_session(self, session_id: str | None = None) -> bool:
        """Resume a previously saved session. Returns True if successful."""
        session_data = self._session.load_session(session_id)
        if not session_data:
            self._log("No resumable session found")
            return False

        plan = self._session.restore_plan(session_data)
        if not plan:
            self._log("Failed to restore plan from session")
            return False

        # Load plan WITHOUT resetting step statuses (preserves completed/in_progress)
        self._plan = plan
        self._state.total_steps = plan.total_steps
        self._conversation_history = []
        self._undo.clear()

        # Reset in_progress steps to pending (they were interrupted)
        for step in plan.steps:
            if step.status == "in_progress":
                step.status = "pending"

        completed = sum(1 for s in plan.steps if s.status == "completed")
        self._log(f"Session resumed: '{plan.name}' ({completed}/{plan.total_steps} steps completed)")
        return True

    def check_ue5_connection(self) -> ConnectionStatus:
        """Check and report UE5 connection status."""
        status = self._ue5.check_connection()
        status_msg = {
            ConnectionStatus.CONNECTED: "UE5 CONNECTED - API mode available",
            ConnectionStatus.PLUGIN_MISSING: "UE5 running but Remote Control plugin not configured",
            ConnectionStatus.EDITOR_NOT_RUNNING: "UE5 Editor not detected - using vision mode",
            ConnectionStatus.DISCONNECTED: "UE5 disconnected - using vision mode",
        }
        self._log(status_msg.get(status, "UE5 status unknown"))
        return status

    def start(self):
        if self._plan is None:
            self._log("ERROR: No plan loaded")
            return

        if not self._state.transition(AgentState.RUNNING):
            self._log(f"ERROR: Cannot start from state {self._state.state.name}")
            return

        # Check UE5 connection before starting
        self.check_ue5_connection()

        # Create a session for crash recovery
        self._session.create_session(self._plan, self._state)
        self._circuit_breaker.reset()

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log("Agent started")

    def resume_from_session(self, session_id: str | None = None) -> bool:
        """Load a saved session and continue the agent loop from where it left off.

        Restores: plan, current step, state, conversation history. The circuit
        breaker resets (fresh start), UE5 connection is re-verified. Saved
        RUNNING state is demoted to PAUSED so the user can inspect before
        continuing. Pass `session_id=None` to pick up the latest resumable session.

        Returns True if the session was loaded and the loop started; False on
        any failure (logged).
        """
        if session_id:
            session_data = self._session.load_session(session_id)
        else:
            session_data = self._session.get_latest_resumable()
        if not session_data:
            self._log(f"Resume failed: no session data for id={session_id or '<latest>'}")
            return False

        restored_plan = self._session.restore_plan(session_data)
        if restored_plan is None:
            self._log("Resume failed: could not restore plan from session data")
            return False

        self._plan = restored_plan

        # Restore conversation history (already image-stripped by SessionManager)
        history = session_data.get("conversation_history")
        self._conversation_history = list(history) if isinstance(history, list) else []

        # Restore step index: the saved state was mid-execution of current_step.
        saved_step_index = int(session_data.get("current_step", 0))
        if 0 <= saved_step_index < len(self._plan.steps):
            self._state.current_step = saved_step_index
            step = self._plan.steps[saved_step_index]
            # Re-enter the step as "in_progress" so _run_loop picks up mid-stream.
            if step.status == "completed":
                # Already finished — advance to the next one.
                self._state.current_step = saved_step_index + 1
            else:
                step.status = "in_progress"

        # Adopt the existing session_id so save_state continues writing the same file.
        self._session.adopt_session(session_id or session_data.get("session_id", ""))

        # Saved RUNNING → demote to PAUSED so user inspects before continuing.
        saved_state_name = session_data.get("state", "IDLE")
        try:
            if saved_state_name in ("RUNNING", "PAUSED"):
                self._state.transition(AgentState.PAUSED)
            else:
                self._state.transition(AgentState.IDLE)
        except Exception as e:
            # State transitions are advisory here — the resume path proceeds
            # regardless. But a hard failure suggests state machine drift; log
            # so it's visible instead of swallowed silently.
            self._log(f"Resume: state transition from {saved_state_name} failed: {e}; continuing")

        # Re-check UE5 + reset circuit breaker for the fresh run.
        self.check_ue5_connection()
        self._circuit_breaker.reset()

        # Now start the loop (transition PAUSED → RUNNING happens via resume()).
        if not self._state.transition(AgentState.RUNNING):
            self._log(f"Resume: cannot transition from {self._state.state.name} to RUNNING. Call resume() after inspection.")
            # Still spawn the thread? No — let user call resume() explicitly.
            return True  # Session loaded successfully, even if user must resume manually.

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log(f"Agent resumed from session {session_data.get('session_id', '?')} at step {self._state.current_step + 1}/{len(self._plan.steps)}")
        return True

    def pause(self):
        if self._state.transition(AgentState.PAUSED):
            self._session.save_state(self._plan, self._state, conversation_history=self._conversation_history)
            self._log("Agent paused (session saved)")

    def resume(self):
        if self._state.transition(AgentState.RUNNING):
            self._log("Agent resumed")

    def stop(self):
        if self._state.transition(AgentState.STOPPED):
            self._session.save_state(self._plan, self._state, conversation_history=self._conversation_history)
            self._log("Agent stopped (session saved)")
            # Fire Stop lifecycle hooks — lets observers clean up / flush metrics.
            try:
                from core.bridge import fire_stop_hooks
                fire_stop_hooks("user_stop", {"session_id": self._session.session_id})
            except Exception as e:
                logger.debug(f"fire_stop_hooks failed (non-fatal): {e}")

    def emergency_stop(self):
        self._state.force_stop()
        self._log("EMERGENCY STOP activated")

    def _run_loop(self):
        """Main agent execution loop with verification and retry."""
        self._log("Agent loop started")
        consecutive_failures = 0
        consecutive_denials = 0
        MAX_CONSECUTIVE_FAILURES = 5
        MAX_CONSECUTIVE_DENIALS = 3

        try:
            while self._state.state == AgentState.RUNNING:
                step = self._plan.current_step
                if step is None:
                    self._log("All steps completed!")
                    self._state.transition(AgentState.IDLE)
                    break

                # Update step tracking
                if step.status == "pending":
                    step.status = "in_progress"
                    step_idx = self._plan.steps.index(step)
                    self._state.current_step = step_idx
                    if self._on_step_change:
                        self._on_step_change(step_idx, step)
                    self._log(f"Step {step.index}: {step.description}")

                # Step timeout check
                if self._state.step_elapsed > self._step_timeout:
                    self._log(f"Step {step.index} timed out after {self._step_timeout}s")
                    step.status = "failed"
                    self._state.transition(AgentState.ERROR, f"Step {step.index} timed out")
                    if self._on_error:
                        self._on_error(f"Step {step.index} timed out")
                    break

                # Capture screen (before)
                before_screenshot = self._capture.capture()
                screenshot_b64 = self._capture.image_to_base64(before_screenshot)
                self._capture.save_audit(f"step_{step.index}_before")

                # Ask Claude what to do
                self._log("Consulting Claude...")
                action_response = self._get_next_action(step, screenshot_b64)

                if action_response is None:
                    consecutive_failures += 1
                    self._log(f"Failed to get action from Claude ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        self._state.transition(AgentState.ERROR, "Claude API repeatedly returning invalid responses")
                        if self._on_error:
                            self._on_error("Claude API repeatedly returning invalid responses")
                        break
                    time.sleep(max(0.5, self._loop_delay * consecutive_failures))  # Backoff with floor
                    continue
                consecutive_failures = 0

                reasoning = action_response.get("reasoning", "")
                action_data = action_response.get("action", {})
                step_complete = action_response.get("step_complete", False)
                confidence = action_response.get("confidence", 0.0)
                verify_spec = action_response.get("verify", {})

                self._log(f"Claude: {reasoning}")

                # Low confidence → pause
                if confidence < 0.5:
                    self._log(f"LOW CONFIDENCE ({confidence:.0%}). Pausing for review.")
                    self._state.transition(AgentState.PAUSED)
                    if self._on_error:
                        self._on_error(f"Low confidence: {reasoning}")
                    if not self._wait_if_paused():
                        break
                    continue

                # Route the action (execute BEFORE checking step_complete)
                action_name = action_data.get("name", "")
                action_params = action_data.get("params", {})
                result = None  # Initialize for step-complete check when template branch or safety-deny path runs

                if action_name:
                    # Handle template actions
                    if action_name == "template":
                        tmpl_result = self._execute_template(action_data.get("params", {}), step)
                        # Bind tmpl_result into result so the step-completion gate at the
                        # bottom of this iteration treats template success the same as
                        # native action success. Without this, successful templates loop forever.
                        result = tmpl_result
                        if tmpl_result and not tmpl_result.success:
                            self._log(f"Template failed, pausing: {tmpl_result.error}")
                            self._state.transition(AgentState.PAUSED)
                            if self._on_error:
                                self._on_error(f"Template failed: {tmpl_result.error}")
                    else:
                        action = Action(
                            name=action_name,
                            params=action_params,
                            description=reasoning,
                            step_index=step.index,
                        )

                        # Safety check
                        safety_check = self._safety.check_action(
                            action_name,
                            f"Step {step.index}: {reasoning}",
                        )

                        if self._on_action:
                            self._on_action("checking", action, None)

                        if safety_check.denied:
                            consecutive_denials += 1
                            self._log(f"Action DENIED ({consecutive_denials}/{MAX_CONSECUTIVE_DENIALS}): {safety_check.deny_reason}")
                            if consecutive_denials >= MAX_CONSECUTIVE_DENIALS:
                                self._log("Too many consecutive safety denials — pausing for user intervention")
                                self._state.transition(AgentState.PAUSED)
                                self._session.save_state(self._plan, self._state, conversation_history=self._conversation_history)
                                if self._on_error:
                                    self._on_error(f"Safety denied {consecutive_denials} consecutive actions. Check confirmation handler.")
                                if not self._wait_if_paused():
                                    break
                                consecutive_denials = 0
                                continue
                            if safety_check.tier == SafetyTier.DESTRUCTIVE:
                                self._state.transition(AgentState.PAUSED)
                            time.sleep(self._loop_delay)
                            continue

                        consecutive_denials = 0  # Reset on successful safety check
                        # Execute with retry
                        result = self._execute_with_retry(action, before_screenshot, verify_spec)

                        if self._on_action:
                            self._on_action("completed", action, result)

                        if not result.success:
                            self._log(f"Action failed after retries: {result.error}")

                # NOW check step completion (only if an action was actually dispatched)
                if step_complete and action_name and result and result.success:
                    step.status = "completed"
                    self._log(f"Step {step.index} COMPLETED")
                    self._capture.save_audit(f"step_{step.index}_complete")
                    self._conversation_history = []
                    # Persist session state on every step completion (crash recovery)
                    self._session.save_state(self._plan, self._state, conversation_history=self._conversation_history)
                    # Re-check UE5 connection periodically
                    if step.index % self._ue5_recheck_interval == 0:
                        self.check_ue5_connection()

                time.sleep(self._loop_delay)
                if not self._wait_if_paused():
                    break

        except Exception as e:
            logger.exception("Agent loop error")
            self._state.transition(AgentState.ERROR, str(e))
            self._session.save_state(self._plan, self._state, conversation_history=self._conversation_history)
            if self._on_error:
                self._on_error(str(e))

    def _execute_with_retry(
        self,
        action: Action,
        before_screenshot: Image.Image,
        verify_spec: dict,
        max_retries: int | None = None,
    ) -> ActionResult:
        """Execute an action with verification and retry."""
        retries = max_retries if max_retries is not None else self._max_retries

        for attempt in range(retries + 1):
            if self._on_action:
                self._on_action("executing", action, None)

            # Fresh before_screenshot per attempt (screen may have changed since first try)
            current_before = self._capture.capture() if attempt > 0 else before_screenshot

            result = self._executor.execute(action)

            # Push to undo stack on success
            if result.success:
                self._undo.push(
                    action.name, action.params,
                    step_index=action.step_index,
                    description=action.description,
                )

            if not result.success:
                if attempt < retries:
                    self._log(f"Retry {attempt+1}/{retries}: {result.error}")
                    time.sleep(0.5)
                    continue
                return result

            # Verify the action
            verify_method = verify_spec.get("method", "screen_changed")
            if verify_method == "none":
                return result

            time.sleep(0.3)  # Brief pause for screen to update
            after_screenshot = self._capture.capture()

            verification = self._verify_action(
                verify_method, verify_spec.get("params", {}),
                current_before, after_screenshot,
            )

            if verification.result == VerifyResult.PASS:
                self._log(f"Verified: {verification.details}")
                return result
            elif verification.result == VerifyResult.UNCERTAIN:
                self._log(f"Verification uncertain: {verification.details}")
                return result  # Accept uncertain results
            else:
                if attempt < retries:
                    self._log(f"Verification failed (attempt {attempt+1}): {verification.details}")
                    time.sleep(0.5)
                else:
                    self._log(f"Verification failed after {retries+1} attempts: {verification.details}")
                    result.success = False
                    result.error = f"Verification failed: {verification.details}"

        return result

    def _verify_action(self, method: str, params: dict, before, after):
        """Run verification based on spec from Claude's response."""
        if method == "screen_changed":
            return self._verifier.verify_screen_changed(before, after)
        elif method == "element_present":
            return self._verifier.verify_element_present(after, params.get("template_name", ""))
        elif method == "element_absent":
            return self._verifier.verify_element_absent(after, params.get("template_name", ""))
        elif method == "region_changed":
            return self._verifier.verify_region_changed(
                before, after,
                x=params.get("x", 0), y=params.get("y", 0),
                width=params.get("width", 100), height=params.get("height", 100),
            )
        else:
            return self._verifier.verify_screen_changed(before, after)

    def _execute_template(self, params: dict, step: PlanStep):
        """Execute a named action template with hybrid API/vision fallback."""
        template_name = params.get("template_name", "")
        template_params = params.get("params", {})

        template = get_template(template_name)
        if template is None:
            self._log(f"Template not found: {template_name}")
            return None

        self._log(f"Executing template: {template_name}")

        result = template.execute(
            bridge=self._ue5 if self._ue5.is_connected else None,
            detector=self._detector,
            verifier=self._verifier,
            executor_fn=lambda name, p: self._executor.execute_simple(name, p),
            capture_fn=self._capture_pil,
            params=template_params,
        )

        method_icon = {"api": "[API]", "vision": "[VIS]", "hybrid": "[HYB]"}.get(result.method, "[?]")
        self._log(f"  {method_icon} {template_name}: {'OK' if result.success else 'FAIL'} ({result.actions_taken} actions)")
        if result.error:
            self._log(f"  Error: {result.error}")
        return result

    def _wait_if_paused(self) -> bool:
        """Wait while paused. Returns True if still RUNNING, False if stopped/error."""
        while self._state.state == AgentState.PAUSED:
            time.sleep(0.1)
        return self._state.state == AgentState.RUNNING

    def _get_next_action(self, step: PlanStep, screenshot_b64: str) -> dict | None:
        """Ask Claude what action to take next."""
        # Build cache-disciplined system prompt: static block (cacheable, 1h TTL)
        # + volatile block (UE5 status + template list, uncacheable). Per v2.1
        # Prompt Cache Discipline — keep invariant prefix ahead of per-call data.
        ue5_status = "CONNECTED (API available)" if self._ue5.is_connected else "NOT CONNECTED (vision mode only)"
        tmpl_list = ", ".join(list_templates())
        # Strip dynamic placeholders from template — those land in the volatile block.
        static_system = AGENT_SYSTEM_PROMPT.split("## UE5 Connection Status:")[0].rstrip()
        volatile_system = (
            f"\n## UE5 Connection Status: {ue5_status}\n"
            f"## Available Templates: {tmpl_list}\n"
        )
        system_blocks = [
            {"type": "text", "text": static_system,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": volatile_system},
        ]

        step_context = (
            f"CURRENT STEP ({step.index}/{self._plan.total_steps}): {step.description}\n"
            f"INSTRUCTIONS: {step.detailed_instructions}\n"
            f"VERIFICATION: {step.verification}\n"
            f"IS DESTRUCTIVE: {step.is_destructive}\n"
            f"REQUIRES APP: {step.requires_app}\n\n"
            f"PLAN: {self._plan.name} - {self._plan.description}\n\n"
            "Analyze the screenshot and return ONLY valid JSON."
        )

        user_message = {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": screenshot_b64,
                    },
                },
                {"type": "text", "text": step_context},
            ],
        }

        self._conversation_history.append(user_message)

        # Trim history: keep last 10 messages (leave room for the assistant reply
        # that gets appended after the API call — keeps total at 11 max, not unbounded)
        if len(self._conversation_history) > 9:
            self._conversation_history = self._conversation_history[-9:]
        # Strip images from all but the most recent user message
        for msg in self._conversation_history[:-1]:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                msg["content"] = [
                    block for block in msg["content"] if block.get("type") != "image"
                ]

        # Circuit breaker: reject if API is in failure state
        if not self._circuit_breaker.can_proceed():
            logger.warning("Circuit breaker OPEN — skipping Claude API call")
            return None

        try:
            # Phase 4 (2026-04-16): Extended thinking block for SOTA reasoning.
            # M.4 (2026-04-23): Native Anthropic tool_use protocol replaces the
            # prior regex JSON-parse of Claude's text output. Claude now emits
            # a structured `take_action` tool call we read directly — no schema
            # drift, no regex, no JSONDecodeError recovery. Legacy regex path
            # remains as a final fallback for ancient SDK builds.
            # Extended-thinking budget counts toward max_tokens on Claude 3.7+.
            # Bump max_tokens by the thinking budget so the *output* still has
            # the configured ceiling (otherwise reasoning silently steals from
            # assistant output, capping it to ~max_tokens - budget tokens).
            _THINKING_BUDGET = 2048
            create_kwargs = {
                "model": self._model,
                "max_tokens": self._max_tokens + _THINKING_BUDGET,
                "temperature": self._temperature,
                "system": system_blocks,
                "messages": self._conversation_history,
            }

            tool_use_kwargs = {
                "tools": [_TAKE_ACTION_TOOL],
                "tool_choice": {"type": "tool", "name": "take_action"},
            }
            thinking_kwargs = {"thinking": {"type": "enabled", "budget_tokens": _THINKING_BUDGET}}

            response = None
            use_native_tool_use = True
            try:
                response = self._client.messages.create(
                    **create_kwargs, **tool_use_kwargs, **thinking_kwargs,
                )
            except TypeError:
                # SDK may not accept thinking OR tools — try each independently.
                try:
                    response = self._client.messages.create(
                        **create_kwargs, **tool_use_kwargs,
                    )
                except TypeError:
                    # No tool-use support either — fall back to legacy text-JSON path.
                    use_native_tool_use = False
                    try:
                        response = self._client.messages.create(
                            **create_kwargs, **thinking_kwargs,
                        )
                    except TypeError:
                        response = self._client.messages.create(**create_kwargs)

            self._circuit_breaker.record_success()

            # Extract reasoning text (for logging + history) from either tool_use
            # block input or text block (fallback path).
            reasoning_text = ""

            if use_native_tool_use:
                tool_use_block = None
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "take_action":
                        tool_use_block = block
                        break
                if tool_use_block is None:
                    logger.error(
                        "Native tool-use path: no `take_action` tool_use block found in response. "
                        "stop_reason=%s, blocks=%s",
                        getattr(response, "stop_reason", "?"),
                        [getattr(b, "type", "?") for b in response.content],
                    )
                    return None
                action_dict = dict(tool_use_block.input)  # Already parsed by SDK — no regex, no JSONDecodeError.
                reasoning_text = action_dict.get("reasoning", "")
            else:
                # Legacy path: regex JSON parse from text response.
                response_text = ""
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        response_text = block.text
                        break
                if not response_text and response.content:
                    response_text = getattr(response.content[0], "text", "") or ""
                try:
                    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                    json_str = json_match.group(1) if json_match else response_text
                    action_dict = json.loads(json_str.strip())
                    reasoning_text = action_dict.get("reasoning", "")
                except json.JSONDecodeError as e:
                    logger.error(f"Legacy JSON parse error: {e} — raw: {response_text[:500]}")
                    return None

            # Record a concise assistant turn in history (no raw tool_use payload —
            # keeps trim logic simple and avoids dangling tool_use_id references).
            summary = reasoning_text.strip() or "Action decided."
            self._conversation_history.append({
                "role": "assistant",
                "content": summary,
            })

            return action_dict

        except Exception as e:
            self._circuit_breaker.record_failure()
            logger.error(f"Claude API error: {e}")
            return None
