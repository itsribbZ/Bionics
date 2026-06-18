"""abi-guard - static Live-Coding ABI + bridge anti-pattern pre-flight linter.

Build E of the 2026-05-28 idea->playable automation roadmap. Encodes the v4 Master
Blueprint S7 pre-located bug catalog as a table-driven rule engine so a planned change
can be checked BEFORE it burns a multi-attempt rabbit-hole. Pure static analysis - no
UE5, no bridge, no Live Coding. Importable (godspeed Phase 2 pre-check hook) + CLI.

    from core.abi_guard import analyze
    report = analyze(diff=patch_text, plan=[{"tool": "bind_pin_to_property", "args": {...}}])
    if report.must_cmdline_rebuild:
        ...  # close the editor and do a full rebuild, NOT Live Coding
    for rw in report.rewrites:
        ...  # apply rw.rewritten in place of rw.original

CLI:
    python -m core.abi_guard --diff changes.patch --plan calls.json [--json]
    exit 0 = no BLOCK-level violation, 1 = BLOCK violation(s), 2 = bad input

Rules (the statically-detectable subset of S7):
  S7.A  Live-Coding ABI trap - adding/removing UPROPERTY/UFUNCTION/UCLASS/USTRUCT/
        UENUM/GENERATED_BODY changes class layout + reflection; Live Coding silently
        rejects or crashes. Verdict: must cmdline-rebuild.
  S7.B/N wire_animgraph_pins on an already-connected single-input pose pin silently
        replaces the existing wire (no fork). Prefer splice_pose_flow / SaveCachedPose.
  S7.C  bind_pin_to_property is metadata-only - no runtime propagation without compile.
        Use drive_animgraph_pin_via_variable.
  S7.L  `py.exec <path>` / `py.execfile <path>` is an unreliable console no-op. Rewrite
        to `py exec(open(r'<path>').read())`.
  S7.M  dead UE5.7 APIs (e.g. IKRetargetBatchOperationNameRule).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Violation:
    rule: str            # S7 entry, e.g. "S7.A"
    severity: str        # BLOCK | WARN | INFO
    message: str
    remedy: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule, "severity": self.severity, "message": self.message,
            "remedy": self.remedy, "evidence": self.evidence,
        }


@dataclass
class Rewrite:
    rule: str
    original: str
    rewritten: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule, "original": self.original,
            "rewritten": self.rewritten, "reason": self.reason,
        }


@dataclass
class AbiGuardReport:
    safe_for_live_coding: bool = True
    must_cmdline_rebuild: bool = False
    violations: list[Violation] = field(default_factory=list)
    rewrites: list[Rewrite] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if no BLOCK-level violation fired."""
        return not any(v.severity == "BLOCK" for v in self.violations)

    def to_dict(self) -> dict:
        return {
            "safe_for_live_coding": self.safe_for_live_coding,
            "must_cmdline_rebuild": self.must_cmdline_rebuild,
            "ok": self.ok,
            "violations": [v.to_dict() for v in self.violations],
            "rewrites": [r.to_dict() for r in self.rewrites],
        }


# S7.A - reflected symbols whose addition/removal changes the class ABI. Live Coding
# silently rejects or crashes; only a full cmdline rebuild applies them safely.
_ABI_MACROS = re.compile(
    r"\b(UPROPERTY|UFUNCTION|UCLASS|USTRUCT|UENUM|UINTERFACE|GENERATED_BODY|GENERATED_UCLASS_BODY)\b"
)

# S7.L - the broken console form `py.exec <path>` / `py.execfile <path>`. The working
# form is `py exec(open(r'<path>').read())`.
_PY_EXEC_PATH = re.compile(
    r"""^\s*py\.(?:exec|execfile)\s+["']?(?P<path>[^"']+?\.py)["']?\s*$""", re.IGNORECASE
)
_PY_EXEC_OK = re.compile(r"exec\s*\(\s*open\s*\(", re.IGNORECASE)

# S7.M - dead UE5.7 APIs -> remedy.
_DEAD_APIS = {
    "IKRetargetBatchOperationNameRule": (
        "Dead in UE5.7 (symbol does not exist). Use the static "
        "IKRetargetBatchOperation.duplicate_and_retarget(...) which takes source/target "
        "SkeletalMesh args + inline name strings, not a name-rule object."
    ),
}

# S7.C / S7.B - bridge tools that carry a known anti-pattern (name with or without the
# ue5_ MCP prefix).
_METADATA_BIND_TOOLS = {"bind_pin_to_property", "ue5_bind_pin_to_property"}
_WIRE_TOOLS = {"wire_animgraph_pins", "ue5_wire_animgraph_pins"}


def _changed_lines(diff: str) -> list[str]:
    """Added (+) and removed (-) lines of a unified diff, hunk/file markers excluded."""
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            out.append(line[1:])
    return out


