"""Unit tests for system, capture, vision, and plans tools.

Tests don't require UE5, just desktop system access.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bionics_tools import register_all

register_all()
from core.bridge import ToolGate

gate = ToolGate()
gate.set_bypass_safety(True)


class TestInputTools:
    def test_get_screen_size(self):
        r = gate.execute("get_screen_size", {})
        assert r.ok is True
        assert r.data["width"] > 0
        assert r.data["height"] > 0

    def test_get_mouse_pos(self):
        r = gate.execute("get_mouse_pos", {})
        assert r.ok is True
        assert "x" in r.data
        assert "y" in r.data

    def test_click_invalid_clicks(self):
        # clicks > 3 should clamp, not fail
        r = gate.execute("click", {"x": 10, "y": 10, "clicks": 99})
        # It should still succeed because we clamp to 3
        assert r.ok is True

    def test_click_negative_coords_clamped(self):
        r = gate.execute("click", {"x": -50, "y": -50})
        # Should clamp to 1,1 (screen edge) and succeed
        assert r.ok is True


class TestCaptureTools:
    def test_list_monitors(self):
        r = gate.execute("list_monitors", {})
        assert r.ok is True
        assert r.data["count"] > 0

    def test_capture_invalid_monitor(self):
        r = gate.execute("capture_screen", {"monitor": 99})
        assert r.ok is False
        assert "Invalid" in r.error or "invalid" in r.error

    def test_capture_primary(self):
        r = gate.execute("capture_screen", {"monitor": 0})
        assert r.ok is True
        assert r.data["width"] > 0
        assert r.data["height"] > 0


class TestMetaTools:
    def test_version(self):
        r = gate.execute("version", {})
        assert r.ok is True
        assert "Bionics" in r.content

    def test_list_tools(self):
        r = gate.execute("list_tools", {})
        assert r.ok is True
        assert r.data["count"] >= 100

    def test_list_tools_filtered(self):
        r = gate.execute("list_tools", {"category": "ue5_actor"})
        assert r.ok is True
        assert r.data["count"] > 0
        for t in r.data["tools"]:
            assert t["category"] == "ue5_actor"

    def test_describe_tool(self):
        r = gate.execute("describe_tool", {"name": "click"})
        assert r.ok is True
        assert r.data["name"] == "click"
        assert "input_schema" in r.data

    def test_describe_unknown(self):
        r = gate.execute("describe_tool", {"name": "xyznonexistent"})
        assert r.ok is False

    def test_list_categories(self):
        r = gate.execute("list_categories", {})
        assert r.ok is True
        assert r.data["total_tools"] >= 100


class TestPlansTools:
    def test_list_plans(self):
        r = gate.execute("list_plans", {})
        assert r.ok is True
        assert "plans" in r.data

    def test_save_plan_with_traversal(self):
        r = gate.execute("save_plan", {
            "name": "../../../evil",
            "steps": [],
        })
        assert r.ok is False
        assert "traversal" in r.error.lower() or "alphanumeric" in r.error.lower()

    def test_save_plan_valid(self):
        r = gate.execute("save_plan", {
            "name": "pytest_save_plan",
            "steps": [{"action": "version"}],
        })
        assert r.ok is True

    def test_load_plan_after_save(self):
        gate.execute("save_plan", {
            "name": "pytest_load_plan",
            "steps": [{"action": "version"}],
        })
        r = gate.execute("load_plan", {"name": "pytest_load_plan"})
        assert r.ok is True
        assert len(r.data["plan"]["steps"]) == 1

    def test_execute_plan_safe_step(self):
        gate.execute("save_plan", {
            "name": "pytest_exec_plan",
            "steps": [{"action": "version"}, {"action": "list_categories"}],
        })
        r = gate.execute("execute_plan", {"name": "pytest_exec_plan"})
        assert r.ok is True
        assert r.data["completed"] == 2


class TestClipboard:
    def test_clipboard_set_get(self):
        test_text = "bionics_test_clipboard_xyz_123"
        r_set = gate.execute("clipboard_set", {"text": test_text})
        # Clipboard may not be available in CI — that's OK
        if r_set.ok:
            r_get = gate.execute("clipboard_get", {})
            if r_get.ok:
                assert test_text in r_get.data.get("text", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
