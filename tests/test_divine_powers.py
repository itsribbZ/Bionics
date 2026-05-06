"""Tests for the divine_powers MCP tool wrapper (v0.7.0).

The wrapper exposes core.auto_planner.AutoPlanner.divine_powers as a
@bionics_tool — the headline NL→UE5 entry point that was previously
locked behind a Python API with no agent-facing door (audit 2026-05-02).
"""
from unittest.mock import MagicMock, patch

import pytest


# Ensure registration runs before any test in this module
@pytest.fixture(scope="module", autouse=True)
def _register_tools():
    from bionics_tools import register_all
    register_all()


class TestDivinePowersRegistration:
    """Tool is registered with correct shape."""

    def test_tool_registered(self):
        from core.bridge import get_registry
        spec = get_registry().get("divine_powers")
        assert spec is not None, "divine_powers must be registered after register_all()"

    def test_safety_tier_is_destructive(self):
        from core.bridge import SafetyTier, get_registry
        spec = get_registry().get("divine_powers")
        assert spec.safety_tier == SafetyTier.DESTRUCTIVE
        assert spec.annotations.destructive is True

    def test_category_is_bionics(self):
        from core.bridge import get_registry
        spec = get_registry().get("divine_powers")
        assert spec.category == "bionics"

    def test_input_schema_has_prompt_and_execute(self):
        from core.bridge import get_registry
        spec = get_registry().get("divine_powers")
        schema = spec.input_schema
        props = schema.get("properties", {})
        assert "prompt" in props, "prompt arg required"
        assert "execute" in props, "execute arg required"
        assert "prompt" in schema.get("required", [])
        # execute is not required (defaults False)
        assert "execute" not in schema.get("required", [])

    def test_output_schema_covers_pipeline_outputs(self):
        from core.bridge import get_registry
        spec = get_registry().get("divine_powers")
        out = spec.output_schema or {}
        props = out.get("properties", {})
        for key in ("prompt", "topics", "diagnosis", "plan",
                    "execution_results", "executed", "bridge_status"):
            assert key in props, f"output_schema missing '{key}'"


