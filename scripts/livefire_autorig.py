"""Live-fire harness for the autorig (validate + IKRig) tool (Rule #13 front-to-back test).

REQUIRES UE5 + the BionicsBridge plugin running. The tool resolves the bridge token from
config.yaml's paths.ue5_project (the _discover_bridge config fallback), so no
BIONICS_BRIDGE_* env workaround is needed. Run AFTER scripts/livefire_uasvc.py has landed
a SkeletalMesh (or point --mesh at any existing one).

Positive case : a humanoid SkeletalMesh -> expect ok=True, humanoid=True, 7/7 chains (Sworder canon).
Negative case : a non-humanoid mesh (e.g. the Formahger sword) -> with --negative the
                harness PASSES on a clean fail-closed verdict (ok=False, humanoid=False)
                and only FAILS on a tool-level crash/timeout.

Usage:
    python scripts/livefire_autorig.py
    python scripts/livefire_autorig.py --mesh "/Game/Test/Skel/SK_SW_HumanoidTemplate/SkeletalMeshes/SK_SW_HumanoidTemplate"
    python scripts/livefire_autorig.py --negative --mesh "/Game/Test/Skel/SK_SW_Formahger/SkeletalMeshes/SK_SW_Formahger"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_DEFAULT_MESH = "/Game/Test/Skel/SK_SW_HumanoidTemplate/SkeletalMeshes/SK_SW_HumanoidTemplate"


def _invoke_and_print(fn, args, label: str):
    """Fire the autorig tool once and print its verdict; return (result, data)."""
    print(f"\n== autorig live-fire [{label}] ==\n  mesh : {args.mesh}\n  dest : {args.ikrig_dest}")
    result = fn(skeletal_mesh_path=args.mesh, ikrig_dest=args.ikrig_dest, timeout_s=args.timeout)
    d = result.data or {}
    print(f"  result.ok        = {result.ok}")
    print(f"  humanoid         = {d.get('humanoid')}")
    print(f"  bone_count       = {d.get('bone_count')} (method={d.get('bone_method')})")
    print(f"  mannequin_missing= {d.get('mannequin_missing')}")
    print(f"  ikrig_path       = {d.get('ikrig_path')}")
    print(f"  chains           = {d.get('configured_count')}/{d.get('expected_count')} "
          f"(verified {d.get('verified_count')}, unique {d.get('unique_verified_count')}, "
          f"cleared {d.get('cleared_count')})")
    print(f"  stage            = {d.get('stage')}")
    print(f"  errors           = {d.get('errors')}")
    if not result.ok:
        print(f"  (failure)        = {result.error}")
    return result, d


def main() -> int:
    ap = argparse.ArgumentParser(description="Live-fire the autorig validate+IKRig tool")
    ap.add_argument("--mesh", default=_DEFAULT_MESH, help="UE content path to the SkeletalMesh")
    ap.add_argument("--ikrig-dest", default="/Game/Test/Skel", help="Content path for the IKRig asset")
    ap.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for the deferred rig")
    ap.add_argument("--negative", action="store_true",
                    help="Treat a clean fail-closed (non-humanoid) verdict as PASS")
    ap.add_argument("--idempotency", action="store_true",
                    help="Run TWICE and assert the rebuild stays at the canonical chain count (dup-pollution regression)")
    args = ap.parse_args()

    from bionics_tools.ue5_autorig import ue5_autorig_humanoid

    # ── Idempotency mode: the dup-pollution regression gate (Rule #13 front-to-back). Run 1 builds
    # the rig; Run 2 must clear the prior chains and rebuild to exactly the canon (never a stack).
    # The expected count comes from the tool (len(CHAINS) = 7 for Sworder) — not hardcoded here.
    if args.idempotency:
        r1, d1 = _invoke_and_print(ue5_autorig_humanoid, args, "run 1 (build)")
        exp = d1.get("expected_count")
        if not r1.ok or not exp or d1.get("verified_count") != exp:
            print(f"\nFAIL: run 1 did not produce a clean {exp or '?'}-chain rig — cannot test idempotency.")
            return 1
        r2, d2 = _invoke_and_print(ue5_autorig_humanoid, args, "run 2 (rebuild)")
        ok = (
            r2.ok
            and d2.get("verified_count") == exp
            and d2.get("unique_verified_count") == exp
            and d2.get("cleared_count") == exp  # run 2 must have removed run 1's chains first
        )
        if ok:
            print(f"\nPASS: re-run cleared {exp} + rebuilt to exactly {exp} — no dup pollution.")
            return 0
        print(f"\nFAIL: re-run did not stay at {exp} — verified={d2.get('verified_count')} "
              f"cleared={d2.get('cleared_count')} (the dup-pollution bug). ")
        return 1

    result, d = _invoke_and_print(ue5_autorig_humanoid, args, "single")

    bridge_down = "could not queue" in (result.error or "") or "Timed out" in (result.error or "")

    if args.negative:
        if bridge_down:
            print("\nFAIL: bridge unreachable / timed out — cannot evaluate the negative case.")
            return 1
        print(f"\nPASS: negative case evaluated cleanly (ok={result.ok}, humanoid={d.get('humanoid')}).")
        return 0

    exp = d.get("expected_count")
    if result.ok and d.get("humanoid") and exp and d.get("configured_count") == exp:
        print(f"\nPASS: humanoid validated + {exp}/{exp} IKRig chains landed.")
        return 0
    print(f"\nFAIL: positive case did not produce a clean humanoid rig ({d.get('configured_count')}/{exp}).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
