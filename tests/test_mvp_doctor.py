"""Tests for MVP Doctor — verifies diagnostic checks produce correct findings."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.mvp_doctor import (
    Category,
    Diagnosis,
    Finding,
    FixMethod,
    MVPDoctor,
    Severity,
)


def test_diagnosis_data_model():
    """Test Diagnosis and Finding data structures."""
    f1 = Finding(
        id="TEST_CRITICAL",
        title="Critical test finding",
        description="Something critical is broken",
        severity=Severity.CRITICAL,
        category=Category.COMBAT,
        fix_method=FixMethod.CPP_EDIT,
        fix_hint="Fix the thing",
        auto_fixable=False,
    )
    f2 = Finding(
        id="TEST_INFO",
        title="Info finding",
        description="FYI",
        severity=Severity.INFO,
        category=Category.COMPILE,
    )

    d = Diagnosis(findings=[f1, f2], checks_run=2, checks_passed=1)

    assert d.critical_count == 1
    assert d.high_count == 0
    assert not d.is_demo_ready  # has unfixed CRITICAL
    assert len(d.unfixed) == 2

    # Mark critical as fixed
    f1.fixed = True
    assert d.is_demo_ready  # only INFO left

    # Serialization
    data = d.to_dict()
    assert data["demo_ready"] is True
    assert len(data["findings"]) == 2

    # Planner prompt
    f1.fixed = False
    prompt = d.to_planner_prompt()
    assert "CRITICAL" in prompt
    assert "TEST_CRITICAL" not in prompt  # IDs aren't in prompt, titles are
    assert "Critical test finding" in prompt
    print("  data_model: PASS")


def test_finding_to_planner_prompt():
    """Test Finding -> planner prompt conversion."""
    f = Finding(
        id="X",
        title="Broken widget",
        description="The widget doesn't work",
        severity=Severity.HIGH,
        category=Category.WIRING,
        fix_method=FixMethod.UE5_PYTHON,
        fix_hint="Run fix_widget.py",
        file_path="C:/src/widget.cpp",
        line_number=42,
    )
    prompt = f.to_planner_prompt()
    assert "HIGH" in prompt
    assert "Broken widget" in prompt
    assert "Run fix_widget.py" in prompt
    assert "widget.cpp:42" in prompt
    print("  finding_prompt: PASS")


def test_doctor_runs_on_real_project():
    """Run the doctor against a real UE5 project (static checks only).

    Uses BIONICS_TEST_UE5_PROJECT env var or paths.ue5_project from config.yaml.
    Skips cleanly if no project is configured or the path doesn't exist.
    """
    import os
    project_path = os.environ.get("BIONICS_TEST_UE5_PROJECT", "")
    if not project_path:
        try:
            from core.paths import get_ue5_project
            proj = get_ue5_project()
            project_path = str(proj) if proj else ""
        except Exception:
            project_path = ""
    if not project_path or not Path(project_path).exists():
        print("  real_project: SKIP (no UE5 project configured — set BIONICS_TEST_UE5_PROJECT or paths.ue5_project)")
        return

    doctor = MVPDoctor(ue5_project_path=project_path)
    diagnosis = doctor.diagnose()

    assert diagnosis.checks_run > 0, "Should run at least some checks"
    assert isinstance(diagnosis.findings, list)

    print(f"  real_project: PASS ({diagnosis.checks_run} checks, {len(diagnosis.findings)} findings)")
    print(diagnosis.summary())


def test_diagnosis_summary_format():
    """Test that summary is well-formatted."""
    d = Diagnosis(checks_run=5, checks_passed=3)
    d.findings = [
        Finding(id="A", title="A", description="a", severity=Severity.CRITICAL, category=Category.AI),
        Finding(id="B", title="B", description="b", severity=Severity.HIGH, category=Category.COMBAT),
    ]
    summary = d.summary()
    assert "CRITICAL" in summary
    assert "Demo ready: NO" in summary
    assert "Blockers" in summary
    print("  summary_format: PASS")


def test_planner_integration_format():
    """Test that to_planner_prompt produces AutoPlanner-compatible output."""
    d = Diagnosis(checks_run=1, checks_passed=0)
    d.findings = [
        Finding(
            id="FIX_ME",
            title="Fix this",
            description="It's broken",
            severity=Severity.HIGH,
            category=Category.WIRING,
            fix_method=FixMethod.UE5_PYTHON,
            fix_hint="Run a script",
            auto_fixable=True,
        ),
    ]
    prompt = d.to_planner_prompt()
    assert "MVP DOCTOR DIAGNOSIS" in prompt
    assert "Fix the following issues" in prompt
    assert "Generate a Bionics execution plan" in prompt
    assert "ue5_python" in prompt or "Run a script" in prompt
    print("  planner_format: PASS")


def test_diagnose_failclosed_unregistered_category():
    """P0: diagnose() must NOT report demo_ready on a category with zero checks.

    MOVEMENT has zero @check decorators today (ASSET now has two). The old code ran 0
    checks and returned demo_ready=True (vacuous all() over []), handing the pipeline gate
    a green light from no validation. The fail-closed guard turns that into a CRITICAL.
    """
    doctor = MVPDoctor(ue5_project_path=".")
    diag = doctor.diagnose(categories=[Category.MOVEMENT])
    assert diag.checks_run == 0, "MOVEMENT has no registered checks"
    assert not diag.is_demo_ready, "empty check set must fail-closed, not vacuously pass"
    assert any(f.id == "NO_VALIDATOR_FOR_REQUESTED_CATEGORIES" for f in diag.findings)
    assert diag.critical_count >= 1
    print("  failclosed_unregistered: PASS")


def test_diagnose_failclosed_string_category():
    """String category names coerce to enums; unknown strings fail-closed."""
    doctor = MVPDoctor(ue5_project_path=".")
    # 'MOVEMENT' (string) coerces to Category.MOVEMENT — still zero checks -> fail-closed.
    diag = doctor.diagnose(categories=["MOVEMENT"])
    assert not diag.is_demo_ready
    assert any(f.id == "NO_VALIDATOR_FOR_REQUESTED_CATEGORIES" for f in diag.findings)
    # Garbage string -> UNKNOWN_CATEGORY critical, never a vacuous pass.
    diag2 = doctor.diagnose(categories=["frobnicate"])
    assert not diag2.is_demo_ready
    assert any(f.id.startswith("UNKNOWN_CATEGORY") for f in diag2.findings)
    print("  failclosed_string: PASS")


def test_diagnose_runall_not_flagged():
    """diagnose(None) / diagnose([]) run all checks; the guard must NOT fire there."""
    doctor = MVPDoctor(ue5_project_path=".")
    for arg in (None, []):
        diag = doctor.diagnose(categories=arg)
        assert diag.checks_run > 0, f"run-all should execute checks (arg={arg!r})"
        assert not any(
            f.id == "NO_VALIDATOR_FOR_REQUESTED_CATEGORIES" for f in diag.findings
        ), "run-all path must never be flagged as missing a validator"
    print("  runall_not_flagged: PASS")


def _asset_doctor(tmp_path, fbx_flag_line="Interchange.FeatureFlags.Import.FBX=0"):
    """Build an MVPDoctor over a fake project dir with an optional FBX flag line."""
    (tmp_path / "Config").mkdir(exist_ok=True)
    body = "[/Script/Engine.Engine]\n" + (f"{fbx_flag_line}\n" if fbx_flag_line else "")
    (tmp_path / "Config" / "DefaultEngine.ini").write_text(body, encoding="utf-8")
    return MVPDoctor(ue5_project_path=str(tmp_path))


def test_asset_check_interchange_fbx_flag(tmp_path):
    """The static asset-preflight: skeletal imports break silently without FBX=0."""
    # Missing flag -> CRITICAL blocker.
    diag = _asset_doctor(tmp_path, fbx_flag_line=None).diagnose(categories=[Category.ASSET])
    assert any(f.id == "ASSET_INTERCHANGE_FBX_FLAG_MISSING" and f.severity == Severity.CRITICAL
               for f in diag.findings)
    assert not diag.is_demo_ready
    # Wrong value -> CRITICAL.
    diag2 = _asset_doctor(tmp_path, "Interchange.FeatureFlags.Import.FBX=1").diagnose(categories=[Category.ASSET])
    assert any(f.id == "ASSET_INTERCHANGE_FBX_FLAG_WRONG" for f in diag2.findings)
    # Correct value -> no interchange finding.
    diag3 = _asset_doctor(tmp_path, "Interchange.FeatureFlags.Import.FBX=0").diagnose(categories=[Category.ASSET])
    assert not any(f.id.startswith("ASSET_INTERCHANGE_FBX_FLAG") for f in diag3.findings)
    print("  asset_interchange_flag: PASS")


def test_asset_check_skeletal_presence(tmp_path):
    """Pipeline-stage signal: INFO when zero SK_SW_ skeletal assets exist, gone once one lands."""
    content = tmp_path / "Content"
    content.mkdir(exist_ok=True)
    diag = _asset_doctor(tmp_path).diagnose(categories=[Category.ASSET])
    assert any(f.id == "ASSET_NO_SKELETAL_IN_ENGINE" for f in diag.findings)
    # Land a skeletal asset -> the INFO signal clears (fresh doctor, fresh file cache).
    (content / "SK_SW_Test.uasset").write_bytes(b"\x00")
    diag2 = _asset_doctor(tmp_path).diagnose(categories=[Category.ASSET])
    assert not any(f.id == "ASSET_NO_SKELETAL_IN_ENGINE" for f in diag2.findings)
    print("  asset_skeletal_presence: PASS")


def test_asset_category_now_has_checks(tmp_path):
    """ASSET is no longer a zero-check category — NO_VALIDATOR must NOT fire for it."""
    diag = _asset_doctor(tmp_path).diagnose(categories=[Category.ASSET])
    assert diag.checks_run >= 2, "ASSET should run its registered checks"
    assert not any(f.id == "NO_VALIDATOR_FOR_REQUESTED_CATEGORIES" for f in diag.findings)
    print("  asset_has_checks: PASS")


def test_run_python_capture_native_first():
    """v0.8.2: _run_python_capture runs native :8090 first; RC bridge.execute_python untouched."""
    from unittest.mock import MagicMock, patch
    doctor = MVPDoctor(ue5_project_path=".")
    bridge = MagicMock()
    doctor._bridge = bridge
    with patch("bionics_tools._ue5_native_exec.run_python_native",
               return_value={"reachable": True, "success": True,
                             "output": '  {"verdict": "READY"}  ', "error": ""}):
        success, output, error = doctor._run_python_capture("print('x')")
    assert success is True
    assert output == '{"verdict": "READY"}'  # stripped
    assert error == ""
    bridge.execute_python.assert_not_called()
    print("  run_python_capture_native_first: PASS")


def test_run_python_capture_falls_back_to_rc():
    """When the native bridge is unreachable, _run_python_capture falls back to RC."""
    from unittest.mock import MagicMock, patch
    doctor = MVPDoctor(ue5_project_path=".")
    bridge = MagicMock()
    bridge.execute_python.return_value = MagicMock(
        success=True, error="", data={"output": [{"output": '{"verdict": "READY"}'}]}
    )
    doctor._bridge = bridge
    with patch("bionics_tools._ue5_native_exec.run_python_native",
               return_value={"reachable": False, "error": "bridge off"}):
        success, output, _error = doctor._run_python_capture("print('x')")
    assert success is True
    assert output == '{"verdict": "READY"}'
    bridge.execute_python.assert_called_once()
    print("  run_python_capture_falls_back_to_rc: PASS")


def test_run_python_capture_native_failure_no_rc_retry():
    """A real native failure (script ran + raised) must NOT fall back to the dead RC path."""
    from unittest.mock import MagicMock, patch
    doctor = MVPDoctor(ue5_project_path=".")
    bridge = MagicMock()
    doctor._bridge = bridge
    with patch("bionics_tools._ue5_native_exec.run_python_native",
               return_value={"reachable": True, "success": False,
                             "output": "", "error": "RuntimeError: boom"}):
        success, _output, error = doctor._run_python_capture("raise RuntimeError('boom')")
    assert success is False
    assert "RuntimeError" in error
    bridge.execute_python.assert_not_called()
    print("  run_python_capture_native_failure_no_rc_retry: PASS")


if __name__ == "__main__":
    print("MVP Doctor Tests:")
    test_diagnosis_data_model()
    test_finding_to_planner_prompt()
    test_diagnosis_summary_format()
    test_planner_integration_format()
    test_doctor_runs_on_real_project()
    test_diagnose_failclosed_unregistered_category()
    test_diagnose_failclosed_string_category()
    test_diagnose_runall_not_flagged()
    test_run_python_capture_native_first()
    test_run_python_capture_falls_back_to_rc()
    test_run_python_capture_native_failure_no_rc_retry()
    import tempfile
    with tempfile.TemporaryDirectory() as _td:
        _tp = Path(_td)
        test_asset_check_interchange_fbx_flag(_tp)
        test_asset_check_skeletal_presence(_tp)
        test_asset_category_now_has_checks(_tp)
    print("\nAll tests passed.")
