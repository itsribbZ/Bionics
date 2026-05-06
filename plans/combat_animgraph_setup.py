"""
Bionics Execution Plan: Combat AnimGraph Setup
================================================

Runs the full divine_powers() pipeline to:
1. Diagnose current AnimBP state (AnimBP Doctor)
2. Execute setup_combat_animgraph.py (creates assets, checks IK bones)
3. Wire the AnimGraph chain (SM → Slot → LayeredBlend → Output)
4. Add weapon-type upper body blend
5. Compile + Save

Usage:
    python plans/combat_animgraph_setup.py   # run from the Bionics repo root

Requirements:
    - UE5 editor open with MyProject loaded
    - Python Remote Execution enabled (Edit > Editor Preferences > Python > Remote Execution)
    - Bionics core installed (core/, ue5_modules/)
"""

import json
import os
import sys

# Ensure Bionics root is in path
BIONICS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BIONICS_ROOT)
os.chdir(BIONICS_ROOT)

from core.auto_planner import AutoPlanner
from core.paths import get_ue5_project, get_ue5_python_dir
from core.ue5_bridge import UE5Bridge

# ============================================================
# CONFIG
# ============================================================

_ue5_project = get_ue5_project()
_python_dir = get_ue5_python_dir()
if not _ue5_project or not _python_dir:
    raise SystemExit(
        "combat_animgraph_setup: paths.ue5_project is not configured.\n"
        "  Set it in config.yaml (see config.yaml.example), or export BIONICS_UE5_PROJECT."
    )
UE5_PROJECT = str(_ue5_project)
SETUP_SCRIPT = str(_python_dir / "setup_combat_animgraph.py")

# The EXACT prompt for divine_powers — describes the full AnimGraph target
COMBAT_ANIMGRAPH_PROMPT = """
Set up the combat AnimGraph for Sworder:721's character (USWAnimInstance / ABP_SWCharacter).

== WHAT EXISTS (C++ side — fully wired, do NOT modify C++) ==
All state variables update every tick in NativeUpdateAnimation():
- WeaponAnimType (ESWWeaponAnimType enum: 0=Unarmed, 1=Rifle, 2=Pistol, 3=Shotgun, 4=Melee)
- WeaponGripAlpha (float 0-1, smoothly interpolated — 1.0 when weapon equipped, 0.7 during sprint)
- AimOffsetAlpha (float 0-1, 1.0 when aiming, 0.3 during sprint, 0.0 during dodge/slide)
- LeftHandIKLocation_CS (FVector component-space — reads from weapon LeftHandIK socket every tick)
- LeftHandIKAlpha (float 0-1 — 0.0 during dodge/reload/melee, 1.0 otherwise)
- Speed (float cm/s), Direction (float -180 to 180)
- AimPitch (float -90 to 90), AimYaw (float -180 to 180)
- bIsSprinting, bIsSliding, bIsDodging, bIsVaulting, bIsCrouched, bIsInAir
- JumpPhase (ESWJumpPhase: None/Start/Loop/Landing)

== ANIMGRAPH CHAIN TO BUILD (exact node topology) ==

Output Pose
  <- [Slot Node] "DefaultSlot" (full body montages: dodge, roll, death, finisher)
    <- [Layered Blend Per Bone] (Branch Filter: spine_01, BlendDepth=1, MeshSpaceRotationBlend=ON)
      Base Pose (lower body):
        <- [State Machine] "Locomotion"
            States: Idle, WalkRun (BlendSpace by Speed+Direction), Sprint, Crouch, Slide,
                    JumpStart, JumpLoop, JumpLand
            Transitions: Speed/bIsSprinting/bIsCrouched/bIsSliding/JumpPhase
      Blend Pose 0 (upper body):
        <- [Slot Node] "UpperBody" (upper body montages: reload, fire, equip)
          <- [Blend Poses by Int] (Active Child Index pin = WeaponAnimType int, Blend Time=0.25)
            Pin 0: Single-frame idle pose (or cached unarmed)
            Pin 1: Single-frame rifle hold pose
            Pin 2: Single-frame pistol hold pose
            Pin 3: Single-frame shotgun hold pose
            Pin 4: Single-frame melee/sword hold pose

  NOTE: After Layered Blend Per Bone, optionally add:
  - [Apply Additive] for Aim Offset (alpha = AimOffsetAlpha)
  - [Two Bone IK] or [Control Rig] for Left Hand IK (alpha = LeftHandIKAlpha)

== LAYERED BLEND PER BONE SETTINGS ==
- Branch Filter 0: Bone Name = spine_01, Blend Depth = 1
- Check: "Mesh Space Rotation Blend" = TRUE (prevents weapon twist during strafe)
- Blend Weight = WeaponGripAlpha (bind to the variable)

== STATE MACHINE "Locomotion" ==
Entry → Idle
Idle → WalkRun: Speed > 10
WalkRun → Idle: Speed < 10
WalkRun → Sprint: bIsSprinting == true
Sprint → WalkRun: bIsSprinting == false
Any → JumpStart: JumpPhase == Start
JumpStart → JumpLoop: TimeInAir > 0.15
JumpLoop → JumpLand: JumpPhase == Landing
JumpLand → Idle: (remaining time < 0.1)
Any → Slide: bIsSliding == true
Slide → WalkRun: bIsSliding == false
Any → Crouch: bIsCrouched == true AND Speed < 10
Crouch → Idle: bIsCrouched == false

== SLOT NODE CONFIGURATION ==
DefaultSlot: Group = DefaultGroup, Blend In = 0.15s, Blend Out = 0.25s
UpperBody: Group = DefaultGroup, Blend In = 0.10s, Blend Out = 0.20s

== FIRST: Run setup_combat_animgraph.py ==
Before any AnimGraph wiring, run the Python setup script that:
- Finds and validates the AnimBP
- Checks skeleton virtual bones for IK
- Reports asset status
- Opens the AnimBP in editor

Script path: <UE5_PROJECT>/Content/Python/setup_combat_animgraph.py
"""

