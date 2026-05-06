"""Tests for the session progress tracker added 2026-04-23 (late PM).

Covers:
  - SessionManager writes progress.json on save_state
  - get_progress() reads it back
  - list_running_sessions() filters correctly
  - 2 MCP tools (bionics_get_session_progress, bionics_list_active_sessions)
    are registered and return proper data
"""
from __future__ import annotations

import json

import pytest

from bionics_tools import register_all
from core.bridge import ToolGate, get_registry
from core.planner import ExecutionPlan, PlanStep
from core.session import SessionManager
from core.state import AgentState, StateMachine

register_all()


@pytest.fixture
def plan():
    return ExecutionPlan(
        name="test_plan",
        description="5-step test",
        steps=[
            PlanStep(
                index=i,
                description=f"step {i}",
                detailed_instructions="",
                verification="",
                is_destructive=False,
                requires_app="",
                category="navigation",
                status="pending",
            )
            for i in range(5)
        ],
    )


@pytest.fixture
def session_mgr(tmp_path):
    return SessionManager(session_dir=tmp_path / "sessions")


def _force_running(state: StateMachine) -> None:
    """Walk IDLE → PLANNING → REVIEWING → RUNNING (the only legal path)."""
    assert state.transition(AgentState.PLANNING)
    assert state.transition(AgentState.REVIEWING)
    assert state.transition(AgentState.RUNNING)


def test_save_state_writes_progress_json(plan, session_mgr):
    state = StateMachine()
    state.total_steps = 5
    state.current_step = 2
    _force_running(state)
    session_mgr.create_session(plan, state)
    session_mgr.save_state(plan, state)

    target = session_mgr._session_dir.parent / "progress.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["session_id"] == session_mgr.session_id
    assert data["plan_name"] == "test_plan"
    assert data["current_step"] == 2
    assert data["total_steps"] == 5
    assert data["percent"] == 40.0
    assert data["state"] == "RUNNING"
    assert "updated_at" in data


def test_get_progress_reads_latest_snapshot(plan, session_mgr):
    state = StateMachine()
    state.total_steps = 5
    state.current_step = 0
    session_mgr.create_session(plan, state)

    state.current_step = 3
    session_mgr.save_state(plan, state)

    progress = session_mgr.get_progress()
    assert progress is not None
    assert progress["current_step"] == 3
    assert progress["percent"] == 60.0


def test_get_progress_returns_none_when_no_session_ever_saved(session_mgr):
    assert session_mgr.get_progress() is None


def test_list_running_sessions_filters_by_state(plan, session_mgr):
    state_running = StateMachine()
    state_running.total_steps = 5
    state_running.current_step = 1
    _force_running(state_running)
    session_mgr.create_session(plan, state_running)
    session_mgr.save_state(plan, state_running)

    # Simulate a second session in STOPPED state.
    state_stopped = StateMachine()
    state_stopped.total_steps = 5
    state_stopped.current_step = 5
    _force_running(state_stopped)
    state_stopped.transition(AgentState.STOPPED)
    session_mgr.create_session(plan, state_stopped)
    session_mgr.save_state(plan, state_stopped)

    running = session_mgr.list_running_sessions()
    names = {s["id"] for s in running}
    # Only the RUNNING one should appear.
    assert len(running) == 1
    all_sessions = session_mgr.list_sessions()
    assert len(all_sessions) == 2
    assert names.issubset({s["id"] for s in all_sessions})


# ---------- MCP tools ------------------------------------------------------

def test_get_session_progress_tool_registered():
    spec = get_registry().get("bionics_get_session_progress")
    assert spec is not None
    assert spec.category == "audit"
    assert spec.annotations.read_only is True


def test_list_active_sessions_tool_registered():
    spec = get_registry().get("bionics_list_active_sessions")
    assert spec is not None
    assert spec.category == "audit"
    assert spec.annotations.read_only is True


def test_get_session_progress_tool_returns_idle_when_no_session():
    """Without any session saved, the tool returns graceful no-op."""
    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute("bionics_get_session_progress", {})
    assert result.ok
    # Either idle (nothing ever saved) or a valid progress dict.
    assert "state" in result.data


def test_list_active_sessions_tool_returns_list():
    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute("bionics_list_active_sessions", {})
    assert result.ok
    assert "active" in result.data
    assert "count" in result.data
    assert isinstance(result.data["active"], list)


# ---------- session_id traversal guards (v0.5.6) --------------------------
# load_session and delete_session must reject any session_id that escapes
# the regex `^[0-9A-Za-z_\-]{1,64}$` AND must apply a resolve()-based
# child-check so a same-named symlink cannot redirect the operation.


def test_load_session_rejects_path_traversal(session_mgr):
    """resume_from_session(session_id=...) is reachable from MCP. A malicious
    id like '../../etc/passwd' must not be able to read arbitrary .json files."""
    assert session_mgr.load_session("../../etc/passwd") is None
    assert session_mgr.load_session("..\\..\\Windows\\System32\\config\\SAM") is None
    assert session_mgr.load_session("foo/../bar") is None


def test_load_session_rejects_special_chars(session_mgr):
    for bad in ("foo bar", "foo;bar", "foo\nbar", "", "a" * 65):
        assert session_mgr.load_session(bad) is None


def test_load_session_accepts_valid_id_format(session_mgr, plan):
    state = StateMachine()
    state.total_steps = 1
    sid = session_mgr.create_session(plan, state)
    # Real ID generated by create_session should pass the guard.
    loaded = session_mgr.load_session(sid)
    assert loaded is not None
    assert loaded["session_id"] == sid


def test_delete_session_rejects_path_traversal(session_mgr, plan, tmp_path):
    """A malicious id must not delete files outside the session dir."""
    # Plant a "victim" file outside the session dir.
    victim = tmp_path / "victim.json"
    victim.write_text('{"plan":{}}')
    state = StateMachine()
    state.total_steps = 1
    session_mgr.create_session(plan, state)
    # Attempt traversal — should be rejected without unlinking the victim.
    session_mgr.delete_session("../victim")
    session_mgr.delete_session("../../etc/passwd")
    assert victim.exists()


def test_delete_session_with_valid_id_actually_deletes(session_mgr, plan):
    state = StateMachine()
    state.total_steps = 1
    sid = session_mgr.create_session(plan, state)
    session_mgr.save_state(plan, state)
    target = session_mgr._session_dir / f"{sid}.json"
    assert target.exists()
    session_mgr.delete_session(sid)
    assert not target.exists()


def test_delete_session_silent_on_invalid_id_no_exception(session_mgr):
    """Sanity: rejecting an unsafe id should be a no-op (no raise, no crash)."""
    # Should not raise.
    session_mgr.delete_session("../../etc/passwd")
    session_mgr.delete_session("")
    session_mgr.delete_session("a" * 200)
