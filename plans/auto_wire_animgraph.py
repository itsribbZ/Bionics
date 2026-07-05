"""Bionics Auto Mode — Wire AnimGraph via Vision + Click.

Uses capture_screen → Claude Vision → click to wire the combat AnimGraph
when UE5 Python API can't manipulate graph nodes directly.

Each step:
  1. Capture screen
  2. Send to Claude with specific instruction
  3. Get click coordinates
  4. Execute click/type/hotkey
  5. Wait + verify

Usage:
    python plans/auto_wire_animgraph.py
"""

import base64
import io
import json
import os
import sys
import time

BIONICS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BIONICS_ROOT)

import pyautogui
from anthropic import Anthropic

from core.capture import ScreenCapture

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

client = Anthropic()
capture = ScreenCapture(monitor=0, max_width=1920)

SYSTEM_PROMPT = """You output ONLY raw JSON. No prose. No markdown. No explanation. ONE JSON object per response.

You are a UE5 Editor automation bot analyzing screenshots to return pixel click coordinates.

OUTPUT FORMAT — pick ONE:
{"action":"right_click","x":500,"y":300,"description":"empty canvas area"}
{"action":"click","x":500,"y":300,"description":"menu item"}
{"action":"type","text":"State Machine","description":"search filter"}
{"action":"hotkey","keys":["ctrl","s"],"description":"save"}
{"action":"not_found","reason":"AnimGraph not visible"}
{"action":"already_done"}

RULES:
- x,y are PIXEL coordinates on the screenshot
- ONLY output the JSON object, nothing else
- No markdown fences, no text before or after
- Pick the BEST single action for the instruction given"""


def capture_and_send(instruction: str) -> dict:
    """Capture screen, send to Claude with instruction, get action back."""
    img = capture.capture()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=[{"type": "text", "text": SYSTEM_PROMPT}],
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": instruction},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from mixed prose
        import re
        json_match = re.search(r'\{[^{}]*"action"[^{}]*\}', raw)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        print(f"  [WARN] No JSON found: {raw[:80]}")
        return {"action": "not_found", "reason": f"Non-JSON: {raw[:60]}"}


def execute_action(action: dict) -> bool:
    """Execute a Claude-returned action."""
    act = action.get("action", "not_found")
    desc = action.get("description", "")

    if act == "not_found":
        print(f"  [SKIP] {action.get('reason', 'target not found')}")
        return False

    if act == "already_done":
        print("  [DONE] Step already completed")
        return True

    if act == "click":
        x, y = action["x"], action["y"]
        print(f"  [CLICK] ({x},{y}) — {desc}")
        pyautogui.click(x, y)
        time.sleep(0.3)
        return True

    if act == "right_click":
        x, y = action["x"], action["y"]
        print(f"  [RCLICK] ({x},{y}) — {desc}")
        pyautogui.rightClick(x, y)
        time.sleep(0.3)
        return True

    if act == "double_click":
        x, y = action["x"], action["y"]
        print(f"  [DCLICK] ({x},{y}) — {desc}")
        pyautogui.doubleClick(x, y)
        time.sleep(0.3)
        return True

    if act == "type":
        text = action["text"]
        print(f"  [TYPE] '{text}' — {desc}")
        pyautogui.typewrite(text, interval=0.03)
        time.sleep(0.2)
        return True

    if act == "hotkey":
        keys = action["keys"]
        print(f"  [HOTKEY] {'+'.join(keys)} — {desc}")
        pyautogui.hotkey(*keys)
        time.sleep(0.3)
        return True

    if act == "wait":
        secs = action.get("seconds", 0.5)
        time.sleep(secs)
        return True

    if act == "sequence":
        for step in action.get("steps", []):
            execute_action(step)
            time.sleep(0.2)
        return True

    print(f"  [UNKNOWN] action={act}")
    return False


def run_step(step_num: int, instruction: str, max_retries: int = 2) -> bool:
    """Run one wiring step with retries."""
    print(f"\n{'='*60}")
    print(f"STEP {step_num}: {instruction[:80]}")
    print(f"{'='*60}")

    for attempt in range(max_retries + 1):
        if attempt > 0:
            print(f"  [RETRY] Attempt {attempt + 1}/{max_retries + 1}")
            time.sleep(1.0)

        action = capture_and_send(instruction)
        ok = execute_action(action)
        if ok:
            time.sleep(0.5)  # Let UE5 process
            return True

    print(f"  [FAIL] Step {step_num} failed after {max_retries + 1} attempts")
    return False


# ============================================================
# MAIN — 10 Wiring Steps
# ============================================================

