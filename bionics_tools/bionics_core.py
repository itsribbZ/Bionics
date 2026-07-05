"""Bionics Core Tools — Plans, Watch Mode, Audit, Meta.

Tools that expose Bionics's own state and capabilities — what makes
Bionics different from soft-ue-cli: plan execution, watch mode, the
registered tool catalog itself.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from core.bridge import SafetyTier, ToolResult, bionics_tool, get_registry

logger = logging.getLogger("bionics.tools.core")

PROJECT_ROOT = Path(__file__).parent.parent
PLANS_DIR = PROJECT_ROOT / "plans"
AUDIT_DIR = PROJECT_ROOT / "audit"


# ============================================================================
# TOOL CATALOG / META
# ============================================================================


@bionics_tool(
    name="list_tools",
    category="meta",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="List All Tools",
    output_schema={
        "type": "object",
        "properties": {
            "tools": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "category": {"type": "string"},
                        "safety_tier": {"type": "string", "enum": ["safe", "moderate", "destructive"]},
                        "description": {"type": "string"},
                        "read_only": {"type": "boolean"},
                        "destructive": {"type": "boolean"},
                    },
                    "required": ["name", "category", "safety_tier"],
                },
            },
            "count": {"type": "integer"},
        },
        "required": ["tools", "count"],
    },
)
def list_tools(
    category: Annotated[str, "Filter by category (input/capture/ue5_actor/...)"] = "",
    search: Annotated[str, "Substring to match tool names"] = "",
) -> ToolResult:
    """List all registered Bionics tools, optionally filtered by category/search."""
    reg = get_registry()
    specs = reg.list_all()
    if category:
        specs = [s for s in specs if s.category == category]
    if search:
        q = search.lower()
        specs = [s for s in specs if q in s.name.lower() or q in s.description.lower()]
    tools = [
        {
            "name": s.name,
            "category": s.category,
            "safety_tier": s.safety_tier.value,
            "description": s.description.split("\n")[0],
            "read_only": s.annotations.read_only,
            "destructive": s.annotations.destructive,
        }
        for s in specs
    ]
    return ToolResult.success(
        content=f"Found {len(tools)} tools",
        data={"tools": tools, "count": len(tools)},
    )


@bionics_tool(
    name="describe_tool",
    category="meta",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="Describe Tool",
    output_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string"},
            "safety_tier": {"type": "string", "enum": ["safe", "moderate", "destructive"]},
            "input_schema": {"type": "object"},
            "annotations": {"type": "object"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "examples": {"type": "array"},
            "output_schema": {"type": "object"},
        },
        "required": ["name", "description", "category", "safety_tier", "input_schema"],
    },
)
def describe_tool(name: Annotated[str, "Tool name to describe"]) -> ToolResult:
    """Return full specification of a single tool: schema, description, safety tier."""
    reg = get_registry()
    spec = reg.get(name)
    if spec is None:
        return ToolResult.failure(
            f"Unknown tool: {name}. Use list_tools to see available.",
        )
    return ToolResult.success(
        content=f"{spec.name} [{spec.category}] — {spec.description.split(chr(10))[0]}",
        data=spec.to_dict(),
    )


@bionics_tool(
    name="list_categories",
    category="meta",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="List Categories",
    output_schema={
        "type": "object",
        "properties": {
            "total_tools": {"type": "integer"},
            "categories": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
        },
        "required": ["total_tools", "categories"],
    },
)
def list_categories() -> ToolResult:
    """List all tool categories with tool counts."""
    reg = get_registry()
    summary = reg.summary()
    return ToolResult.success(
        content=f"{summary['total_tools']} tools across {len(summary['categories'])} categories",
        data=summary,
    )


# ============================================================================
# PLANS
# ============================================================================


@bionics_tool(
    name="list_plans",
    category="plans",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Plans",
    output_schema={
        "type": "object",
        "properties": {
            "plans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "path": {"type": "string"},
                        "title": {"type": "string"},
                        "step_count": {"type": "integer"},
                        "description": {"type": "string"},
                        "error": {"type": "string"},
                    },
                    "required": ["name", "path"],
                },
            },
            "count": {"type": "integer"},
        },
        "required": ["plans", "count"],
    },
)
def list_plans() -> ToolResult:
    """List all saved plans in the plans/ directory."""
    if not PLANS_DIR.exists():
        return ToolResult.success(
            content="No plans directory", data={"plans": [], "count": 0},
        )
    plans = []
    for p in sorted(PLANS_DIR.glob("*.json")):
        try:
            plan = json.loads(p.read_text(encoding="utf-8"))
            plans.append({
                "name": p.stem,
                "path": str(p.relative_to(PROJECT_ROOT)),
                "title": plan.get("title", p.stem),
                "step_count": len(plan.get("steps", [])),
                "description": plan.get("description", "")[:100],
            })
        except Exception as e:
            plans.append({
                "name": p.stem, "path": str(p), "error": str(e),
            })
    return ToolResult.success(
        content=f"Found {len(plans)} plans",
        data={"plans": plans, "count": len(plans)},
    )


@bionics_tool(
    name="load_plan",
    category="plans",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Load Plan",
)
def load_plan(name: Annotated[str, "Plan name (without .json extension)"]) -> ToolResult:
    """Load a plan JSON file and return its contents (no execution)."""
    # Path traversal guard (matches execute_plan) — strip any directory components.
    if "/" in name or "\\" in name or ".." in name:
        return ToolResult.failure(f"Invalid plan name (path traversal): {name!r}")
    safe_name = Path(name).name
    if not safe_name:
        return ToolResult.failure(f"Invalid plan name: {name!r}")
    plan_path = PLANS_DIR / f"{safe_name.removesuffix('.json').removesuffix('.JSON')}.json"
    if not plan_path.exists():
        return ToolResult.failure(f"Plan not found: {safe_name}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        return ToolResult.success(
            content=f"Loaded plan '{safe_name}' ({len(plan.get('steps', []))} steps)",
            data={"plan": plan, "path": str(plan_path)},
        )
    except Exception as e:
        return ToolResult.failure(f"Failed to parse plan: {e}")


@bionics_tool(
    name="execute_plan",
    category="plans",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    title="Execute Plan",
)
def execute_plan(
    name: Annotated[str, "Plan name (without .json)"],
    dry_run: Annotated[bool, "Don't actually run steps, just validate"] = False,
    continue_on_error: Annotated[bool, "Continue executing after a failed step"] = False,
    allow_destructive: Annotated[bool, "Allow DESTRUCTIVE-tier steps without per-step confirm"] = False,
) -> ToolResult:
    """Execute a saved plan through the Bionics agent (destructive, 2-confirm)."""
    # Path traversal guard — strip any directory components from name
    safe_name = Path(name).name
    if not safe_name or safe_name != name.replace(".json", "").replace(".JSON", ""):
        # Allow optional .json suffix but no path traversal
        if "/" in name or "\\" in name or ".." in name:
            return ToolResult.failure(f"Invalid plan name (path traversal): {name!r}")
    plan_path = PLANS_DIR / f"{safe_name.removesuffix('.json')}.json"
    if not plan_path.exists():
        return ToolResult.failure(f"Plan not found: {safe_name}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            return ToolResult.failure("Plan 'steps' must be a list")
        if dry_run:
            return ToolResult.success(
                content=f"[dry-run] Plan '{name}' has {len(steps)} steps",
                data={"plan": name, "steps": steps, "dry_run": True},
            )
        # Dispatch each step through the registry
        from core.bridge import SafetyTier, ToolGate, get_registry
        gate = ToolGate()
        # Per-step tier-aware execution (do NOT globally bypass safety)
        results = []
        registry = get_registry()
        for i, step in enumerate(steps):
            action = step.get("action") or step.get("tool") or step.get("name")
            params = step.get("params") or step.get("arguments") or {}
            if not action:
                results.append({"step": i, "ok": False, "error": "no action name"})
                if not continue_on_error:
                    break
                continue
            spec = registry.get(action)
            if spec is None:
                results.append({"step": i, "ok": False, "error": f"unknown tool: {action}"})
                if not continue_on_error:
                    break
                continue
            # Safety tier policy: auto-approve SAFE + MODERATE (user approved plan),
            # block DESTRUCTIVE unless allow_destructive=True
            if spec.safety_tier == SafetyTier.DESTRUCTIVE and not allow_destructive:
                results.append({
                    "step": i, "action": action, "ok": False,
                    "error": "DESTRUCTIVE tool blocked (use allow_destructive=true)",
                })
                if not continue_on_error:
                    break
                continue
            res = gate.execute(action, params, confirm_override=True)
            results.append({
                "step": i, "action": action, "ok": res.ok,
                "error": res.error, "content": res.content[:200],
            })
            if not res.ok and not continue_on_error:
                break
        passed = sum(1 for r in results if r.get("ok"))
        all_ran = len(results) == len(steps)
        fully_passed = all_ran and passed == len(steps)
        return ToolResult(
            ok=fully_passed,
            content=f"Plan '{safe_name}': {passed}/{len(steps)} steps succeeded"
                    + ("" if fully_passed else " (partial)"),
            data={
                "plan": safe_name, "results": results,
                "completed": passed, "total": len(steps),
                "fully_passed": fully_passed,
            },
        )
    except Exception as e:
        return ToolResult.failure(f"Plan execution failed: {e}")


@bionics_tool(
    name="save_plan",
    category="plans",
    safety_tier=SafetyTier.MODERATE,
    title="Save Plan",
)
def save_plan(
    name: str,
    steps: Annotated[list[dict], "List of {action, params} steps"],
    title: str = "",
    description: str = "",
) -> ToolResult:
    """Save a plan as JSON to plans/ directory."""
    # Path traversal guard
    safe_name = Path(name).name
    if not safe_name or safe_name != name or "/" in name or "\\" in name or ".." in name:
        return ToolResult.failure(f"Invalid plan name (path traversal): {name!r}")
    if not safe_name.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return ToolResult.failure(
            f"Plan name must be alphanumeric (plus _-.): {name!r}"
        )
    PLANS_DIR.mkdir(exist_ok=True)
    plan_path = PLANS_DIR / f"{safe_name}.json"
    plan = {
        "name": safe_name,
        "title": title or safe_name,
        "description": description,
        "steps": steps,
    }
    try:
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return ToolResult.success(
            content=f"Saved plan '{safe_name}' ({len(steps)} steps)",
            data={"path": str(plan_path), "steps": len(steps)},
        )
    except Exception as e:
        return ToolResult.failure(f"Save failed: {e}")


# ============================================================================
# WATCH MODE
# ============================================================================


@bionics_tool(
    name="watch_state",
    category="watch",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Watch Mode State",
)
def watch_state() -> ToolResult:
    """Return current Watch Mode state (idle/running/paused/error)."""
    try:
        # Read from a known state location if Watch Engine writes one
        state_file = AUDIT_DIR / "watch_state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            return ToolResult.success(
                content=f"Watch: {state.get('status', 'unknown')}", data=state,
            )
        return ToolResult.success(
            content="Watch state file not present; assume IDLE",
            data={"status": "idle", "source": "default"},
        )
    except Exception as e:
        return ToolResult.failure(f"Watch state read failed: {e}")


@bionics_tool(
    name="watch_config",
    category="watch",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Watch Mode Config",
)
def watch_config() -> ToolResult:
    """Return the current Watch Mode configuration from config.yaml."""
    try:
        import yaml
        config_path = PROJECT_ROOT / "config.yaml"
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        watch = cfg.get("watch_mode", {})
        return ToolResult.success(
            content=f"Watch config: poll={watch.get('poll_interval_ms')}ms ssim={watch.get('ssim_threshold')}",
            data=watch,
        )
    except Exception as e:
        return ToolResult.failure(f"Config read failed: {e}")


# ============================================================================
# AUDIT
# ============================================================================


@bionics_tool(
    name="audit_log_tail",
    category="audit",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Audit Log Tail",
)
def audit_log_tail(lines: Annotated[int, "Number of recent lines"] = 50) -> ToolResult:
    """Read the last N lines of the Bionics audit log."""
    log_path = AUDIT_DIR / "bionics.log"
    if not log_path.exists():
        return ToolResult.failure("Audit log not found")
    try:
        all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:]
        return ToolResult.success(
            content=f"{len(tail)} lines from bionics.log",
            data={"lines": tail, "total_lines": len(all_lines)},
        )
    except Exception as e:
        return ToolResult.failure(f"Log read failed: {e}")


@bionics_tool(
    name="list_sessions",
    category="audit",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Audit Sessions",
)
def list_sessions() -> ToolResult:
    """List all recorded audit session directories."""
    sessions_dir = AUDIT_DIR / "sessions"
    if not sessions_dir.exists():
        return ToolResult.success(content="No sessions", data={"sessions": []})
    sessions = sorted(
        [d.name for d in sessions_dir.iterdir() if d.is_dir()],
        reverse=True,
    )[:100]
    return ToolResult.success(
        content=f"{len(sessions)} sessions",
        data={"sessions": sessions, "count": len(sessions)},
    )


@bionics_tool(
    name="list_resumable_sessions",
    category="audit",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Resumable Agent Sessions",
)
def list_resumable_sessions() -> ToolResult:
    """List saved SessionManager sessions that can be resumed by AgentCore.

    Distinct from `list_sessions` (which lists audit screenshot directories).
    This returns the `<session_id>.json` state files with status + step counts
    so a caller can pick one for `AgentCore.resume_from_session(session_id)`.
    """
    try:
        from core.session import SessionManager
        sm = SessionManager()
        sessions = sm.list_sessions()
        resumable = [s for s in sessions if s.get("can_resume")]
        return ToolResult.success(
            content=f"{len(resumable)} resumable of {len(sessions)} total",
            data={
                "resumable": resumable,
                "all_sessions": sessions,
                "resumable_count": len(resumable),
                "total_count": len(sessions),
            },
        )
    except Exception as e:
        return ToolResult.failure(f"Failed to enumerate sessions: {e}")


@bionics_tool(
    name="bionics_get_session_progress",
    category="audit",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    title="Get Session Progress",
    output_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "plan_name": {"type": "string"},
            "state": {"type": "string"},
            "current_step": {"type": "integer"},
            "total_steps": {"type": "integer"},
            "percent": {"type": "number"},
            "updated_at": {"type": "string"},
        },
    },
)
def bionics_get_session_progress() -> ToolResult:
    """Return the latest progress snapshot (percent complete, current step).

    Cheap read — the progress snapshot is a small JSON file written on every
    `SessionManager.save_state` call. Meant for statusline widgets, web
    dashboards, or MCP clients that want to show in-flight plan state without
    loading the full session file.
    """
    try:
        from core.session import SessionManager
        sm = SessionManager()
        progress = sm.get_progress()
        if progress is None:
            return ToolResult.success(
                content="No session in progress",
                data={"session_id": "", "state": "IDLE", "percent": 0.0},
            )
        return ToolResult.success(
            content=(
                f"{progress.get('plan_name', '?')} "
                f"{progress.get('percent', 0)}% "
                f"({progress.get('current_step', 0)}/{progress.get('total_steps', 0)}) "
                f"[{progress.get('state', '?')}]"
            ),
            data=progress,
        )
    except Exception as e:
        return ToolResult.failure(f"Progress read failed: {e}")


@bionics_tool(
    name="bionics_list_active_sessions",
    category="audit",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Active Sessions",
    output_schema={
        "type": "object",
        "properties": {
            "active": {"type": "array"},
            "count": {"type": "integer"},
        },
        "required": ["active", "count"],
    },
)
def bionics_list_active_sessions() -> ToolResult:
    """Return the subset of saved sessions currently in RUNNING state.

    Useful for detecting stuck or crashed agents — if an "active" session's
    updated_at is far in the past, the process likely died without saving a
    terminal state.
    """
    try:
        from core.session import SessionManager
        sm = SessionManager()
        active = sm.list_running_sessions()
        return ToolResult.success(
            content=f"{len(active)} active sessions",
            data={"active": active, "count": len(active)},
        )
    except Exception as e:
        return ToolResult.failure(f"Active sessions listing failed: {e}")


# ============================================================================
# CONFIGURATION
# ============================================================================


@bionics_tool(
    name="get_config",
    category="meta",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get Config",
    output_schema={
        "type": "object",
        "description": "Bionics configuration (full or scoped to one section)",
        "additionalProperties": True,
    },
)
def get_config(section: Annotated[str, "Top-level section (api/capture/safety/...)"] = "") -> ToolResult:
    """Return Bionics configuration (full or single section)."""
    try:
        import yaml
        cfg = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
        if section:
            if section not in cfg:
                return ToolResult.failure(
                    f"Unknown section: {section}. Available: {list(cfg.keys())}",
                )
            return ToolResult.success(
                content=f"config.{section}", data={section: cfg[section]},
            )
        return ToolResult.success(
            content="full config", data=cfg,
        )
    except Exception as e:
        return ToolResult.failure(f"Config read failed: {e}")


@bionics_tool(
    name="version",
    category="meta",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="Bionics Version",
    output_schema={
        "type": "object",
        "properties": {
            "version": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["version", "name"],
    },
)
def version() -> ToolResult:
    """Return Bionics version and build info."""
    try:
        import yaml
        cfg = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
        ver = cfg.get("bionics", {}).get("version", "unknown")
    except Exception:
        ver = "unknown"
    return ToolResult.success(
        content=f"Bionics v{ver}",
        data={
            "version": ver,
            "name": "Bionics",
            "description": "AI Desktop Automation Agent with UE5 integration",
        },
    )


# ============================================================================
# DIVINE POWERS — Top-level NL→UE5 orchestration entry point
# ============================================================================
#
# `divine_powers` is THE flagship Bionics pipeline:
#   Prompt -> MVP Doctor -> Author chain + UE Knowledge -> Plan -> (optional) Execute
#
# Before v0.7.0 the entire pipeline lived only in `core/auto_planner.py` and
# was reachable only by running `plans/combat_animgraph_setup.py` manually —
# no MCP client could invoke it. This wrapper closes the gap surfaced by the
# 2026-05-02 architecture audit (single highest-leverage Sworder unlock).


@bionics_tool(
    name="divine_powers",
    category="bionics",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    title="Divine Powers — NL → Doctor → Plan → Execute",
    output_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "topics": {"type": "array", "items": {"type": "string"}},
            "diagnosis": {"type": "object"},
            "plan": {"type": ["object", "null"]},
            "plan_path": {"type": ["string", "null"]},
            "execution_results": {"type": "array"},
            "demo_ready": {"type": "boolean"},
            "run_id": {"type": "string"},
            "ecosystem_context": {"type": "object"},
            "executed": {"type": "boolean"},
            "bridge_status": {"type": "string"},
        },
        "required": ["prompt", "topics", "executed"],
    },
)
def divine_powers(
    prompt: Annotated[str, "Natural language UE5 task (e.g. 'fix the AnimBP T-pose', 'wire BP_Sword to play swing montage on attack')"],
    execute: Annotated[bool, "If True, attempt live UE5 execution via bridge. If False or bridge unreachable, returns plan only."] = False,
) -> ToolResult:
    """Run the unified Bionics divine_powers pipeline against the current Sworder UE5 project.

    Pipeline phases:
      1. Detect topics from prompt (AnimBP / Combat / AI / Movement / Save / UI / ...)
      2. Run targeted MVP Doctor on those topics — current-state diagnosis
      3. Load Author chain (canonical KB load order) + UE Knowledge zone heads
      4. Generate fix plan from Doctor findings + ecosystem context (Claude API)
      5. If `execute=True` AND UE5 bridge reachable: run plan steps via UE5Bridge
      6. Persist outcome to memory + Voyager warm-start cache

    DESTRUCTIVE when `execute=True` (UE5 editor mutations). Plan-only mode (default)
    is read-only — generates the plan without touching the editor.
    """
    from core.auto_planner import AutoPlanner
    from core.paths import get_ue5_project

    proj = get_ue5_project()
    planner = AutoPlanner(ue5_project_path=str(proj) if proj else "")

    bridge = None
    bridge_status = "not_attempted"
    if execute:
        try:
            from core.ue5_bridge import UE5Bridge
            probe = UE5Bridge()
            probe.check_connection()
            if probe.is_connected:
                bridge = probe
                bridge_status = "connected"
            else:
                # Prefer enum name (e.g. "editor_not_running") over numeric value
                bridge_status = (
                    probe.status.name.lower()
                    if hasattr(probe.status, "name")
                    else str(probe.status)
                )
        except Exception as e:
            bridge_status = f"probe_failed: {e}"
            logger.warning(f"divine_powers UE5 bridge probe failed: {e}")

    try:
        result = planner.divine_powers(prompt=prompt, bridge=bridge)
        result["executed"] = bridge is not None
        result["bridge_status"] = bridge_status

        topics = result.get("topics", [])
        diag = result.get("diagnosis") or {}
        plan = result.get("plan") or {}
        finding_count = len(diag.get("findings", [])) if isinstance(diag, dict) else 0
        step_count = len(plan.get("steps", [])) if isinstance(plan, dict) else 0
        topics_summary = ",".join(topics)[:80] if topics else "GENERAL"

        return ToolResult.success(
            content=(
                f"divine_powers OK — topics=[{topics_summary}], "
                f"findings={finding_count}, plan_steps={step_count}, "
                f"executed={result['executed']} ({bridge_status})"
            ),
            data=result,
        )
    except Exception as e:
        logger.exception("divine_powers failed")
        return ToolResult.failure(f"divine_powers failed: {e}")
