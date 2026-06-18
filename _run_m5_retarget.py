"""_run_m5_retarget.py - M5 enabler: bake retargeted AnimSequences onto SK_SW_HumanoidTemplate.

Runs ue5_batch_retarget: Mannequin Unarmed locomotion -> RTG_Mannequin_to_SW_HumanoidTemplate
-> /Game/Test/Skel/SK_SW_HumanoidTemplate/Anims. Non-destructive (new target folder).
Editor-mode, no PIE. ASCII-only.
"""
import sys, os, json, pathlib

BIONICS = r"C:\Users\jbro1\Desktop\T1\Bionics"
PROJ = r"C:\Users\jbro1\Documents\Sworder721\MyProject"
sys.path.insert(0, BIONICS)
os.chdir(BIONICS)

inst = pathlib.Path(PROJ) / ".bionics-bridge" / "instance.json"
if inst.exists():
    try:
        tok = json.loads(inst.read_text()).get("token")
        if tok:
            os.environ["BIONICS_BRIDGE_TOKEN"] = tok
            print("M5_RETARGET: token set")
    except Exception as e:
        print("M5_RETARGET: token err:", e)

from bionics_tools.ue5_rigging import ue5_batch_retarget

res = ue5_batch_retarget(
    source_folder="/Game/Characters/Mannequins/Anims/Unarmed",
    target_folder="/Game/Test/Skel/SK_SW_HumanoidTemplate/Anims",
    retargeter_path="/Game/Test/Skel/RTG_Mannequin_to_SW_HumanoidTemplate",
    suffix="_SWHT",
)

ok = getattr(res, "ok", None)
data = getattr(res, "data", None) or {}
print("M5_RETARGET_OK:", ok)
print("M5_RETARGET_CONTENT:", getattr(res, "content", None))
try:
    print("M5_RETARGET_DATA:", json.dumps(data, indent=2, default=str))
except Exception:
    print("M5_RETARGET_DATA(raw):", data)
print("M5_RETARGET_RESULT:", "PASS" if ok else "FAIL")