def main():
    print("=" * 60)
    print("BIONICS AUTO MODE — AnimGraph Wiring")
    print("=" * 60)
    print("\nMake sure:")
    print("  - ABP_SWCharacter is open in UE5 editor")
    print("  - You're on the AnimGraph tab")
    print("  - The canvas is visible with the Output Pose node")
    print("\nStarting in 3 seconds...")
    time.sleep(3)

    # ---- PHASE 1: Find a good empty canvas spot ----
    print("\n--- PHASE 1: Locating empty canvas area ---")
    action = capture_and_send(
        "Look at the UE5 AnimGraph canvas. Find a large EMPTY area where I can right-click "
        "to add nodes. Return coordinates for an empty spot with no nodes or wires. "
        "Prefer the center-left area of the canvas."
    )
    if action.get("action") == "not_found":
        print("[!] Can't find AnimGraph canvas. Make sure ABP_SWCharacter is open on AnimGraph tab.")
        return

    # Get canvas position for right-clicks
    canvas_x = action.get("x", 800)
    canvas_y = action.get("y", 400)
    print(f"  Canvas spot: ({canvas_x}, {canvas_y})")

    # ---- PHASE 2: Add nodes via right-click → search → Enter ----
    nodes_to_add = [
        ("State Machine", "Locomotion"),
        ("Slot", None),  # will configure name after
        ("Layered Blend Per Bone", None),
        ("Slot", None),  # second slot for UpperBody
        ("Blend Poses by int", None),
    ]

    created = 0
    positions = {}  # track where each node lands

    for i, (search_term, rename_to) in enumerate(nodes_to_add):
        step_num = i + 1
        # Offset each node so they don't stack
        rx = canvas_x + (i % 3) * 250 - 250
        ry = canvas_y + (i // 3) * 200 - 100

        print(f"\n{'='*60}")
        print(f"STEP {step_num}: Add '{search_term}' node")
        print(f"{'='*60}")

        # Right-click empty canvas
        print(f"  [RCLICK] ({rx},{ry})")
        pyautogui.rightClick(rx, ry)
        time.sleep(0.8)  # wait for context menu

        # Type search term (menu search box is auto-focused in UE5)
        print(f"  [TYPE] '{search_term}'")
        pyautogui.typewrite(search_term, interval=0.04)
        time.sleep(0.6)  # wait for filter

        # Capture to find the result to click
        action = capture_and_send(
            f"A context menu with search results is showing. I just searched for '{search_term}'. "
            f"Find the menu item that matches '{search_term}' and click on it. "
            f"Look for highlighted/first result text in the dropdown list. "
            f"If you see 'Add New State Machine' click that. "
            f"If you see the matching item, return click coordinates for it."
        )

        if action.get("action") in ("click", "double_click"):
            execute_action(action)
            time.sleep(0.5)
            positions[search_term + str(i)] = (rx, ry)
            created += 1

            # Rename if needed (State Machine → Locomotion)
            if rename_to:
                time.sleep(0.3)
                print(f"  [TYPE] '{rename_to}' (renaming)")
                pyautogui.typewrite(rename_to, interval=0.03)
                pyautogui.press("enter")
                time.sleep(0.3)
        else:
            # Fallback: just press Enter (selects first result)
            print("  [ENTER] selecting first search result")
            pyautogui.press("enter")
            time.sleep(0.5)
            created += 1

            if rename_to:
                time.sleep(0.3)
                print(f"  [TYPE] '{rename_to}'")
                pyautogui.typewrite(rename_to, interval=0.03)
                pyautogui.press("enter")
                time.sleep(0.3)

        # Escape any leftover menus
        pyautogui.press("escape")
        time.sleep(0.2)

    print(f"\n{'='*60}")
    print(f"PHASE 2 RESULTS: {created}/{len(nodes_to_add)} nodes added")
    print(f"{'='*60}")

    # ---- PHASE 3: Wire the nodes ----
    print("\n--- PHASE 3: Wiring nodes ---")
    print("Capturing current state to identify node positions...")

    action = capture_and_send(
        "I just added 5 nodes to the AnimGraph canvas: a State Machine (named Locomotion), "
        "two Slot nodes, a Layered Blend Per Bone, and a Blend Poses by Int. "
        "I also see the Output Pose node. Describe what you see and where each node is located. "
        "Return {\"action\":\"click\",\"x\":0,\"y\":0,\"description\":\"I see: [list nodes and positions]\"}"
    )
    if action.get("description"):
        print(f"  Claude sees: {action['description'][:200]}")

    print("\n--- PHASE 3: Manual wiring needed ---")
    print("Nodes are placed. Now wire them:")
    print("  1. Drag Locomotion output → LayeredBlend 'Base Pose'")
    print("  2. Drag BlendByInt output → Slot(UpperBody) input")
    print("  3. Drag Slot(UpperBody) output → LayeredBlend 'Blend Poses 0'")
    print("  4. Drag LayeredBlend output → Slot(DefaultSlot) input")
    print("  5. Drag Slot(DefaultSlot) output → Output Pose")
    print("  6. Bind WeaponGripAlpha → LayeredBlend weight")
    print("  7. Bind WeaponAnimType → ToInt → BlendByInt ActiveChildIndex")
    print("\n  Then: F7 compile, Ctrl+S save")

    # ---- PHASE 4: Compile + Save ----
    print("\n--- Compile + Save? (press Enter to compile, Ctrl+C to skip) ---")
    try:
        input()
        print("  [HOTKEY] F7 — compile")
        pyautogui.press("f7")
        time.sleep(3)
        print("  [HOTKEY] Ctrl+S — save")
        pyautogui.hotkey("ctrl", "s")
        time.sleep(1)
        print("  [OK] Compile + Save executed")
    except (EOFError, KeyboardInterrupt):
        print("  [SKIP] Compile/save skipped")


if __name__ == "__main__":
    main()