def analyze_diff(diff: str) -> AbiGuardReport:
    """Scan a unified diff for S7.A ABI-changing edits."""
    report = AbiGuardReport()
    if not diff:
        return report
    for line in _changed_lines(diff):
        m = _ABI_MACROS.search(line)
        if m:
            report.must_cmdline_rebuild = True
            report.safe_for_live_coding = False
            report.violations.append(Violation(
                rule="S7.A",
                severity="BLOCK",
                message=(
                    f"Changes reflected symbol '{m.group(1)}' - alters class ABI/reflection. "
                    "Live Coding will silently reject or crash."
                ),
                remedy=(
                    "Close the editor and do a full cmdline rebuild (Rebuild.bat / "
                    "UnrealBuildTool), NOT Live Coding (ue5_native_live_coding_compile). "
                    "Then restart UE5."
                ),
                evidence=line.strip(),
            ))
    return report


def analyze_plan(plan: list | None) -> AbiGuardReport:
    """Scan a list of planned bridge calls ({tool/name, args/arguments}) for S7.B/C/L/M."""
    report = AbiGuardReport()
    for call in plan or []:
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or call.get("name") or "").strip()
        args = call.get("args") or call.get("arguments") or {}

        if tool in _METADATA_BIND_TOOLS:
            report.violations.append(Violation(
                rule="S7.C",
                severity="WARN",
                message=(
                    "bind_pin_to_property is metadata-only - the runtime FExposedValueHandler "
                    "is not patched without a recompile, so live driving silently no-ops."
                ),
                remedy=(
                    "Use drive_animgraph_pin_via_variable (atomic spawn K2Node_VariableGet + "
                    "wire + compile) for runtime-correct pin driving."
                ),
                evidence=tool,
            ))
        if tool in _WIRE_TOOLS:
            report.violations.append(Violation(
                rule="S7.B/N",
                severity="INFO",
                message=(
                    "wire_animgraph_pins on an already-connected single-input pose pin silently "
                    "replaces the existing wire (no fork)."
                ),
                remedy=(
                    "If the target input pin may already be wired, use splice_pose_flow (it "
                    "surfaces broke_existing_wire) or fork via SaveCachedPose."
                ),
                evidence=tool,
            ))

        if isinstance(args, dict):
            for key, val in args.items():
                if not isinstance(val, str):
                    continue
                for dead, fix in _DEAD_APIS.items():
                    if dead in val:
                        report.violations.append(Violation(
                            rule="S7.M",
                            severity="BLOCK",
                            message=f"References dead UE5.7 API '{dead}'.",
                            remedy=fix,
                            evidence=f"{tool}.{key}={val[:80]}",
                        ))
                pm = _PY_EXEC_PATH.match(val)
                if pm and not _PY_EXEC_OK.search(val):
                    path = pm.group("path")
                    report.rewrites.append(Rewrite(
                        rule="S7.L",
                        original=val.strip(),
                        rewritten=f"py exec(open(r'{path}').read())",
                        reason=(
                            "`py.exec <path>` / `py.execfile <path>` is an unreliable no-op in "
                            "the UE5 console; exec(open(...).read()) actually runs the file."
                        ),
                    ))
    return report


def analyze(diff: str | None = None, plan: list | None = None) -> AbiGuardReport:
    """Combined static check over an optional diff and/or planned bridge calls."""
    merged = AbiGuardReport()
    for r in (analyze_diff(diff or ""), analyze_plan(plan or [])):
        merged.violations.extend(r.violations)
        merged.rewrites.extend(r.rewrites)
        merged.must_cmdline_rebuild = merged.must_cmdline_rebuild or r.must_cmdline_rebuild
        merged.safe_for_live_coding = merged.safe_for_live_coding and r.safe_for_live_coding
    return merged


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="abi_guard",
        description="Static Live-Coding ABI + bridge anti-pattern linter (v4 S7 catalog).",
    )
    ap.add_argument("--diff", help="path to a unified diff / patch file")
    ap.add_argument("--plan", help="path to a JSON file: list of {tool, args} bridge calls")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    diff_text = ""
    plan: list = []
    try:
        if args.diff:
            diff_text = Path(args.diff).read_text(encoding="utf-8")
        if args.plan:
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if not isinstance(plan, list):
                print("error: --plan must be a JSON list of {tool, args}", file=sys.stderr)
                return 2
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    report = analyze(diff=diff_text, plan=plan)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(
            f"safe_for_live_coding={report.safe_for_live_coding} "
            f"must_cmdline_rebuild={report.must_cmdline_rebuild} ok={report.ok}"
        )
        for v in report.violations:
            print(f"  [{v.severity}] {v.rule}: {v.message}")
            if v.remedy:
                print(f"      -> {v.remedy}")
        for rw in report.rewrites:
            print(f"  [REWRITE {rw.rule}] {rw.original}  ->  {rw.rewritten}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
