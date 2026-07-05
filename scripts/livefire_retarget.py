"""Live-fire: native batch-retarget tool end-to-end over :8090 (UE5 must be open).

Sacred Rule #13 — the front-to-back ship gate for ue5_batch_retarget_anims (M5 Stage 2).
Unlike the mocked unit tests (tests/test_ue5_retarget.py), this drives the REAL native bridge:
the proven RTG_Mannequin_to_SW_HumanoidTemplate retargeter retargeting the Mannequin anim set
onto SK_SW_HumanoidTemplate via the UE5.7 duplicate_and_retarget API. Run with UE5 + the
Sworder project open and the BionicsBridge plugin live.

    python scripts/livefire_retarget.py
    python scripts/livefire_retarget.py <retargeter_path> <source_folder>

Exit 0 = retargeted >0 anims; 1 = any failure (bridge down, unresolved meshes, no anims, 0 out).

NOTE (offline-build caveat, 2026-05-30): the chain-mapping query inside the tool is best-effort
(degrades if the UE5.7 chain-query API symbol differs). If this live-fire passes but reports
mapped_chains_verified=False, capture the real IKRetargeterController chain-mapping API from the
editor and promote min_mapped_chains to a hard gate. Respect feedback_dll_repo_inversion_landmine
before any plugin rebuild.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root on sys.path so `bionics_tools` + `core` import when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bionics_tools.ue5_retarget import ue5_batch_retarget_anims  # noqa: E402

# The proven M5 Stage-2 retargeter + Mannequin anim source (2026-05-29 recipe).
_DEFAULT_RTG = "/Game/Test/Skel/RTG_Mannequin_to_SW_HumanoidTemplate"
_DEFAULT_SRC = "/Game/Characters/Mannequins/Animations/Manny"


def main() -> int:
    rtg = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_RTG
    src = sys.argv[2] if len(sys.argv) > 2 else _DEFAULT_SRC
    print(f"[livefire_retarget] retargeting {src} via {rtg} over :8090 ...")
    result = ue5_batch_retarget_anims(
        retargeter_path=rtg,
        source_folder=src,
        suffix="_SWHT",
    )

    ok = bool(getattr(result, "ok", False))
    data = getattr(result, "data", {}) or {}
    print(f"[livefire_retarget] ok={ok}")
    print(f"[livefire_retarget] found={data.get('found_count')} retargeted={data.get('retargeted_count')} "
          f"mapped_chains={data.get('mapped_chains')} verified={data.get('mapped_chains_verified')} "
          f"stage={data.get('stage')}")
    if data.get("retargeted"):
        print(f"[livefire_retarget] outputs[:5]: {data['retargeted'][:5]}")
    if data.get("errors"):
        print(f"[livefire_retarget] errors: {data['errors']}")
    if not ok:
        print("[livefire_retarget] FAIL")
        return 1
    print("[livefire_retarget] PASS — anims retargeted via duplicate_and_retarget")
    return 0


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
