"""Task #16 — query SM_1 rifle-state Sequence assignments (PURE QUERY, zero mutation).

Reads the live bridge token from instance.json (rotates per editor restart), queries
ABP_SciFiTrooperManV3's AnimGraph via the native C++ ue5_animgraph tool, and reports
whether the 4 rifle states (Rifle_Idle/Move/Crouch_Idle/Crouch_Move) have empty Sequences.
"""
import os
import sys
import json
from pathlib import Path

BIONICS = r"C:\Users\jbro1\Desktop\T1\Bionics"
PROJECT = r"C:\Users\jbro1\Documents\Sworder721\MyProject"
ABP = "/Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3"

# --- resolve live bridge token from instance.json (no hardcode; survives rotation) ---
inst = Path(PROJECT) / ".bionics-bridge" / "instance.json"
data = json.loads(inst.read_text(encoding="utf-8"))
os.environ["BIONICS_BRIDGE_URL"] = data["url"]
os.environ["BIONICS_BRIDGE_TOKEN"] = data["token"]
print(f"[task16] bridge url={data['url']} project={data.get('project')} pid={data.get('pid')}")

sys.path.insert(0, BIONICS)
os.chdir(BIONICS)

from bionics_tools.ue5_animgraph import ue5_query_animgraph  # noqa: E402

res = ue5_query_animgraph(ABP)
print(f"[task16] ok={getattr(res, 'ok', None)} error={getattr(res, 'error', None)!r}")

payload = getattr(res, "data", None)
if payload is None:
    print("[task16] no .data on result; raw content preview:")
    print(getattr(res, "content", "")[:1000])
    sys.exit(0)

# Dump top-level shape so we learn what the query returns.
def keys_of(x):
    return list(x.keys()) if isinstance(x, dict) else type(x).__name__

print(f"[task16] payload top-level keys: {keys_of(payload)}")

# Walk the whole structure, collect every node mentioning a rifle state OR a Sequence prop.
RIFLE = ("Rifle_Idle", "Rifle_Move", "Rifle_Crouch_Idle", "Rifle_Crouch_Move")
hits = []

def walk(obj, path=""):
    if isinstance(obj, dict):
        blob = json.dumps(obj)[:400]
        name = obj.get("name") or obj.get("node_name") or obj.get("state_name") or ""
        if any(r.lower() in str(name).lower() for r in RIFLE) or \
           any(r in blob for r in RIFLE) or \
           ("sequence" in blob.lower() and ("State" in path or "state" in blob.lower())):
            hits.append((path, name, blob))
        for k, v in obj.items():
            walk(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]")

walk(payload)

# Show state machines / states summary explicitly.
print("\n[task16] === STATE MACHINE / STATE SUMMARY ===")
sms = []
def find_sm(obj, path=""):
    if isinstance(obj, dict):
        t = str(obj.get("type", "")) + str(obj.get("class", "")) + str(obj.get("node_class", ""))
        if "StateMachine" in t or "states" in obj:
            sms.append((path, obj))
        for k, v in obj.items():
            find_sm(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_sm(v, f"{path}[{i}]")
find_sm(payload)
for path, sm in sms:
    nm = sm.get("name") or sm.get("node_name") or "?"
    states = sm.get("states")
    print(f"  SM @ {path} name={nm} states={states if isinstance(states,(list,dict)) else keys_of(states)}")

print(f"\n[task16] === RIFLE / SEQUENCE HITS ({len(hits)}) ===")
for path, name, blob in hits[:40]:
    print(f"  @ {path}\n     name={name}\n     {blob}\n")

# Persist full payload for deeper inspection if needed.
out = Path(BIONICS) / "_alpha_task16_result.json"
out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
print(f"[task16] full payload written -> {out} ({out.stat().st_size} bytes)")