# ============================================================
# EXECUTION
# ============================================================

def main():
    print("=" * 60)
    print("BIONICS — Combat AnimGraph Setup")
    print("=" * 60)

    # Initialize planner
    planner = AutoPlanner(ue5_project_path=UE5_PROJECT)

    # Connect to UE5
    bridge = UE5Bridge()
    connected = bridge.check_connection()
    print(f"UE5 Bridge: {'CONNECTED' if connected else 'DISCONNECTED'}")

    if not connected:
        print("\n[!] UE5 editor must be running with Python Remote Execution enabled.")
        print("    Edit > Editor Preferences > Python > Enable Remote Execution")
        print("\n    Generating plan-only (no execution)...")

    # Step 1: Run setup script first (if connected)
    if connected:
        print("\n--- Step 1: Running setup_combat_animgraph.py in UE5 ---")
        try:
            with open(SETUP_SCRIPT) as f:
                setup_code = f.read()
            result = bridge.execute_python(setup_code)
            if result.success:
                print("  Setup script: PASS")
                for line in (result.output or "").split("\n"):
                    if "[ANIMGRAPH_SETUP]" in line:
                        print(f"  {line.strip()}")
            else:
                print(f"  Setup script: FAIL — {result.error}")
        except Exception as e:
            print(f"  Setup script error: {e}")

    # Step 2: Run divine_powers with the full AnimGraph prompt
    print("\n--- Step 2: Running divine_powers() ---")
    try:
        result = planner.divine_powers(
            prompt=COMBAT_ANIMGRAPH_PROMPT,
            bridge=bridge if connected else None,
        )

        print(f"\nTopics detected: {result.get('topics', [])}")
        print(f"Demo ready: {result.get('demo_ready', False)}")

        # Print diagnosis
        diagnosis = result.get("diagnosis", {})
        findings = diagnosis.get("findings", [])
        print(f"\nDoctor: {diagnosis.get('checks_run', 0)} checks, {len(findings)} findings")
        for f in findings[:10]:
            severity = f.get("severity", "INFO")
            print(f"  [{severity}] {f.get('message', '')}")

        # Print plan
        plan = result.get("plan")
        if plan:
            plan_name = plan.get("name", "unnamed")
            steps = plan.get("steps", [])
            print(f"\nPlan: {plan_name} ({len(steps)} steps)")
            for step in steps:
                idx = step.get("index", "?")
                desc = step.get("description", "")
                method = step.get("execution_method", "unknown")
                print(f"  {idx}. [{method}] {desc}")

            # Save plan to file for inspection
            plan_file = os.path.join(BIONICS_ROOT, "plans", "combat_animgraph_plan.json")
            with open(plan_file, "w") as pf:
                json.dump(plan, pf, indent=2)
            print(f"\nPlan saved to: {plan_file}")

        # Print execution results
        exec_results = result.get("execution_results", [])
        if exec_results:
            print(f"\nExecution: {len(exec_results)} steps")
            for r in exec_results:
                status = "PASS" if r.get("success") else "FAIL" if r.get("success") is False else "MANUAL"
                step_idx = r.get("step", "?")
                print(f"  Step {step_idx}: {status}")
        else:
            print("\nNo execution performed (plan-only or disconnected)")

    except Exception as e:
        print(f"\ndivine_powers error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()
