"""Task #16 (decider) — native-Python introspection probe via the :8090 bridge.

READ-ONLY. Decides whether Python can reach the SequencePlayers inside SM_1's rifle states.
If yes -> we can complete #16 (read) and #17 (write) via native python. If no -> rifle
thread is genuinely bridge-blocked (needs C++ FindNodeByName-recurse patch + rebuild, or
manual editor work) and we pivot to the planets thread (Jacob's 05-15 explicit greenlight).
"""
import os
import sys
import json
from pathlib import Path

BIONICS = r"C:\Users\jbro1\Desktop\T1\Bionics"
PROJECT = r"C:\Users\jbro1\Documents\Sworder721\MyProject"

inst = Path(PROJECT) / ".bionics-bridge" / "instance.json"
data = json.loads(inst.read_text(encoding="utf-8"))
os.environ["BIONICS_BRIDGE_URL"] = data["url"]
os.environ["BIONICS_BRIDGE_TOKEN"] = data["token"]

sys.path.insert(0, BIONICS)
os.chdir(BIONICS)

from bionics_tools.ue5_native import call_bridge_tool  # noqa: E402
from bionics_tools._ue5_native_exec import run_python_native  # noqa: E402

# ---- UE5-side introspection script (runs inside the editor's Python interpreter) ----
UE_SCRIPT = r'''
import unreal, json
ABP = "/Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3"
RIFLE = ["Rifle_Idle", "Rifle_Move", "Rifle_Crouch_Idle", "Rifle_Crouch_Move"]
R = {"transport": "ok", "abp_loaded": False, "strategies": {}, "states": {}, "diag": []}

abp = unreal.load_asset(ABP)
R["abp_loaded"] = abp is not None
if abp:
    R["diag"].append("abp class=%s" % abp.get_class().get_name())

def try_strat(name, fn):
    try:
        fn()
        R["strategies"][name] = "ok"
    except Exception as e:
        R["strategies"][name] = "ERR: %s: %s" % (type(e).__name__, e)

# Strategy 1: does UAnimBlueprint expose editor graphs via a property?
def s1():
    for prop in ("AnimGraph", "FunctionGraphs", "UbergraphPages", "graphs"):
        try:
            v = abp.get_editor_property(prop)
            R["diag"].append("prop %s -> %r" % (prop, type(v).__name__))
        except Exception as ex:
            R["diag"].append("prop %s ERR %s" % (prop, ex))
try_strat("s1_editor_props", s1)

# Strategy 2: KismetEditor / AnimationStateMachine library availability
def s2():
    libs = [n for n in dir(unreal) if "StateMachine" in n or "AnimGraph" in n or "AnimBlueprint" in n]
    R["diag"].append("anim libs: %s" % libs[:20])
try_strat("s2_lib_scan", s2)

# Strategy 3: read baked anim-node data off the generated class CDO
def s3():
    gen = abp.generated_class()
    R["diag"].append("gen=%s" % (gen.get_name() if gen else None))
    cdo = unreal.get_default_object(gen) if gen else None
    R["diag"].append("cdo=%r" % (cdo is not None))
    # Try to find anim node struct properties holding a 'sequence'
    if cdo:
        names = [p for p in dir(cdo) if "anim" in p.lower() or "node" in p.lower()]
        R["diag"].append("cdo anim-ish attrs: %s" % names[:20])
try_strat("s3_generated_cdo", s3)

# Strategy 4: enumerate the AnimBP package subobjects for SequencePlayer nodes
def s4():
    found = []
    outer = abp.get_outer() if hasattr(abp, "get_outer") else None
    pkg = abp.get_outermost()
    R["diag"].append("pkg=%s" % (pkg.get_name() if pkg else None))
    # EditorAssetLibrary cannot list subobjects; try unreal.find_object on guessed paths
    base = abp.get_path_name()
    for st in RIFLE:
        # state bound graph nodes are deeply nested; we cannot guess GUIDs, so just record we tried
        pass
    R["diag"].append("s4: no public subobject iterator in UE python")
try_strat("s4_subobject_iter", s4)

print("BIONICS_PROBE_JSON_START")
print(json.dumps(R))
print("BIONICS_PROBE_JSON_END")
'''

res = run_python_native(UE_SCRIPT, timeout_s=30.0, invoke=call_bridge_tool, subdir="alpha_probe")
print("reachable=%s success=%s" % (res.get("reachable"), res.get("success")))
if res.get("error"):
    print("ERROR:", res["error"][:500])
out = res.get("output", "")
# Extract the JSON the UE-side script printed.
if "BIONICS_PROBE_JSON_START" in out:
    body = out.split("BIONICS_PROBE_JSON_START", 1)[1].split("BIONICS_PROBE_JSON_END", 1)[0].strip()
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, indent=2))
    except Exception as e:
        print("parse err:", e)
        print(out[:2000])
else:
    print("--- raw output (first 2000) ---")
    print(out[:2000])
