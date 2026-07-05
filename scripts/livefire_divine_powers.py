"""Live-fire smoke for v0.7.0 divine_powers MCP wrapper.

Hits the real wrapper end-to-end:
  AutoPlanner construction -> topic detection -> MVP Doctor -> Author chain
  -> UE Knowledge zone heads -> Claude API plan generation -> ToolResult.

execute=False by default (plan-only — no UE5 mutations). Pass --execute to
enable the live execution path against the throwaway test BP.

Targets throwaway TEST assets ONLY (/Game/Tests/BP_EventGraphSmoke for the
EventGraph prompts; /Game/Test/Skel for the `rig` prompt) — never names
Sworder production assets. The `rig` prompt exercises the bionics_tool
execution path: it steers the planner to emit execution_method="bionics_tool"
(ue5_autorig_humanoid) which dispatches a real :8090 call via _invoke_bionics_tool.
Run it AFTER scripts/livefire_uasvc.py has landed the SkeletalMesh.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SAFE_PROMPTS = {
    "tiny": (
        "Inspect the EventGraph of /Game/Tests/BP_EventGraphSmoke and "
        "describe what nodes already exist. Do not add or modify anything."
    ),
    "small": (
        "Add a debug PrintString call on BeginPlay event in "
        "/Game/Tests/BP_EventGraphSmoke that prints "
        "'Bionics divine_powers smoke test'. Wire BeginPlay.then to "
        "PrintString.execute and compile."
    ),
    # Exercises the bionics_tool execution path (planner -> _invoke_bionics_tool -> :8090).
    # Tightly scoped to a single autorig step on the mesh livefire_uasvc.py lands, so it
    # maps deterministically onto ue5_autorig_humanoid rather than a hand-rolled ue5_python.
    "rig": (
        "Validate the skeletal mesh at "
        "/Game/Test/Skel/SK_SW_HumanoidTemplate/SkeletalMeshes/SK_SW_HumanoidTemplate "
        "as a humanoid and build an IKRig for it. Use the native Bionics autorig tool "
        "(ue5_autorig_humanoid) for this — do not hand-write a Python script."
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Actually execute the plan via UE5 bridge (default: plan-only)")
    parser.add_argument("--prompt-key", choices=list(SAFE_PROMPTS.keys()),
                        default="tiny",
                        help="Which canned safe prompt to use (tiny=read-only inspect, small=add+wire+compile)")
    args = parser.parse_args()

    prompt = SAFE_PROMPTS[args.prompt_key]
    print("=" * 70)
    print(f"DIVINE_POWERS LIVE-FIRE  prompt-key={args.prompt_key}  execute={args.execute}")
    print("=" * 70)
    print(f"Prompt: {prompt}")
    print()

    # Ensure registry is populated so the wrapper is importable post-decoration
    from bionics_tools import register_all
    n = register_all()
    print(f"[setup] register_all() = {n} tools")

    from bionics_tools.bionics_core import divine_powers

    t0 = time.time()
    result = divine_powers(prompt=prompt, execute=args.execute)
    elapsed_ms = int((time.time() - t0) * 1000)

    print()
    print("=" * 70)
    print(f"RESULT  ok={result.ok}  elapsed={elapsed_ms}ms")
    print("=" * 70)
    print(f"content: {result.content}")
    if not result.ok:
        print(f"error  : {result.error}")
        return 1

    data = result.data or {}
    print()
    print(f"topics        : {data.get('topics')}")
    print(f"executed      : {data.get('executed')}")
    print(f"bridge_status : {data.get('bridge_status')}")
    print(f"demo_ready    : {data.get('demo_ready')}")
    print(f"run_id        : {data.get('run_id')}")

    diag = data.get("diagnosis") or {}
    findings = diag.get("findings", []) if isinstance(diag, dict) else []
    print(f"diagnosis findings: {len(findings)}")
    for f in findings[:5]:
        sev = f.get("severity", "?")
        msg = (f.get("message") or f.get("description") or "")[:120]
        print(f"  [{sev}] {msg}")

    plan = data.get("plan") or {}
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    print()
    print(f"PLAN  name={plan.get('name')!r}  steps={len(steps)}")
    for i, s in enumerate(steps[:10], 1):
        action = s.get("action") or s.get("tool") or s.get("name") or "?"
        desc = (s.get("description") or "")[:120]
        params = s.get("params") or s.get("arguments") or {}
        print(f"  [{i}] {action}")
        if desc:
            print(f"       {desc}")
        if params:
            # truncate long values
            pretty = {k: (str(v)[:80] + "..." if len(str(v)) > 80 else v) for k, v in params.items()}
            print(f"       params: {pretty}")

    if data.get("execution_results"):
        print()
        print(f"EXECUTION RESULTS ({len(data['execution_results'])}):")
        for i, er in enumerate(data["execution_results"][:10], 1):
            print(f"  [{i}] {json.dumps(er, default=str)[:200]}")

    eco = data.get("ecosystem_context") or {}
    if eco:
        print()
        print("ECOSYSTEM CONTEXT:")
        print(f"  ue_knowledge_zones : {eco.get('ue_knowledge_zones')}")
        print(f"  author_chain       : {eco.get('author_chain')}")
        ws = eco.get("voyager_warm_start") or {}
        print(f"  voyager warm_start : proven={len(ws.get('proven', []))} similar={len(ws.get('similar', []))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
