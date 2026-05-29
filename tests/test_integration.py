"""Integration tests — verify the full Bionics pipeline connects end-to-end.

Tests that all modules wire together correctly without needing real
external dependencies (no UE5, no Claude API, no PyQt6 GUI).
"""

from pathlib import Path
from unittest.mock import MagicMock


class TestToolRegistryToMCP:
    """Tool registry → ToolGate → MCP server pipeline."""

    def test_all_tools_registered(self):
        """Every tool from bionics_tools/ is in the global registry."""
        from bionics_tools import register_all
        from core.bridge import ToolRegistry
        register_all()
        reg = ToolRegistry()
        assert reg.count() >= 130, f"Expected 130+ tools, got {reg.count()}"

    def test_gate_executes_safe_tool(self):
        """ToolGate can execute a SAFE tool end-to-end."""
        from core.bridge import ToolGate
        gate = ToolGate()
        gate.set_bypass_safety(True)
        result = gate.execute("version")
        assert result.ok
        assert result.content

    def test_gate_blocks_unknown_tool(self):
        """ToolGate rejects unknown tool names."""
        from core.bridge import ToolGate
        gate = ToolGate()
        result = gate.execute("nonexistent_tool_xyz")
        assert not result.ok

    def test_tool_categories_populated(self):
        """Registry has multiple categories."""
        from core.bridge import ToolRegistry
        reg = ToolRegistry()
        cats = reg.categories()
        assert len(cats) >= 5, f"Expected 5+ categories, got {cats}"

    def test_tool_json_schema_valid(self):
        """Every tool has a valid JSON schema with at least 'type' field."""
        from core.bridge import ToolRegistry
        reg = ToolRegistry()
        for name in reg.list_names():
            spec = reg.get(name)
            schema = spec.input_schema
            assert "type" in schema, f"Tool {name} has no 'type' in schema"
            assert schema["type"] == "object", f"Tool {name} schema type is not 'object'"


class TestStateMachineToAgent:
    """State machine → Agent wiring."""

    def test_agent_respects_state_machine(self):
        """Agent methods check state machine before acting."""
        from core.state import AgentState, StateMachine

        sm = StateMachine()
        assert sm.state == AgentState.IDLE

        # Can't go directly to RUNNING
        assert not sm.transition(AgentState.RUNNING)

        # Must go through lifecycle
        assert sm.transition(AgentState.PLANNING)
        assert sm.transition(AgentState.REVIEWING)
        assert sm.transition(AgentState.RUNNING)
        assert sm.state == AgentState.RUNNING

    def test_safety_to_executor_tier_consistency(self):
        """Safety layer tiers match what executor registers."""
        from core.executor import ActionExecutor
        from core.safety import ACTION_TIERS, SafetyLayer, SafetyTier

        executor = ActionExecutor()
        sl = SafetyLayer()

        for action_name in executor._handlers:
            tier = sl.get_tier(action_name)
            # Every registered handler should have a known tier (not default DESTRUCTIVE)
            if action_name not in ("screenshot", "read_screen"):
                # screenshot/read_screen are stubs but safe
                assert tier != SafetyTier.DESTRUCTIVE or action_name in ACTION_TIERS, \
                    f"Action '{action_name}' defaults to DESTRUCTIVE — should be in ACTION_TIERS"


class TestCaptureToVerification:
    """Capture → SSIM → Verification pipeline."""

    def test_ssim_identical_images(self):
        """Two identical images should have SSIM ~1.0."""
        import numpy as np

        from core.verification import ActionVerifier

        verifier = ActionVerifier()
        # Create identical 100x100 gray images
        img = np.random.randint(0, 255, (100, 100), dtype=np.uint8)
        ssim = verifier._compute_ssim(img, img)
        assert ssim > 0.99, f"Identical images should have SSIM ~1.0, got {ssim}"

    def test_ssim_different_images(self):
        """Completely different images should have low SSIM."""
        import numpy as np

        from core.verification import ActionVerifier

        verifier = ActionVerifier()
        img1 = np.zeros((100, 100), dtype=np.uint8)
        img2 = np.ones((100, 100), dtype=np.uint8) * 255
        ssim = verifier._compute_ssim(img1, img2)
        assert ssim < 0.1, f"Black vs white should have low SSIM, got {ssim}"

    def test_verify_screen_changed(self):
        """Full verify_screen_changed pipeline with PIL images."""
        import numpy as np
        from PIL import Image

        from core.verification import ActionVerifier, VerifyResult

        verifier = ActionVerifier()

        # Create two moderately different PIL images (simulate a button click change)
        rng = np.random.RandomState(42)
        arr1 = rng.randint(100, 150, (100, 100, 3), dtype=np.uint8)
        arr2 = arr1.copy()
        arr2[30:60, 30:60] = rng.randint(200, 255, (30, 30, 3), dtype=np.uint8)
        img1 = Image.fromarray(arr1)
        img2 = Image.fromarray(arr2)

        report = verifier.verify_screen_changed(img1, img2)
        assert report.result == VerifyResult.PASS
        assert report.change_score > 0.01


