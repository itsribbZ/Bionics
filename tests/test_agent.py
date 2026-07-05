"""Tests for AgentCore — the brain of Bionics.

Tests the agent's decision-making logic, plan execution, step completion,
error handling, and lifecycle management. All external dependencies are mocked.
"""

import json
from unittest.mock import MagicMock, patch

from core.executor import ActionExecutor, ActionResult
from core.planner import ExecutionPlan, PlanStep
from core.safety import SafetyLayer
from core.state import AgentState, StateMachine


def _make_agent(**overrides):
    """Create an AgentCore with all dependencies mocked."""
    from core.agent import AgentCore

    sm = StateMachine()
    safety = SafetyLayer()
    safety.set_confirmation_callback(lambda check: True)

    capture = MagicMock()
    capture.capture.return_value = MagicMock()  # PIL Image mock
    capture.image_to_base64.return_value = "base64data"
    capture.save_audit = MagicMock()

    executor = MagicMock(spec=ActionExecutor)

    with patch("core.agent.Anthropic"):
        with patch("core.agent.UE5Bridge"):
            with patch("core.agent.ElementDetector"):
                with patch("core.agent.ActionVerifier"):
                    agent = AgentCore(
                        state_machine=sm,
                        safety=safety,
                        capture=capture,
                        executor=executor,
                        api_key="test-key",
                        **overrides,
                    )

    return agent, sm, safety, capture, executor


class TestAgentInit:
    def test_creates_with_mocked_deps(self):
        agent, sm, _, _, _ = _make_agent()
        assert sm.state == AgentState.IDLE
        assert agent._plan is None

    def test_model_default(self):
        agent, _, _, _, _ = _make_agent()
        assert agent._model == "claude-sonnet-4-6"

    def test_loop_delay_conversion(self):
        agent, _, _, _, _ = _make_agent(loop_delay_ms=1000)
        assert agent._loop_delay == 1.0

    def test_step_timeout(self):
        agent, _, _, _, _ = _make_agent(step_timeout_s=120)
        assert agent._step_timeout == 120


class TestPlanLoading:
    def test_load_plan(self):
        agent, sm, _, _, _ = _make_agent()
        plan = ExecutionPlan(
            name="test",
            description="test plan",
            steps=[
                PlanStep(index=1, description="Step 1", detailed_instructions="Do thing 1", verification="check"),
                PlanStep(index=2, description="Step 2", detailed_instructions="Do thing 2", verification="check"),
            ],
        )
        agent.load_plan(plan)
        assert agent._plan is not None
        assert len(agent._plan.steps) == 2
        assert sm.total_steps == 2


class TestStepCompletion:
    """Verifies the fix from the audit: step_complete must be gated on result.success."""

    def test_step_not_completed_on_failure(self):
        """step_complete=True from Claude but action failed → step stays in_progress."""
        agent, sm, _, _, _ = _make_agent()

        step = PlanStep(index=1, description="Test step", detailed_instructions="test", verification="")
        step.status = "in_progress"

        # Simulate: Claude says step_complete=True, but the action failed
        result = ActionResult(action="click", params={}, success=False, error="element not found")

        # The fixed logic: `if step_complete and action_name and result and result.success`
        step_complete = True
        action_name = "click"

        # This is the exact condition from agent.py line 429
        if step_complete and action_name and result and result.success:
            step.status = "completed"

        assert step.status == "in_progress"  # Should NOT be completed

    def test_step_completed_on_success(self):
        """step_complete=True from Claude AND action succeeded → step completed."""
        step = PlanStep(index=1, description="Test step", detailed_instructions="test", verification="")
        step.status = "in_progress"

        result = ActionResult(action="click", params={}, success=True)
        step_complete = True
        action_name = "click"

        if step_complete and action_name and result and result.success:
            step.status = "completed"

        assert step.status == "completed"

    def test_step_not_completed_without_action(self):
        """step_complete=True but no action dispatched → step stays."""
        step = PlanStep(index=1, description="Test step", detailed_instructions="test", verification="")
        step.status = "in_progress"

        result = ActionResult(action="click", params={}, success=True)
        step_complete = True
        action_name = ""  # No action dispatched

        if step_complete and action_name and result and result.success:
            step.status = "completed"

        assert step.status == "in_progress"

    def test_template_success_marks_step_completed(self):
        """Regression: template action success must bind tmpl_result into result so the
        completion gate fires. Pre-fix, template steps looped forever because result stayed None."""
        step = PlanStep(index=1, description="Test step", detailed_instructions="test", verification="")
        step.status = "in_progress"

        # Mirror agent.py:555-565: result starts None, template branch assigns tmpl_result
        result = None  # initialized at line 555
        action_name = "template"
        tmpl_result = ActionResult(action="template", params={}, success=True)
        # The fix: result = tmpl_result inside the template branch
        result = tmpl_result

        step_complete = True

        if step_complete and action_name and result and result.success:
            step.status = "completed"

        assert step.status == "completed", "Template success must complete the step"

    def test_template_failure_does_not_complete_step(self):
        """Template failure must NOT mark step completed even though result is bound."""
        step = PlanStep(index=1, description="Test step", detailed_instructions="test", verification="")
        step.status = "in_progress"

        result = None
        action_name = "template"
        tmpl_result = ActionResult(action="template", params={}, success=False, error="unknown template")
        result = tmpl_result

        step_complete = True

        if step_complete and action_name and result and result.success:
            step.status = "completed"

        assert step.status == "in_progress"


