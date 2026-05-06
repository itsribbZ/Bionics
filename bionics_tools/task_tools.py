"""MCP-callable tools for the async task manager.

Exposes the `core.task_manager.TaskManager` lifecycle to MCP clients so agents
can kick off long-running UE5 ops (hot reload, batch retarget, big compiles)
without blocking the MCP connection.

Pattern:

    task_id = bionics_task_submit("ue5_live_coding", {})
    # ...later, poll...
    status = bionics_task_status(task_id)
    # ...once completed...
    result = bionics_task_result(task_id)

`ue5_live_coding`, `ue5_batch_retarget`, and `ue5_compile_blueprint` are the
obvious candidates. Clients decide whether to await synchronously or hand the
task off; this module just provides the scaffolding.
"""
from __future__ import annotations

from typing import Annotated

from core.bridge import SafetyTier, ToolResult, bionics_tool
from core.task_manager import TaskStatus, get_task_manager


@bionics_tool(
    name="bionics_task_submit",
    category="tasks",
    safety_tier=SafetyTier.SAFE,
    read_only=False,
    title="Submit Async Task",
    output_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "status": {"type": "string"},
            "tool_name": {"type": "string"},
        },
        "required": ["task_id", "status", "tool_name"],
    },
)
def bionics_task_submit(
    tool_name: Annotated[str, "Name of the registered tool to invoke asynchronously"],
    args: Annotated[dict, "Arguments dict for the target tool"] | None = None,
) -> ToolResult:
    """Queue an existing Bionics tool for background execution. Returns a task_id.

    Poll with `bionics_task_status` / `bionics_task_result`.
    """
    mgr = get_task_manager()
    try:
        task_id = mgr.submit(tool_name, args or {})
    except ValueError as e:
        return ToolResult.failure(str(e))
    except PermissionError as e:
        # TaskManager.submit raises PermissionError when a DESTRUCTIVE tool is
        # submitted without BIONICS_MCP_ALLOW_DESTRUCTIVE set. Surface as a
        # structured failure so MCP clients get a clean ToolResult, not a
        # raw exception trace from the MCP server wrapper.
        return ToolResult.failure(str(e))
    return ToolResult.success(
        content=f"Task queued: {task_id}",
        data={
            "task_id": task_id,
            "status": TaskStatus.PENDING.value,
            "tool_name": tool_name,
        },
    )


@bionics_tool(
    name="bionics_task_status",
    category="tasks",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    title="Task Status",
    output_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": [s.value for s in TaskStatus],
            },
            "tool_name": {"type": "string"},
            "elapsed_ms": {"type": "number"},
        },
        "required": ["task_id", "status"],
    },
)
def bionics_task_status(
    task_id: Annotated[str, "Task identifier from bionics_task_submit"],
) -> ToolResult:
    """Return current status of an async task without blocking."""
    mgr = get_task_manager()
    task = mgr.get_task(task_id)
    if task is None:
        return ToolResult.failure(f"Unknown task_id: {task_id}")
    d = task.to_dict()
    return ToolResult.success(
        content=f"{task_id}: {d['status']}",
        data={
            "task_id": d["id"],
            "status": d["status"],
            "tool_name": d["tool_name"],
            "elapsed_ms": d["elapsed_ms"],
        },
    )


@bionics_tool(
    name="bionics_task_result",
    category="tasks",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Task Result",
)
def bionics_task_result(
    task_id: Annotated[str, "Task identifier from bionics_task_submit"],
) -> ToolResult:
    """Return the full task record (status + result + timing)."""
    mgr = get_task_manager()
    task = mgr.get_task(task_id)
    if task is None:
        return ToolResult.failure(f"Unknown task_id: {task_id}")
    d = task.to_dict()
    if task.status == TaskStatus.FAILED:
        return ToolResult.failure(task.error, **d)
    return ToolResult.success(
        content=f"{task_id} [{d['status']}]",
        data=d,
    )


@bionics_tool(
    name="bionics_task_cancel",
    category="tasks",
    safety_tier=SafetyTier.SAFE,
    read_only=False,
    title="Cancel Task",
)
def bionics_task_cancel(
    task_id: Annotated[str, "Task identifier to cancel"],
) -> ToolResult:
    """Cancel a pending task. Running tasks cannot be preempted."""
    mgr = get_task_manager()
    cancelled = mgr.cancel(task_id)
    if cancelled:
        return ToolResult.success(
            content=f"{task_id} cancelled",
            data={"task_id": task_id, "cancelled": True},
        )
    task = mgr.get_task(task_id)
    if task is None:
        return ToolResult.failure(f"Unknown task_id: {task_id}")
    return ToolResult.failure(
        f"Cannot cancel {task_id} — status is {task.status.value}",
        task_id=task_id,
        status=task.status.value,
    )


@bionics_tool(
    name="bionics_task_list",
    category="tasks",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Tasks",
    output_schema={
        "type": "object",
        "properties": {
            "tasks": {"type": "array"},
            "count": {"type": "integer"},
        },
        "required": ["tasks", "count"],
    },
)
def bionics_task_list(
    status: Annotated[str, "Filter: pending | running | completed | failed | cancelled"] = "",
) -> ToolResult:
    """List tasks, optionally filtered by status."""
    mgr = get_task_manager()
    status_filter = None
    if status:
        try:
            status_filter = TaskStatus(status.lower())
        except ValueError:
            return ToolResult.failure(
                f"Invalid status: {status}. Valid: {[s.value for s in TaskStatus]}"
            )
    tasks = mgr.list_tasks(status_filter=status_filter)
    return ToolResult.success(
        content=f"{len(tasks)} tasks",
        data={"tasks": [t.to_dict() for t in tasks], "count": len(tasks)},
    )


@bionics_tool(
    name="bionics_task_clear",
    category="tasks",
    safety_tier=SafetyTier.SAFE,
    read_only=False,
    title="Clear Completed Tasks",
    output_schema={
        "type": "object",
        "properties": {
            "removed": {"type": "integer"},
        },
        "required": ["removed"],
    },
)
def bionics_task_clear() -> ToolResult:
    """Evict all terminal (completed/failed/cancelled) tasks from the registry.

    Long-running MCP sessions accumulate tasks indefinitely otherwise. The
    TaskManager also auto-evicts at a soft cap (~500 tasks), but exposing
    this tool lets agents drop the dict explicitly when they know they're
    done with a fan-out batch.
    """
    mgr = get_task_manager()
    removed = mgr.clear_completed()
    return ToolResult.success(
        content=f"Removed {removed} terminal tasks",
        data={"removed": removed},
    )


def register() -> int:
    """Return the number of tools defined in this module (for __init__.py)."""
    return 6
