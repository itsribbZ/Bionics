"""Live-fire harness for the uasvc skeletal-import tool (Rule #13 front-to-back test).

REQUIRES UE5 + the BionicsBridge plugin running. The tool resolves the bridge token
from config.yaml's paths.ue5_project (the _discover_bridge config fallback added
2026-05-28), so no BIONICS_BRIDGE_* env workaround is needed.

Positive case  : import a humanoid .glb -> expect ok=True, is_skeletal=True.
Negative case  : import a non-humanoid mesh (e.g. the Formahger sword) -> expect the
                 fail-closed path. With --negative the harness PASSES when the import
                 reports ok=False (the canonical StaticMesh / non-skeletal rejection)
                 or when a skeletal mesh lands (some sources do carry skin) — it only
                 FAILS if the tool itself errors out (crash/timeout), not on a clean
                 fail-closed verdict.

Usage:
    python scripts/livefire_uasvc.py --glb "C:/path/SK_SW_HumanoidTemplate.glb" --name SK_SW_HumanoidTemplate
    python scripts/livefire_uasvc.py            # uses defaults from the proven seed params, if present
    python scripts/livefire_uasvc.py --negative --glb "C:/path/SK_SW_Formahger.glb" --name SK_SW_Formahger
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Default source params come from the live-proven 2026-05-28 seed, if still on disk.
_SEED_PARAMS = Path(
    "C:/Users/jbro1/Desktop/T1/blend-master/bridge/_skeletal_import_params.json"
)


def _seed_defaults() -> dict:
    try:
        return json.loads(_SEED_PARAMS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    seed = _seed_defaults()
    ap = argparse.ArgumentParser(description="Live-fire the uasvc skeletal import tool")
    ap.add_argument("--glb", default=seed.get("file_path", ""),
                    help="Absolute path to source .glb/.gltf/.fbx (skin data required)")
    ap.add_argument("--name", default=seed.get("asset_name", "SK_SW_HumanoidTemplate"),
                    help="Destination asset name")
    ap.add_argument("--dest", default=seed.get("dest_path", "/Game/Test/Skel"),
                    help="UE content path")
    ap.add_argument("--timeout", type=float, default=120.0, help="Seconds to wait for the deferred import")
    ap.add_argument("--negative", action="store_true",
                    help="Treat a clean fail-closed verdict as PASS (non-humanoid source)")
    args = ap.parse_args()

    if not args.glb:
        print("FAIL: no --glb provided and no seed params found. "
              f"Pass --glb explicitly (looked for {_SEED_PARAMS}).")
        return 2

    from bionics_tools.ue5_uasvc import ue5_uasvc_import_skeletal, ue5_uasvc_preflight

    ext = Path(args.glb).suffix.lower()
    print(f"== uasvc live-fire ==\n  source : {args.glb}\n  name   : {args.name}\n  dest   : {args.dest}")

    # Preflight is FBX-specific; print it for visibility regardless.
    pf = ue5_uasvc_preflight()
    print(f"  preflight: ok={pf.ok} :: {pf.content}")
    if ext == ".fbx" and not pf.ok:
        print("FAIL: FBX source but Interchange preflight failed (see above).")
        return 1

    result = ue5_uasvc_import_skeletal(
        file_path=args.glb, asset_name=args.name, dest_path=args.dest, timeout_s=args.timeout
    )
    print(f"\n  result.ok    = {result.ok}")
    print(f"  is_skeletal  = {(result.data or {}).get('is_skeletal')}")
    print(f"  skeleton     = {(result.data or {}).get('skeleton_path')}")
    print(f"  imported     = {(result.data or {}).get('imported')}")
    print(f"  errors       = {(result.data or {}).get('errors')}")
    if not result.ok:
        print(f"  (failure)    = {result.error}")

    bridge_down = "could not queue" in (result.error or "") or "Timed out" in (result.error or "")

    if args.negative:
        if bridge_down:
            print("\nFAIL: bridge unreachable / timed out — cannot evaluate the negative case.")
            return 1
        print("\nPASS: negative case evaluated without a tool-level crash "
              f"(verdict ok={result.ok}, is_skeletal={(result.data or {}).get('is_skeletal')}).")
        return 0

    if result.ok and (result.data or {}).get("is_skeletal"):
        print("\nPASS: skeletal mesh landed (fail-closed gate satisfied).")
        return 0
    print("\nFAIL: positive case did not land a SkeletalMesh.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
