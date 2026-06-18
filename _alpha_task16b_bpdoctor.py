"""Task #16 (definitive) — BPDoctor scan for NULL anim refs in ABP_SciFiTrooperManV3.

The top-level AnimGraph query doesn't recurse into state-internal SequencePlayers, so we
use BPDoctor's NULL_ANIM_REF check to detect empty Sequence assignments inside the 4 rifle
states. Read-only / SAFE tier — zero mutation.
"""
import os
import sys
import json
from pathlib import Path

BIONICS = r"C:\Users\jbro1\Desktop\T1\Bionics"
PROJECT = r"C:\Users\jbro1\Documents\Sworder721\MyProject"
ABP = "/Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3"

inst = Path(PROJECT) / ".bionics-bridge" / "instance.json"
data = json.loads(inst.read_text(encoding="utf-8"))
os.environ["BIONICS_BRIDGE_URL"] = data["url"]
os.environ["BIONICS_BRIDGE_TOKEN"] = data["token"]

sys.path.insert(0, BIONICS)
os.chdir(BIONICS)

from bionics_tools.ue5_animgraph import ue5_bpdoctor_scan, ue5_bpdoctor_results  # noqa: E402


def show(label, res):
    print(f"\n[{label}] ok={getattr(res,'ok',None)} error={getattr(res,'error',None)!r}")
    d = getattr(res, "data", None)
    if d is None:
        print(f"[{label}] content: {getattr(res,'content','')[:600]}")
        return None
    print(f"[{label}] data keys: {list(d.keys()) if isinstance(d, dict) else type(d).__name__}")
    return d


scan = ue5_bpdoctor_scan(asset_path=ABP)
sd = show("scan", scan)
if isinstance(sd, dict):
    for k in ("grade", "errors", "warnings", "infos", "total", "issue_count", "summary"):
        if k in sd:
            print(f"   scan.{k} = {sd[k]}")

# Pull NULL_ANIM_REF specifically, then all errors.
nullref = ue5_bpdoctor_results(check_code_filter="NULL_ANIM_REF")
nd = show("NULL_ANIM_REF", nullref)

errs = ue5_bpdoctor_results(severity_filter="error")
ed = show("errors", errs)


def print_issues(tag, d):
    if not isinstance(d, dict):
        return
    issues = d.get("issues") or d.get("results") or d.get("findings") or []
    print(f"\n[{tag}] {len(issues)} issue(s):")
    for i, it in enumerate(issues[:40]):
        if isinstance(it, dict):
            code = it.get("code") or it.get("check_code") or "?"
            sev = it.get("severity") or "?"
            path = it.get("path") or it.get("asset_path") or it.get("node") or ""
            msg = it.get("message") or it.get("description") or ""
            print(f"  [{i}] {sev}/{code} {path} :: {msg}")
        else:
            print(f"  [{i}] {it}")


print_issues("NULL_ANIM_REF", nd)
print_issues("errors", ed)

# Persist for deeper read.
out = Path(BIONICS) / "_alpha_task16b_bpdoctor_result.json"
out.write_text(json.dumps({"scan": sd, "nullref": nd, "errors": ed}, indent=2, default=str), encoding="utf-8")
print(f"\n[task16b] full result -> {out} ({out.stat().st_size} bytes)")
