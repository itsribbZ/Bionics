"""Tests for core/abi_guard.py — the static S7 anti-pattern linter (Build E).

Table-driven, zero engine/bridge dependency. Mirrors the roadmap's acceptance test:
  - diff adding UPROPERTY -> must_cmdline_rebuild=True / safe=False citing S7.A
  - function-body-only diff -> safe=True
  - wire_animgraph_pins on a plan -> S7.B/N note
  - bind_pin_to_property -> S7.C redirect to drive_animgraph_pin_via_variable
  - dead 5.7 API in an arg -> S7.M BLOCK
  - `py.exec <path>` -> emits the exec(open(...).read()) rewrite (S7.L)
"""
from __future__ import annotations

import json

from core.abi_guard import _main, analyze, analyze_diff, analyze_plan

# --------------------------- S7.A Live-Coding ABI ---------------------------

def test_uproperty_add_forces_cmdline_rebuild():
    diff = (
        "--- a/SWThing.h\n"
        "+++ b/SWThing.h\n"
        "@@ -10,6 +10,8 @@\n"
        " class USWThing {\n"
        "+    UPROPERTY(EditAnywhere)\n"
        "+    int32 Foo;\n"
        " };\n"
    )
    report = analyze_diff(diff)
    assert report.must_cmdline_rebuild is True
    assert report.safe_for_live_coding is False
    assert not report.ok  # BLOCK fired
    assert any(v.rule == "S7.A" and v.severity == "BLOCK" for v in report.violations)


def test_function_body_only_diff_is_live_coding_safe():
    diff = (
        "--- a/SWThing.cpp\n"
        "+++ b/SWThing.cpp\n"
        "@@ -20,3 +20,4 @@\n"
        " void USWThing::Tick(float Dt) {\n"
        "+    Health += Dt * RegenRate;\n"
        " }\n"
    )
    report = analyze_diff(diff)
    assert report.must_cmdline_rebuild is False
    assert report.safe_for_live_coding is True
    assert report.ok
    assert report.violations == []


def test_removed_ufunction_also_flags_abi():
    diff = (
        "--- a/SWThing.h\n"
        "+++ b/SWThing.h\n"
        "@@ -5,7 +5,6 @@\n"
        "-    UFUNCTION(BlueprintCallable)\n"
        "-    void OldApi();\n"
    )
    report = analyze_diff(diff)
    assert report.must_cmdline_rebuild is True
    assert any(v.rule == "S7.A" for v in report.violations)


# --------------------------- S7.C / S7.B plan rules ---------------------------

def test_bind_pin_redirects_to_drive_variable():
    report = analyze_plan([{"tool": "bind_pin_to_property", "args": {"pin_name": "bActiveValue"}}])
    v = next(v for v in report.violations if v.rule == "S7.C")
    assert v.severity == "WARN"
    assert "drive_animgraph_pin_via_variable" in v.remedy
    assert report.ok  # WARN is not a hard block


def test_wire_pins_warns_no_fork():
    report = analyze_plan([{"tool": "ue5_wire_animgraph_pins", "args": {}}])
    assert any(v.rule == "S7.B/N" and v.severity == "INFO" for v in report.violations)
    assert "splice_pose_flow" in next(v for v in report.violations if v.rule == "S7.B/N").remedy


# --------------------------- S7.M dead API ---------------------------

def test_dead_ikretarget_api_blocks():
    report = analyze_plan([
        {"tool": "ue5_run_python", "args": {"script": "nr = unreal.IKRetargetBatchOperationNameRule()"}},
    ])
    assert not report.ok
    v = next(v for v in report.violations if v.rule == "S7.M")
    assert v.severity == "BLOCK"
    assert "duplicate_and_retarget" in v.remedy


# --------------------------- S7.L py.exec rewrite ---------------------------

def test_py_exec_path_gets_rewritten():
    report = analyze_plan([
        {"tool": "execute_console_command", "args": {"command": r"py.exec C:\work\import.py"}},
    ])
    assert len(report.rewrites) == 1
    rw = report.rewrites[0]
    assert rw.rule == "S7.L"
    assert rw.rewritten == r"py exec(open(r'C:\work\import.py').read())"


def test_py_execfile_also_rewritten():
    report = analyze_plan([
        {"tool": "execute_console_command", "args": {"command": "py.execfile /tmp/x.py"}},
    ])
    assert report.rewrites and report.rewrites[0].rewritten == "py exec(open(r'/tmp/x.py').read())"


def test_correct_py_exec_form_not_rewritten():
    report = analyze_plan([
        {"tool": "execute_console_command", "args": {"command": r"py exec(open(r'C:\work\import.py').read())"}},
    ])
    assert report.rewrites == []  # already the working form


def test_inline_py_exec_not_a_path_is_ignored():
    # `py.exec print(1)` is inline code, not a file path -> no rewrite.
    report = analyze_plan([{"tool": "execute_console_command", "args": {"command": "py.exec print(1)"}}])
    assert report.rewrites == []


# --------------------------- combined + CLI ---------------------------

def test_analyze_merges_diff_and_plan():
    diff = "+++ b/X.h\n+    UCLASS()\n+class UX {};\n"
    plan = [{"tool": "bind_pin_to_property", "args": {}}]
    report = analyze(diff=diff, plan=plan)
    assert report.must_cmdline_rebuild is True
    assert {v.rule for v in report.violations} == {"S7.A", "S7.C"}


def test_empty_inputs_are_clean():
    report = analyze(diff=None, plan=None)
    assert report.ok and report.safe_for_live_coding and not report.must_cmdline_rebuild
    assert report.to_dict()["violations"] == []


def test_cli_exit_codes(tmp_path, capsys):
    # BLOCK -> exit 1
    diff_file = tmp_path / "c.patch"
    diff_file.write_text("+++ b/X.h\n+    UPROPERTY()\n+    int32 Y;\n", encoding="utf-8")
    assert _main(["--diff", str(diff_file)]) == 1
    # Clean -> exit 0
    clean = tmp_path / "clean.patch"
    clean.write_text("+++ b/X.cpp\n+    DoThing();\n", encoding="utf-8")
    assert _main(["--diff", str(clean)]) == 0
    # JSON plan with rewrite -> exit 0 (rewrite is not a block), valid JSON out
    plan_file = tmp_path / "p.json"
    plan_file.write_text(json.dumps([{"tool": "execute_console_command",
                                      "args": {"command": "py.exec /a/b.py"}}]), encoding="utf-8")
    assert _main(["--plan", str(plan_file), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"rewrites"' in out
    # Bad plan (not a list) -> exit 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a list"}', encoding="utf-8")
    assert _main(["--plan", str(bad)]) == 2


if __name__ == "__main__":
    import sys
    sys.exit(__import__("pytest").main([__file__, "-q"]))