class TestDivinePowersBehavior:
    """Wrapper delegates correctly and handles bridge presence/absence."""

    def _canned_planner_result(self):
        return {
            "prompt": "fix the AnimBP T-pose",
            "topics": ["ANIMATION"],
            "diagnosis": {"findings": [{"id": "ap01", "severity": "high"}]},
            "plan": {"steps": [{"action": "create_animgraph_node"},
                                {"action": "wire_animgraph_pins"}]},
            "plan_path": "/tmp/plan.json",
            "execution_results": [],
            "demo_ready": False,
            "run_id": "test-run-001",
            "ecosystem_context": {
                "ue_knowledge_zones": ["Z1"],
                "author_chain": ["B:11", "U:P5"],
                "voyager_warm_start": {"proven": [], "similar": []},
            },
        }

    @patch("core.paths.get_ue5_project", return_value=None)
    @patch("core.auto_planner.AutoPlanner")
    def test_plan_only_mode_no_bridge_attempted(self, mock_planner_cls, _mock_proj):
        mock_planner = MagicMock()
        mock_planner.divine_powers.return_value = self._canned_planner_result()
        mock_planner_cls.return_value = mock_planner

        from bionics_tools.bionics_core import divine_powers
        result = divine_powers(prompt="fix the AnimBP T-pose", execute=False)

        assert result.ok is True
        assert result.data["executed"] is False
        assert result.data["bridge_status"] == "not_attempted"
        # Planner was called with bridge=None
        mock_planner.divine_powers.assert_called_once()
        kwargs = mock_planner.divine_powers.call_args.kwargs
        assert kwargs["prompt"] == "fix the AnimBP T-pose"
        assert kwargs["bridge"] is None

    @patch("core.paths.get_ue5_project", return_value=None)
    @patch("core.auto_planner.AutoPlanner")
    @patch("core.ue5_bridge.UE5Bridge")
    def test_execute_true_bridge_unreachable_falls_back_to_plan_only(
        self, mock_bridge_cls, mock_planner_cls, _mock_proj
    ):
        # Simulate bridge probe but is_connected = False
        from core.ue5_bridge import ConnectionStatus
        mock_bridge = MagicMock()
        mock_bridge.is_connected = False
        mock_bridge.status = ConnectionStatus.EDITOR_NOT_RUNNING
        mock_bridge_cls.return_value = mock_bridge

        mock_planner = MagicMock()
        mock_planner.divine_powers.return_value = self._canned_planner_result()
        mock_planner_cls.return_value = mock_planner

        from bionics_tools.bionics_core import divine_powers
        result = divine_powers(prompt="fix tpose", execute=True)

        assert result.ok is True
        assert result.data["executed"] is False, "should not execute when bridge not connected"
        assert result.data["bridge_status"] in ("editor_not_running", "EDITOR_NOT_RUNNING")
        # Planner still called, but bridge=None
        kwargs = mock_planner.divine_powers.call_args.kwargs
        assert kwargs["bridge"] is None

    @patch("core.paths.get_ue5_project", return_value=None)
    @patch("core.auto_planner.AutoPlanner")
    @patch("core.ue5_bridge.UE5Bridge")
    def test_execute_true_bridge_connected_passes_bridge_to_planner(
        self, mock_bridge_cls, mock_planner_cls, _mock_proj
    ):
        mock_bridge = MagicMock()
        mock_bridge.is_connected = True
        mock_bridge_cls.return_value = mock_bridge

        mock_planner = MagicMock()
        mock_planner.divine_powers.return_value = self._canned_planner_result()
        mock_planner_cls.return_value = mock_planner

        from bionics_tools.bionics_core import divine_powers
        result = divine_powers(prompt="wire BP_Sword", execute=True)

        assert result.ok is True
        assert result.data["executed"] is True
        assert result.data["bridge_status"] == "connected"
        kwargs = mock_planner.divine_powers.call_args.kwargs
        assert kwargs["bridge"] is mock_bridge

    @patch("core.paths.get_ue5_project", return_value=None)
    @patch("core.auto_planner.AutoPlanner")
    def test_planner_exception_returns_failure_not_crash(self, mock_planner_cls, _mock_proj):
        mock_planner = MagicMock()
        mock_planner.divine_powers.side_effect = RuntimeError("synthetic planner failure")
        mock_planner_cls.return_value = mock_planner

        from bionics_tools.bionics_core import divine_powers
        result = divine_powers(prompt="anything", execute=False)

        assert result.ok is False
        assert "synthetic planner failure" in (result.error or "") or \
               "synthetic planner failure" in (result.content or "")

    @patch("core.paths.get_ue5_project", return_value=None)
    @patch("core.auto_planner.AutoPlanner")
    def test_content_summary_includes_topic_and_counts(self, mock_planner_cls, _mock_proj):
        mock_planner = MagicMock()
        mock_planner.divine_powers.return_value = self._canned_planner_result()
        mock_planner_cls.return_value = mock_planner

        from bionics_tools.bionics_core import divine_powers
        result = divine_powers(prompt="fix tpose", execute=False)

        assert "ANIMATION" in result.content
        assert "findings=1" in result.content
        assert "plan_steps=2" in result.content
        assert "executed=False" in result.content


