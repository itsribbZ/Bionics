#!/usr/bin/env python3
"""Bionics Execution Script — Wire AnimBP Master Template.

Run this AFTER UE5 is open. Bionics will:
1. Focus UE5 window
2. Open ABP_SW_MasterTemplate (or create it)
3. Wire the AnimGraph step by step using vision + mouse/keyboard
4. Compile and save

Usage:
    python plans/animbp_wire_master_template.py   # run from the Bionics repo root
"""
import logging
import os
import sys
import time
from pathlib import Path

# Bionics project root — resolved from this script's location (portable across machines)
BIONICS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BIONICS_ROOT))
os.chdir(BIONICS_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from core.agent import AgentCore
from core.capture import ScreenCapture
from core.executor import ActionExecutor
from core.planner import ExecutionPlan, PlanStep
from core.safety import SafetyLayer
from core.state import StateMachine
from core.ue5_bridge import UE5Bridge

# ================================================================
# BUILD THE EXECUTION PLAN
# ================================================================

plan = ExecutionPlan(
    name="Wire AnimBP Master Template",
    description="Create and wire ABP_SW_MasterTemplate with universal AnimGraph chain: Locomotion SM -> DefaultSlot -> Output Pose. This is the ONE template all characters duplicate from.",
    prerequisites=[
        "UE5 Editor is open with Sworder721 project",
        "BS_Strafe_WalkRun_RT blend space exists with 11 samples",
        "USWAnimInstance C++ class is compiled",
    ],
    warnings=[
        "If ABP_SW_MasterTemplate already exists, open it instead of creating new",
        "Do NOT delete Output Pose node — it cannot be recreated",
    ],
    steps=[
        PlanStep(
            index=1,
            description="Open Content Browser and navigate to /Game/Variant_Combat/Anims/",
            detailed_instructions="Press Ctrl+Space to open the Content Browser search. Type 'ABP_SW_MasterTemplate'. If it exists, double-click to open it and skip to step 4. If it does NOT exist, close the search (Escape) and proceed to step 2.",
            verification="Content Browser is visible showing the Variant_Combat/Anims folder, or the AnimBP editor is open",
            category="navigation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=2,
            description="Create new Animation Blueprint",
            detailed_instructions="You are in /Game/Variant_Combat/Anims/ folder in Content Browser. RIGHT-CLICK on the empty area in the content panel (the large area showing asset thumbnails, NOT the folder tree). In the context menu that appears, hover over 'Animation' to expand the submenu, then click 'Animation Blueprint'. A dialog will appear asking for Parent Class and Target Skeleton. For Parent Class, search for and select 'SWAnimInstance'. For Target Skeleton, select 'CH_SciFiTrooper_Man_03_Skeleton'. Click OK/Create. A new asset will appear - rename it to 'ABP_SW_MasterTemplate'. DO NOT use console commands or templates - use direct mouse right-click.",
            verification="A new ABP_SW_MasterTemplate asset appears in the Content Browser",
            category="creation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=3,
            description="Open ABP_SW_MasterTemplate",
            detailed_instructions="Double-click ABP_SW_MasterTemplate in Content Browser to open it. Click the 'AnimGraph' tab if not already selected.",
            verification="AnimGraph editor is visible with an Output Pose node",
            category="navigation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=4,
            description="Delete all existing nodes except Output Pose",
            detailed_instructions="Press Ctrl+A to select all nodes. Then hold Ctrl and click on 'Output Pose' node to deselect it. Press Delete key to remove all other nodes. Only 'Output Pose' should remain.",
            verification="AnimGraph shows only the Output Pose node, no other nodes",
            category="deletion",
            is_destructive=True,
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=5,
            description="Create Locomotion State Machine",
            detailed_instructions="Right-click on empty space in the AnimGraph. In the context menu search bar, type 'State Machine'. Click 'Add New State Machine'. A new State Machine node appears. Click on its title text and rename it to 'Locomotion'.",
            verification="A node labeled 'Locomotion' State Machine exists in the AnimGraph",
            category="creation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=6,
            description="Enter the Locomotion State Machine",
            detailed_instructions="Double-click the 'Locomotion' State Machine node to enter it. You should see an 'Entry' node with a green arrow inside the state machine.",
            verification="Inside Locomotion SM — Entry node visible with green arrow",
            category="navigation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=7,
            description="Create IdleWalkRun state",
            detailed_instructions="Right-click on empty space inside the State Machine. Click 'Add State'. Name it 'IdleWalkRun'. Then drag from the 'Entry' green arrow to the 'IdleWalkRun' state to connect them (this sets it as the default state).",
            verification="Entry node has an arrow connecting to IdleWalkRun state",
            category="creation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=8,
            description="Wire BlendSpace Player inside IdleWalkRun",
            detailed_instructions="Double-click 'IdleWalkRun' to enter the state. Right-click empty space -> search 'Blend Space Player' -> click to add it. Select the BlendSpace Player node. In the Details panel on the right, find 'Blend Space' dropdown and select 'BS_Strafe_WalkRun_RT'. On the node, find the 'X (Speed)' pin — drag from it, release in empty space, search 'Speed', select the Speed variable. Find the 'Y (Direction)' pin — drag from it, search 'Direction', select the Direction variable. Finally, drag from the BlendSpace Player's output pose pin (right side) to the 'Animation Pose' result pin.",
            verification="BlendSpace Player node connected: BS_Strafe_WalkRun_RT asset set, Speed and Direction variables wired, output connected to state result",
            category="configuration",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=9,
            description="Return to top-level AnimGraph",
            detailed_instructions="Click the 'AnimGraph' text in the breadcrumb trail at the top of the graph editor. This takes you back to the top-level AnimGraph view.",
            verification="Top-level AnimGraph visible with Locomotion SM node and Output Pose node",
            category="navigation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=10,
            description="Add DefaultSlot node for montage playback",
            detailed_instructions="Right-click on empty space in the AnimGraph. Search for 'Slot' in the context menu. Click to add a 'Slot' node (NOT 'Save Cached Pose'). Select the Slot node. In the Details panel, find 'Slot Name' and set it to 'DefaultGroup.DefaultSlot'.",
            verification="A Slot node exists in the AnimGraph with Slot Name = DefaultGroup.DefaultSlot",
            category="creation",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=11,
            description="Wire the AnimGraph chain: Locomotion SM -> DefaultSlot -> Output Pose",
            detailed_instructions="Drag from the Locomotion SM output pose pin (right side, white arrow) to the Slot node's input pose pin (left side). Then drag from the Slot node's output pose pin (right side) to the Output Pose node's input pin. The chain should flow left to right: Locomotion SM -> Slot -> Output Pose.",
            verification="Three nodes connected in a chain: Locomotion SM -> Slot (DefaultSlot) -> Output Pose. All connections shown as white lines between pose pins.",
            category="configuration",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=12,
            description="Compile the AnimBP",
            detailed_instructions="Click the 'Compile' button in the toolbar (or press F7). Wait for compilation to complete. Look for a green checkmark indicating success.",
            verification="Green checkmark appears on the Compile button — no errors in the compiler output",
            category="verification",
            requires_app="Unreal Engine 5",
        ),
        PlanStep(
            index=13,
            description="Save the AnimBP",
            detailed_instructions="Press Ctrl+S to save the AnimBP. If a save dialog appears, confirm.",
            verification="Asset is saved — no asterisk (*) in the tab title",
            category="configuration",
            requires_app="Unreal Engine 5",
        ),
    ],
)

# ================================================================
# RUN THE AGENT
# ================================================================

print("=" * 60)
print("BIONICS — Wire AnimBP Master Template")
print(f"Plan: {plan.total_steps} steps")
print("=" * 60)

# Connect to UE5 bridge (optional — vision works without it)
bridge = UE5Bridge()
try:
    bridge.check_connection()
    print(f"UE5 Bridge: {bridge.connection_status}")
except:
    print("UE5 Bridge: not connected (vision-only mode)")

# Create agent components
state_machine = StateMachine()
safety = SafetyLayer()
# Headless mode: auto-approve all actions (GUI normally handles confirmations)
safety.set_confirmation_callback(lambda check: True)
safety.set_auto_approve_moderate(True)
capture = ScreenCapture(audit_dir="audit/animbp_wire")
executor = ActionExecutor()

agent = AgentCore(
    state_machine=state_machine,
    safety=safety,
    capture=capture,
    executor=executor,
    step_timeout_s=120,
)

# Load plan
agent.load_plan(plan)

# Set callbacks
def on_step(idx, step):
    print(f"\n>>> Step {step.index}: {step.description}")

def on_error(msg):
    print(f"\n!!! ERROR: {msg}")

agent.set_callbacks(
    on_step_change=on_step,
    on_error=on_error,
)

# Transition through required states: IDLE -> PLANNING -> REVIEWING -> RUNNING
state_machine.transition(state_machine.state.PLANNING)
state_machine.transition(state_machine.state.REVIEWING)

print("\nStarting in 3 seconds — move mouse to corner to abort (failsafe)...")
time.sleep(3)
agent.start()

# Wait for completion
while agent._state.state.name == "RUNNING":
    time.sleep(1)

print(f"\nFinal state: {agent._state.state.name}")
print(f"Completed: {plan.completed_steps}/{plan.total_steps} steps")

if plan.completed_steps == plan.total_steps:
    print("\n" + "=" * 60)
    print("MASTER TEMPLATE WIRED SUCCESSFULLY")
    print("Next: run duplicate_animbp.py in UE5 to create character AnimBPs")
    print("=" * 60)
