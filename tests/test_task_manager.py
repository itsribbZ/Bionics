"""Tests for `core/task_manager.py` + `bionics_tools/task_tools.py`.

Verifies:
  - Submit → RUNNING → COMPLETED lifecycle
  - Failure path records error
  - Cancellation works on PENDING, refuses on RUNNING/COMPLETED
  - 5 MCP tools are all registered + return proper data
  - Filter-by-status in list_tasks
"""
from __future__ import annotations

import time

import pytest

from bionics_tools import register_all
from core.bridge import (
    SafetyTier,
    ToolGate,
    ToolResult,
    bionics_tool,
    get_registry,
)
from core.task_manager import TaskStatus, get_task_manager

register_all()


# ---------- Test tools -----------------------------------------------------

@bionics_tool(
    name="_task_ok",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
)
def _task_ok(wait_ms: int = 0, value: str = "hi") -> ToolResult:
    if wait_ms:
        time.sleep(wait_ms / 1000.0)
    return ToolResult.success(content=value, data={"value": value})


@bionics_tool(
    name="_task_fail",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
)
def _task_fail() -> ToolResult:
    return ToolResult.failure("boom")


@bionics_tool(
    name="_task_throws",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
)
def _task_throws() -> ToolResult:
    raise RuntimeError("exploded")


# ---------- Direct TaskManager tests --------------------------------------

@pytest.fixture
def mgr():
    m = get_task_manager()
    m.clear_completed()
    return m


def test_submit_unknown_tool_raises(mgr):
    with pytest.raises(ValueError, match="Unknown tool"):
        mgr.submit("_does_not_exist", {})


def test_submit_runs_task_to_completion(mgr):
    task_id = mgr.submit("_task_ok", {"value": "hello"})
    task = mgr.wait(task_id, timeout=5.0)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.result is not None
    assert task.result["data"]["value"] == "hello"
    assert task.started_at > 0
    assert task.completed_at >= task.started_at


def test_failing_tool_records_failed_status(mgr):
    task_id = mgr.submit("_task_fail", {})
    task = mgr.wait(task_id, timeout=5.0)
    assert task.status == TaskStatus.FAILED
    assert task.error and "boom" in task.error


def test_exception_records_failed_status(mgr):
    task_id = mgr.submit("_task_throws", {})
    task = mgr.wait(task_id, timeout=5.0)
    assert task.status == TaskStatus.FAILED
    assert "RuntimeError" in task.error
    assert "exploded" in task.error


def test_cancel_running_task_refuses(mgr):
    # Submit a slow task so it definitely starts running.
    task_id = mgr.submit("_task_ok", {"wait_ms": 100})
    # Spin until RUNNING so we don't accidentally cancel while PENDING.
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if mgr.get_status(task_id) == TaskStatus.RUNNING:
            break
        time.sleep(0.01)
    # Now cancellation must fail (Python has no preemption).
    result = mgr.cancel(task_id)
    assert result is False
    mgr.wait(task_id, timeout=2.0)


def test_list_tasks_filters_by_status(mgr):
    t1 = mgr.submit("_task_ok", {"value": "a"})
    t2 = mgr.submit("_task_fail", {})
    mgr.wait(t1, timeout=2.0)
    mgr.wait(t2, timeout=2.0)

    completed = mgr.list_tasks(status_filter=TaskStatus.COMPLETED)
    failed = mgr.list_tasks(status_filter=TaskStatus.FAILED)

    completed_ids = {t.id for t in completed}
    failed_ids = {t.id for t in failed}
    assert t1 in completed_ids
    assert t2 in failed_ids


def test_clear_completed_removes_terminal_states(mgr):
    t1 = mgr.submit("_task_ok", {})
    t2 = mgr.submit("_task_fail", {})
    mgr.wait(t1, timeout=2.0)
    mgr.wait(t2, timeout=2.0)

    assert mgr.get_task(t1) is not None
    removed = mgr.clear_completed()
    assert removed >= 2
    assert mgr.get_task(t1) is None
    assert mgr.get_task(t2) is None


# ---------- MCP tool wiring tests -----------------------------------------

MCP_TASK_TOOLS = [
    "bionics_task_submit",
    "bionics_task_status",
    "bionics_task_result",
    "bionics_task_cancel",
    "bionics_task_list",
]


@pytest.mark.parametrize("name", MCP_TASK_TOOLS)
def test_task_mcp_tools_registered(name):
    spec = get_registry().get(name)
    assert spec is not None
    assert spec.category == "tasks"


def test_task_submit_tool_returns_task_id():
    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute(
        "bionics_task_submit",
        {"tool_name": "_task_ok", "args": {"value": "mcp"}},
    )
    assert result.ok
    assert result.data["task_id"].startswith("task_")
    assert result.data["status"] == TaskStatus.PENDING.value
    # Drain it
    mgr = get_task_manager()
    mgr.wait(result.data["task_id"], timeout=5.0)


def test_task_status_tool_reports_current_state():
    mgr = get_task_manager()
    task_id = mgr.submit("_task_ok", {})
    mgr.wait(task_id, timeout=5.0)

    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute("bionics_task_status", {"task_id": task_id})
    assert result.ok
    assert result.data["status"] == TaskStatus.COMPLETED.value
    assert result.data["task_id"] == task_id


def test_task_result_tool_returns_full_record():
    mgr = get_task_manager()
    task_id = mgr.submit("_task_ok", {"value": "result"})
    mgr.wait(task_id, timeout=5.0)

    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute("bionics_task_result", {"task_id": task_id})
    assert result.ok
    assert result.data["status"] == TaskStatus.COMPLETED.value
    assert result.data["result"] is not None
    assert result.data["result"]["data"]["value"] == "result"