class TestExecutePlanStepsObservability:
    """v0.7.5 silent-failure fix — patch-hint detection + empty-error backstop.

    Caught live-fire 2026-05-03: divine_powers(execute=True) returned
    executed=True, bridge_status=connected, but every step had
    {success: false, output: "", error: ""} — unobservable silent failure.
    Root causes: (1) C++ patch-hint steps were being executed as Python and
    returning False with no signal; (2) bridge.execute_python could return
    success=False with empty error string. Both fixed in v0.7.5.
    """

    def _make_planner(self):
        from core.auto_planner import AutoPlanner
        return AutoPlanner(ue5_project_path="", api_key="dummy")

    def test_cpp_patch_hint_step_skipped_with_success_none(self):
        """Steps prefixed [C++ PATCH HINT] are NOT executed — recorded as skip."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": "[C++ PATCH HINT] Bind player OnDied delegate",
            "script_content": "# C++ patch — modify EXOGameMode.cpp manually",
        }]}
        results = self._make_planner()._execute_plan_steps(plan, bridge)
        assert len(results) == 1
        assert results[0]["success"] is None, "patch hint must NOT report success=False"
        assert "patch hint" in results[0]["note"].lower()
        bridge.execute_python.assert_not_called(), "patch hint must NOT call the bridge"

    def test_cpp_edit_prefix_variant_also_skipped(self):
        """v0.7.7: planner also uses [C++ EDIT] (live-fired 2026-05-03) — same skip path."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": "[C++ EDIT] Add SpawnLootDrop() call inside SWEnemyBase::HandleDeath",
            "script_content": "# Manual edit — see SWEnemyBase.cpp:240",
        }]}
        results = self._make_planner()._execute_plan_steps(plan, bridge)
        assert results[0]["success"] is None
        bridge.execute_python.assert_not_called()

    def test_all_comment_script_skipped_with_success_none(self):
        """script_content with no executable lines (all comments/whitespace) is a skip."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": "Refactor task — see comments",
            "script_content": "# step 1: open file\n# step 2: replace string\n   \n",
        }]}
        results = self._make_planner()._execute_plan_steps(plan, bridge)
        assert results[0]["success"] is None
        bridge.execute_python.assert_not_called()

    def test_real_python_step_still_executes(self):
        """Sanity: actual Python content (not comments, no patch-hint prefix) executes."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        bridge.execute_python.return_value = MagicMock(
            success=True, error="", data={"output": [{"output": "ok"}]}
        )
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": "Spawn light",
            "script_content": "import unreal\nunreal.EditorActorSubsystem().get_all_level_actors()",
        }]}
        results = self._make_planner()._execute_plan_steps(plan, bridge)
        assert results[0]["success"] is True
        assert results[0]["output"] == "ok"
        bridge.execute_python.assert_called_once()

    def test_empty_error_with_output_synthesizes_error_with_output(self):
        """When bridge returns success=False + empty error + some output, synthesize."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        bridge.execute_python.return_value = MagicMock(
            success=False, error="",
            data={"output": [{"output": "Traceback inferred from log: NameError"}]},
        )
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": "Apply fix",
            "script_content": "import unreal\nfoo()",
        }]}
        results = self._make_planner()._execute_plan_steps(plan, bridge)
        assert results[0]["success"] is False
        assert results[0]["error"] != "", "empty error must be replaced by synthesized one"
        assert "NameError" in results[0]["error"], \
            "synthesized error should include captured output"

    def test_empty_error_no_output_synthesizes_diagnostic_error(self):
        """When bridge returns success=False + empty error + no output, surface a diagnostic."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        bridge.execute_python.return_value = MagicMock(
            success=False, error="", data={"output": []},
        )
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": "Fix mobility",
            "script_content": "import unreal\nunreal.do_something()",
        }]}
        results = self._make_planner()._execute_plan_steps(plan, bridge)
        assert results[0]["success"] is False
        assert "no error message" in results[0]["error"]
        assert "no output" in results[0]["error"]