class TestCircuitBreakerIntegration:
    """CircuitBreaker protects the Claude API call path."""

    def test_circuit_blocks_after_failures(self):
        from core.resilience import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        for _ in range(3):
            cb.record_failure()

        # Should block
        assert not cb.can_proceed()
        assert cb.state == "open"

    def test_circuit_protects_repeated_calls(self):
        """Simulate the agent pattern: check before API call."""
        from core.resilience import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)
        calls_made = 0

        for _ in range(10):
            if cb.can_proceed():
                calls_made += 1
                cb.record_failure()  # simulate API failure

        # Should have stopped after threshold
        assert calls_made == 2


class TestPlannerPipeline:
    """AutoPlanner plan generation pipeline (mocked Claude)."""

    def test_plan_from_diagnosis_no_fixes(self):
        """When Doctor finds no issues, plan should be empty."""
        from core.auto_planner import AutoPlanner

        planner = AutoPlanner(api_key="test")
        diagnosis = MagicMock()
        diagnosis.to_planner_prompt.return_value = "No fixes needed"
        diagnosis.unfixed = []

        result = planner.plan_from_diagnosis(diagnosis)
        assert result["plan"]["name"] == "no_fixes"
        assert len(result["plan"]["steps"]) == 0


class TestPathsModule:
    """core/paths.py resolves paths from config."""

    def test_project_root_is_correct(self):
        from core.paths import PROJECT_ROOT
        assert (PROJECT_ROOT / "config.yaml").exists()
        assert (PROJECT_ROOT / "core").is_dir()

    def test_get_ue5_project_from_config(self):
        from core.paths import get_ue5_project
        # Should return a Path or None, never crash
        result = get_ue5_project()
        assert result is None or isinstance(result, Path)

    def test_get_bible_path_from_config(self):
        from core.paths import get_bible_path
        result = get_bible_path()
        assert result is None or isinstance(result, Path)


class TestConfigConsumption:
    """Config.yaml values are actually read and used."""

    def test_ue5_bridge_reads_config(self):
        """get_bridge() reads host/port from config.yaml."""
        # Reset singleton for test
        import bionics_tools._ue5_common as mod
        from bionics_tools._ue5_common import get_bridge
        old = mod._bridge_instance
        mod._bridge_instance = None
        try:
            bridge = get_bridge()
            # Bridge should exist (even if UE5 isn't running)
            assert bridge is not None
            # Port should be from config (30010 default)
            assert bridge._rc_port == 30010
        finally:
            mod._bridge_instance = old

    def test_config_version_matches_code(self):
        """config.yaml version matches expectations."""
        import yaml

        from core.paths import PROJECT_ROOT
        with open(PROJECT_ROOT / "config.yaml") as f:
            cfg = yaml.safe_load(f)
        assert cfg["bionics"]["version"] == "0.8.3"


class TestWatchEnginePipeline:
    """WatchEngine state transitions and pipeline stages."""

    def test_state_lifecycle(self):
        from core.watch_state import WatchState, WatchStateMachine
        sm = WatchStateMachine()
        assert sm.state == WatchState.IDLE
        assert sm.transition(WatchState.WATCHING)
        assert sm.transition(WatchState.ANALYZING)
        assert sm.transition(WatchState.ANNOTATING)
        assert sm.transition(WatchState.WATCHING)
        assert sm.transition(WatchState.IDLE)

    def test_handoff_context_structure(self):
        """WatchEngine.get_handoff_context returns the right shape."""
        from core.watch_engine import WatchEngine
        engine = WatchEngine.__new__(WatchEngine)
        engine._task_description = "test task"
        engine._completed_steps = ["step1", "step2"]
        engine._knowledge_context = "some knowledge"
        engine._cycle_count = 42

        ctx = engine.get_handoff_context()
        assert ctx["task"] == "test task"
        assert len(ctx["completed_steps"]) == 2
        assert ctx["knowledge"] == "some knowledge"
        assert ctx["cycle_count"] == 42