def test_task_list_tool_filter_validates_status():
    gate = ToolGate()
    gate.set_bypass_safety(True)
    bad = gate.execute("bionics_task_list", {"status": "bogus"})
    assert bad.ok is False
    assert "Invalid status" in bad.error


def test_task_cancel_tool_rejects_unknown_id():
    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute("bionics_task_cancel", {"task_id": "task_notreal"})
    assert result.ok is False
    assert "Unknown task_id" in result.error


# ---------- DESTRUCTIVE gate tests (v0.5.6) -------------------------------
# Mirrors mcp_server.py::_make_mcp_wrapper — TaskManager.submit must reject
# DESTRUCTIVE-tier tools when BIONICS_MCP_ALLOW_DESTRUCTIVE is not truthy,
# closing the bypass that previously let bionics_task_submit execute any
# DESTRUCTIVE tool through the task path.


@bionics_tool(
    name="_task_destructive_marker",
    category="test",
    safety_tier=SafetyTier.DESTRUCTIVE,
    read_only=False,
)
def _task_destructive_marker() -> ToolResult:
    return ToolResult.success(content="should-not-run", data={})


def test_destructive_submit_blocked_without_env(monkeypatch, mgr):
    monkeypatch.delenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", raising=False)
    with pytest.raises(PermissionError, match="DESTRUCTIVE"):
        mgr.submit("_task_destructive_marker", {})


def test_destructive_submit_allowed_when_env_set(monkeypatch, mgr):
    monkeypatch.setenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", "true")
    task_id = mgr.submit("_task_destructive_marker", {})
    task = mgr.wait(task_id, timeout=2.0)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED


def test_destructive_env_truthy_variants_all_allow(monkeypatch, mgr):
    for value in ("true", "1", "yes", "on", "TRUE", "Yes"):
        monkeypatch.setenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", value)
        # Should not raise.
        task_id = mgr.submit("_task_destructive_marker", {})
        mgr.wait(task_id, timeout=2.0)


def test_destructive_env_falsy_variants_all_block(monkeypatch, mgr):
    for value in ("", "false", "0", "no", "off", "False", "anything-else"):
        monkeypatch.setenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", value)
        with pytest.raises(PermissionError):
            mgr.submit("_task_destructive_marker", {})


def test_bionics_task_submit_returns_structured_failure_for_destructive(monkeypatch):
    """The MCP wrapper must surface PermissionError as a clean ToolResult.failure,
    not propagate the raw exception through the MCP server."""
    monkeypatch.delenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", raising=False)
    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute(
        "bionics_task_submit",
        {"tool_name": "_task_destructive_marker", "args": {}},
    )
    assert result.ok is False
    assert "DESTRUCTIVE" in result.error
    assert "BIONICS_MCP_ALLOW_DESTRUCTIVE" in result.error


# ---------- v0.5.8: _tasks auto-evict + bionics_task_clear ----------------
# Long-running MCP sessions previously leaked tasks indefinitely (clear_completed
# existed but nothing called it). v0.5.8 adds (a) threshold-based auto-evict in
# submit() and (b) an explicit bionics_task_clear MCP tool.


def test_auto_evict_fires_at_threshold(mgr, monkeypatch):
    """Submit beyond the soft cap must trigger clear_completed inside submit()."""
    # Lower the threshold so the test runs in milliseconds.
    monkeypatch.setattr(type(mgr), "_AUTO_EVICT_THRESHOLD", 3)
    # Fill the dict with completed tasks past the threshold.
    for i in range(3):
        tid = mgr.submit("_task_ok", {"value": str(i)})
        mgr.wait(tid, timeout=2.0)
    assert len(mgr._tasks) == 3  # all completed, just below trigger
    # The next submit hits the threshold (len >= 3), evicts the 3 terminal tasks
    # before inserting the new one.
    tid = mgr.submit("_task_ok", {"value": "trigger"})
    # Only the brand-new task remains.
    assert len(mgr._tasks) == 1
    assert tid in mgr._tasks
    mgr.wait(tid, timeout=2.0)


def test_auto_evict_preserves_non_terminal_tasks(mgr, monkeypatch):
    """Auto-evict must NEVER clear PENDING/RUNNING tasks — only terminals."""
    monkeypatch.setattr(type(mgr), "_AUTO_EVICT_THRESHOLD", 2)
    # 1 completed + 1 long-running (still RUNNING when next submit fires)
    done_id = mgr.submit("_task_ok", {})
    mgr.wait(done_id, timeout=2.0)
    slow_id = mgr.submit("_task_ok", {"wait_ms": 500})
    # Submit triggers auto-evict (len == 2 >= threshold) but slow_id is still RUNNING
    new_id = mgr.submit("_task_ok", {})
    assert slow_id in mgr._tasks  # not evicted
    assert done_id not in mgr._tasks  # was COMPLETED, evicted
    assert new_id in mgr._tasks
    mgr.wait(slow_id, timeout=3.0)
    mgr.wait(new_id, timeout=2.0)


def test_bionics_task_clear_tool_registered():
    spec = get_registry().get("bionics_task_clear")
    assert spec is not None
    assert spec.category == "tasks"
    assert spec.safety_tier == SafetyTier.SAFE


def test_bionics_task_clear_tool_removes_terminal_tasks(mgr):
    t1 = mgr.submit("_task_ok", {})
    t2 = mgr.submit("_task_fail", {})
    mgr.wait(t1, timeout=2.0)
    mgr.wait(t2, timeout=2.0)
    before = len(mgr._tasks)
    assert before >= 2
    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute("bionics_task_clear", {})
    assert result.ok
    assert result.data["removed"] >= 2
    assert mgr.get_task(t1) is None
    assert mgr.get_task(t2) is None