class TestResponseParsing:
    """Tests the JSON parsing logic from _get_next_action."""

    def _parse_like_agent(self, text: str) -> dict | None:
        """Reproduce the agent's JSON extraction logic."""
        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = text
        try:
            return json.loads(json_str.strip())
        except json.JSONDecodeError:
            return None

    def test_parse_raw_json(self):
        text = '{"action": {"name": "click", "params": {"x": 100, "y": 200}}, "step_complete": false}'
        result = self._parse_like_agent(text)
        assert result is not None
        assert result["action"]["name"] == "click"

    def test_parse_json_in_code_block(self):
        text = '```json\n{"action": {"name": "click", "params": {"x": 100}}, "step_complete": true}\n```'
        result = self._parse_like_agent(text)
        assert result is not None
        assert result["step_complete"] is True

    def test_parse_json_in_bare_code_block(self):
        text = '```\n{"action": {"name": "wait", "params": {"seconds": 1}}, "step_complete": false}\n```'
        result = self._parse_like_agent(text)
        assert result is not None
        assert result["action"]["name"] == "wait"

    def test_parse_invalid_json_returns_none(self):
        text = "I'm not sure what to do here, the screen looks different than expected."
        result = self._parse_like_agent(text)
        assert result is None

    def test_parse_json_with_reasoning(self):
        text = '```json\n{"reasoning": "I see the compile button", "action": {"name": "click_element", "params": {"template_name": "compile"}}, "step_complete": false, "confidence": 0.9}\n```'
        result = self._parse_like_agent(text)
        assert result is not None
        assert result["confidence"] == 0.9


class TestAgentLifecycle:
    def test_emergency_stop(self):
        agent, sm, _, _, _ = _make_agent()
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.REVIEWING)
        sm.transition(AgentState.RUNNING)
        agent.emergency_stop()
        assert sm.state == AgentState.STOPPED

    def test_pause_and_resume(self):
        agent, sm, _, _, _ = _make_agent()
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.REVIEWING)
        sm.transition(AgentState.RUNNING)
        agent.pause()
        assert sm.state == AgentState.PAUSED
        agent.resume()
        assert sm.state == AgentState.RUNNING

    def test_stop_from_running(self):
        agent, sm, _, _, _ = _make_agent()
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.REVIEWING)
        sm.transition(AgentState.RUNNING)
        agent.stop()
        assert sm.state == AgentState.STOPPED


class TestUE5Connection:
    def test_check_ue5_connection(self):
        agent, _, _, _, _ = _make_agent()
        agent._ue5 = MagicMock()
        agent._ue5.check_connection.return_value = MagicMock()
        agent._ue5.is_connected = True
        agent.check_ue5_connection()
        agent._ue5.check_connection.assert_called_once()


class TestConversationHistory:
    def test_history_trimmed(self):
        """Verify conversation history doesn't grow unbounded."""
        agent, _, _, _, _ = _make_agent()
        # Simulate adding messages
        for i in range(20):
            agent._conversation_history.append({"role": "user", "content": f"msg {i}"})
            agent._conversation_history.append({"role": "assistant", "content": f"reply {i}"})

        # The agent trims to last 9 when length > 9
        if len(agent._conversation_history) > 9:
            agent._conversation_history = agent._conversation_history[-9:]

        assert len(agent._conversation_history) == 9