class TestGeneratePlanJSONRecovery:
    """v0.7.6 retry-on-malformed-JSON fix.

    Live-fire 2026-05-03 caught: divine_powers crashed with
    `ValueError: Failed to generate valid plan: Unterminated string starting at:
    line 83 column 25 (char 25697)` because Claude's plan response was
    truncated mid-script_content at max_tokens=8192. Bumped to 16384 + added
    single repair-retry; final failure reports stop_reason + both errors so
    the operator can diagnose, instead of an opaque crash.
    """

    def _planner_with_mocked_client(self, *responses, tmp_root):
        """Build a planner whose Anthropic client returns the given canned responses
        (each a (text, stop_reason) tuple) on successive .messages.create() calls.

        tmp_root: a Path the planner can safely write its plan_<timestamp>.json under
                  (provided by pytest's tmp_path fixture per-test for isolation).
        """
        from unittest.mock import MagicMock

        from core.auto_planner import AutoPlanner

        def _make_resp(text: str, stop: str = "end_turn"):
            r = MagicMock()
            r.content = [MagicMock(text=text)]
            r.stop_reason = stop
            return r

        client = MagicMock()
        client.messages.create.side_effect = [_make_resp(t, s) for t, s in responses]

        planner = AutoPlanner(ue5_project_path="", api_key="dummy")
        planner._client = client  # bypass real client lazy-init

        # Redirect generate_plan's plan-save side-effect to tmp_root.
        # generate_plan does `from core.paths import PROJECT_ROOT` then writes
        # to `PROJECT_ROOT / "plans" / "auto_*.json"`. Patching the source module
        # attribute correctly redirects that write because the import grabs the
        # current module-level binding at call time.
        import core.paths as _paths_mod
        _paths_mod._original_PROJECT_ROOT = _paths_mod.PROJECT_ROOT
        _paths_mod.PROJECT_ROOT = tmp_root

        return planner, client

    def _restore_project_root(self):
        import core.paths as _paths_mod
        if hasattr(_paths_mod, "_original_PROJECT_ROOT"):
            _paths_mod.PROJECT_ROOT = _paths_mod._original_PROJECT_ROOT
            delattr(_paths_mod, "_original_PROJECT_ROOT")

    def test_first_call_returns_valid_json_no_retry(self, tmp_path):
        """Happy path: planner returns valid JSON on first try, no retry triggered."""
        valid = '{"name": "p", "description": "d", "steps": [], "prerequisites": [], "warnings": [], "estimated_time_seconds": 10, "rollback_strategy": "n/a"}'
        planner, client = self._planner_with_mocked_client((valid, "end_turn"), tmp_root=tmp_path)
        try:
            result = planner.generate_plan("test prompt", deep_research=False, divine=False)
            assert result["plan"]["name"] == "p"
            assert client.messages.create.call_count == 1, "no retry expected on valid JSON"
        finally:
            self._restore_project_root()

    def test_malformed_json_triggers_repair_retry_and_succeeds(self, tmp_path):
        """Malformed first response → repair retry → valid second response → success."""
        malformed = '{"name": "p", "steps": [{"script_content": "unterminated'
        valid = '{"name": "p_repaired", "description": "d", "steps": [], "prerequisites": [], "warnings": [], "estimated_time_seconds": 10, "rollback_strategy": "n/a"}'
        planner, client = self._planner_with_mocked_client(
            (malformed, "max_tokens"),
            (valid, "end_turn"),
            tmp_root=tmp_path,
        )
        try:
            result = planner.generate_plan("test prompt", deep_research=True, divine=False)
            assert result["plan"]["name"] == "p_repaired"
            assert client.messages.create.call_count == 2, "exactly one retry expected"
        finally:
            self._restore_project_root()

    def test_both_calls_malformed_raises_structured_error(self, tmp_path):
        """Malformed first AND second → ValueError with both error reasons surfaced."""
        malformed1 = '{"name": "p", "steps": [{"script_content": "broken'
        malformed2 = '{"name": "p", "steps": [{"unterminated'
        planner, client = self._planner_with_mocked_client(
            (malformed1, "max_tokens"),
            (malformed2, "max_tokens"),
            tmp_root=tmp_path,
        )
        try:
            with pytest.raises(ValueError) as exc:
                planner.generate_plan("test prompt", deep_research=True, divine=False)
            msg = str(exc.value)
            assert "after retry" in msg
            assert "stop_reason=max_tokens" in msg
            assert "original_error" in msg
            assert "retry_error" in msg
            assert client.messages.create.call_count == 2, "exactly one retry expected"
        finally:
            self._restore_project_root()
