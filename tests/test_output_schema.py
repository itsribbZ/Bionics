"""Tests for the MCP outputSchema wiring.

Verifies:
  - @bionics_tool accepts `output_schema=` kwarg
  - ToolSpec.output_schema carries the schema
  - ToolSpec.to_dict() includes `output_schema` when present (and omits it
    for tools that don't opt in)
  - The 15 shipped query tools all declare an `output_schema`
"""
from __future__ import annotations

import pytest

from bionics_tools import register_all
from core.bridge import (
    SafetyTier,
    ToolResult,
    bionics_tool,
    get_registry,
)

# Ensure every tool is registered before introspection tests run.
register_all()


@bionics_tool(
    name="_outsch_ok",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    output_schema={
        "type": "object",
        "properties": {"echo": {"type": "string"}},
        "required": ["echo"],
    },
)
def _outsch_ok(value: str = "hi") -> ToolResult:
    return ToolResult.success(content="ok", data={"echo": value})


@bionics_tool(
    name="_outsch_none",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
)
def _outsch_none() -> ToolResult:
    return ToolResult.success(content="ok")


def test_output_schema_attached_on_spec():
    spec = get_registry().get("_outsch_ok")
    assert spec is not None
    assert spec.output_schema == {
        "type": "object",
        "properties": {"echo": {"type": "string"}},
        "required": ["echo"],
    }


def test_no_output_schema_is_none():
    spec = get_registry().get("_outsch_none")
    assert spec is not None
    assert spec.output_schema is None


def test_to_dict_includes_output_schema_only_when_set():
    ok_spec = get_registry().get("_outsch_ok")
    none_spec = get_registry().get("_outsch_none")
    assert "output_schema" in ok_spec.to_dict()
    assert "output_schema" not in none_spec.to_dict()


# -------------------- Shipped coverage --------------------

# The 15 tools that MUST declare an output_schema per the 2026-04-23 rollout.
SHIPPED_OUTPUT_SCHEMA_TOOLS = [
    "version",
    "list_tools",
    "describe_tool",
    "list_categories",
    "list_plans",
    "get_config",
    "get_mouse_pos",
    "get_screen_size",
    "list_monitors",
    "list_windows",
    "system_info",
    "list_processes",
    "ue5_project_info",
    "ue5_get_selected",
    "ue5_asset_info",
]


@pytest.mark.parametrize("tool_name", SHIPPED_OUTPUT_SCHEMA_TOOLS)
def test_shipped_tool_has_output_schema(tool_name):
    spec = get_registry().get(tool_name)
    assert spec is not None, f"{tool_name} not registered"
    assert spec.output_schema is not None, f"{tool_name} missing output_schema"
    assert spec.output_schema.get("type") == "object"


def test_at_least_fifteen_tools_declare_output_schema():
    """Guard against future regression if a tool drops its schema silently."""
    declared = [s for s in get_registry().list_all() if s.output_schema is not None]
    names = {s.name for s in declared}
    missing = [n for n in SHIPPED_OUTPUT_SCHEMA_TOOLS if n not in names]
    assert not missing, f"Expected tools missing output_schema: {missing}"
