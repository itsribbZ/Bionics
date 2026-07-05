"""Post-rebuild verification harness for the 2026-05-15 godspeed blocker eradication.

Run this AFTER Jacob:
  (1) Closes UE5 editor
  (2) Full-rebuilds BionicsBridge plugin (Build.bat MyProjectEditor ...)
  (3) Relaunches UE5 with the new ini

Three checks (each gated on prior — fails fast if foundation broken):
  T0.A  HTTP 30010 RC accepts PythonScriptLibrary.ExecutePythonCommand (was 400)
  T1.C  ue5_create_animgraph_variable_get spawns K2Node_VariableGet in AnimGraph
  T1.B  ue5_drive_animgraph_pin_via_variable atomic spawn-wire-compile lands

Read-only-by-default. T1.B/T1.C use a throwaway test path NOT a Sworder asset.
Override --target to live-fire against the rifle ABP if explicitly desired.

Usage:
  python scripts/verify_campaign_20260515.py             # all 3 checks, throwaway target
  python scripts/verify_campaign_20260515.py --skip-bridge  # only T0.A curl check
  python scripts/verify_campaign_20260515.py --target /Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3 --variable bHasRangedWeapon
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def check_t0a_rc_python_execution() -> tuple[bool, str]:
    """T0.A — RC HTTP 30010 should now accept PythonScriptLibrary calls (was 400)."""
    try:
        import requests
    except ImportError:
        return False, "requests not installed (pip install requests)"

    url = "http://127.0.0.1:30010/remote/object/call"
    body = {
        "objectPath": "/Script/PythonScriptPlugin.Default__PythonScriptLibrary",
        "functionName": "ExecutePythonCommand",
        "parameters": {
            "PythonCommand": "unreal.log('bionics campaign T0.A verify alive')",
            "ExecutionMode": "ExecuteFile",
        },
    }
    try:
        resp = requests.put(url, json=body, timeout=5)
    except requests.RequestException as e:
        return False, f"RC HTTP unreachable — UE5 not running or port 30010 not bound: {e}"

    if resp.status_code == 200:
        return True, f"RC HTTP 200 OK — bEnableRemotePythonExecution=True confirmed (was 400 pre-patch)"
    if resp.status_code == 400:
        body_text = resp.text[:200]
        return False, (
            f"RC HTTP 400 still — patch did not take effect. "
            f"Confirm UE5 was restarted AFTER DefaultEngine.ini edit. Body: {body_text}"
        )
    return False, f"RC HTTP {resp.status_code}: {resp.text[:200]}"


def check_t1c_create_variable_get(asset_path: str, variable_name: str) -> tuple[bool, str]:
    """T1.C — ue5_create_animgraph_variable_get spawns a K2Node_VariableGet."""
    try:
        from bionics_tools.ue5_native import call_bridge_tool
    except ImportError as e:
        return False, f"Bionics import failed: {e}"

    result = call_bridge_tool("create_animgraph_variable_get", {
        "asset_path": asset_path,
        "variable_name": variable_name,
        "pos_x": -800,
        "pos_y": -200,
    })
    if not result.ok:
        if "Bridge unreachable" in (result.error or ""):
            return False, "C++ :8090 bridge unreachable — rebuild required, then restart UE5"
        if "tool 'create_animgraph_variable_get'" in (result.error or "").lower():
            return False, (
                "C++ bridge running BUT tool not registered — rebuild did not pick up "
                "BionicsBridgeEditorModule.cpp:74 entry. Check Build.bat output for the "
                "AnimGraphTools.cpp recompile."
            )
        return False, f"T1.C failed: {result.error}"

    data = result.data or {}
    if not data.get("created"):
        return False, f"T1.C ok=True but created=False: {result.content[:200]}"
    return True, (
        f"T1.C OK — node {data.get('node_name')} (guid {data.get('node_guid', '?')[:8]}...) "
        f"output pin {data.get('output_pin_name')}"
    )


def check_t1b_drive_pin(asset_path: str, variable_name: str, target_node: str, target_pin: str) -> tuple[bool, str]:
    """T1.B — atomic spawn-wire-compile must report compile_ok=True."""
    try:
        from bionics_tools.ue5_native import call_bridge_tool
    except ImportError as e:
        return False, f"Bionics import failed: {e}"

    result = call_bridge_tool("drive_animgraph_pin_via_variable", {
        "asset_path": asset_path,
        "variable_name": variable_name,
        "target_node_name": target_node,
        "target_pin_name": target_pin,
        "pos_x": -800,
        "pos_y": -100,
        "compile": True,
    })
    if not result.ok:
        if "Bridge unreachable" in (result.error or ""):
            return False, "C++ :8090 bridge unreachable"
        if "tool 'drive_animgraph_pin_via_variable'" in (result.error or "").lower():
            return False, "Tool not registered — see T1.C diagnostic"
        return False, f"T1.B failed: {result.error}"

    data = result.data or {}
    if not data.get("compile_ok"):
        return False, (
            f"T1.B spawn+wire ok but compile_ok=False (status {data.get('compile_status', '?')}). "
            f"BlueprintStatus enum: 0=UpToDate, 2=Dirty, 3=Error. Investigate compile errors."
        )
    return True, (
        f"T1.B OK — {variable_name} → {target_node}.{target_pin} wired + compiled "
        f"(get node {data.get('get_node_name')})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--target",
        default="/Game/Tests/BP_EventGraphSmoke",
        help="AnimBP asset path for T1.B/T1.C tests (default: throwaway test BP)",
    )
    parser.add_argument("--variable", default="bIsAiming", help="Variable name on the target AnimBP class")
    parser.add_argument("--target-node", default="", help="Anim node GetName() for T1.B wire target (skipped if empty)")
    parser.add_argument("--target-pin", default="bActiveValue", help="Input pin name on target node")
    parser.add_argument("--skip-bridge", action="store_true", help="Run only T0.A curl check; skip T1.B/T1.C")
    args = parser.parse_args()

    print("=" * 70)
    print("BIONICS CAMPAIGN VERIFICATION — 2026-05-15 godspeed")
    print("=" * 70)
    verdicts = []

    # T0.A — RC HTTP gate
    print("\n[T0.A] RC HTTP 30010 PythonScriptLibrary gate")
    t0_start = time.time()
    ok, msg = check_t0a_rc_python_execution()
    verdicts.append(("T0.A", ok, msg, time.time() - t0_start))
    print(f"  {'PASS' if ok else 'FAIL'} ({time.time() - t0_start:.2f}s) — {msg}")

    if args.skip_bridge:
        print("\nSkipping T1.B/T1.C per --skip-bridge")
    else:
        # T1.C — base variable_get primitive
        print(f"\n[T1.C] ue5_create_animgraph_variable_get on {args.target}.{args.variable}")
        t1c_start = time.time()
        ok, msg = check_t1c_create_variable_get(args.target, args.variable)
        verdicts.append(("T1.C", ok, msg, time.time() - t1c_start))
        print(f"  {'PASS' if ok else 'FAIL'} ({time.time() - t1c_start:.2f}s) — {msg}")

        # T1.B — only if user provided a target node (full wire-and-compile is destructive)
        if args.target_node:
            print(f"\n[T1.B] drive_animgraph_pin_via_variable: {args.variable} → {args.target_node}.{args.target_pin}")
            t1b_start = time.time()
            ok, msg = check_t1b_drive_pin(args.target, args.variable, args.target_node, args.target_pin)
            verdicts.append(("T1.B", ok, msg, time.time() - t1b_start))
            print(f"  {'PASS' if ok else 'FAIL'} ({time.time() - t1b_start:.2f}s) — {msg}")
        else:
            print("\n[T1.B] skipped — pass --target-node to live-fire the atomic spawn-wire-compile")

    print("\n" + "=" * 70)
    print("VERDICTS")
    print("=" * 70)
    for tag, ok, msg, dt in verdicts:
        sym = "PASS" if ok else "FAIL"
        print(f"  {tag:>6}  {sym}  ({dt:.2f}s)  {msg[:90]}")
    print("=" * 70)

    all_ok = all(ok for _, ok, _, _ in verdicts)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
