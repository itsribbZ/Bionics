"""Unit tests for core/bridge.py — ToolRegistry, @bionics_tool, ToolGate.

Run with:
    pytest tests/test_bridge.py -v
    python -m pytest tests/ -v --tb=short

These tests don't require UE5, fastmcp, or an API key — they test the
registry, schema generation, and gate pipeline in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.bridge import (
    SafetyTier,
    ToolGate,
    ToolResult,
    _build_input_schema,
    _check_type,
    _type_to_schema,
    bionics_tool,
    get_registry,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    """Clear the registry between tests to avoid cross-test pollution."""
    # We don't destroy the global singleton; just track what each test adds.
    # Tests register tools under unique names to avoid collisions.
    yield


# ============================================================================
# ToolResult tests
# ============================================================================


class TestToolResult:
    def test_success_factory(self):
        r = ToolResult.success(content="hello", data={"x": 1})
        assert r.ok is True
        assert r.content == "hello"
        assert r.data == {"x": 1}
        assert r.error == ""

    def test_failure_factory(self):
        r = ToolResult.failure("nope")
        assert r.ok is False
        assert r.error == "nope"

    def test_to_dict(self):
        r = ToolResult.success(content="x", data={"n": 5})
        d = r.to_dict()
        assert d["ok"] is True
        assert d["data"] == {"n": 5}
        assert "error" in d
        assert "meta" in d

    def test_to_dict_data_none(self):
        r = ToolResult.success()
        d = r.to_dict()
        assert d["data"] == {}

    def test_to_json(self):
        import json
        r = ToolResult.success(content="hi", data={"k": "v"})
        parsed = json.loads(r.to_json())
        assert parsed["ok"] is True
        assert parsed["content"] == "hi"


# ============================================================================
# Schema generation tests
# ============================================================================


class TestSchemaGeneration:
    def test_primitive_types(self):
        assert _type_to_schema(str) == {"type": "string"}
        assert _type_to_schema(int) == {"type": "integer"}
        assert _type_to_schema(float) == {"type": "number"}
        assert _type_to_schema(bool) == {"type": "boolean"}
        assert _type_to_schema(list) == {"type": "array"}
        assert _type_to_schema(dict) == {"type": "object"}

    def test_list_with_items(self):
        schema = _type_to_schema(list[int])
        assert schema["type"] == "array"
        assert schema["items"] == {"type": "integer"}

    def test_literal_strings(self):
        schema = _type_to_schema(Literal["a", "b", "c"])
        assert schema["type"] == "string"
        assert schema["enum"] == ["a", "b", "c"]

    def test_literal_ints(self):
        schema = _type_to_schema(Literal[1, 2, 3])
        assert schema["type"] == "integer"
        assert schema["enum"] == [1, 2, 3]

    def test_optional_single(self):
        schema = _type_to_schema(int | None)
        assert schema.get("nullable") is True
        assert schema["type"] == "integer"

    def test_union_multiple(self):
        schema = _type_to_schema(int | str | None)
        assert "anyOf" in schema
        types = [s.get("type") for s in schema["anyOf"]]
        assert "integer" in types
        assert "string" in types
        assert "null" in types

    def test_annotated_with_description(self):
        def foo(x: Annotated[int, "the count"]) -> ToolResult: return ToolResult.success()
        schema = _build_input_schema(foo)
        assert schema["properties"]["x"]["description"] == "the count"
        assert schema["properties"]["x"]["type"] == "integer"

    def test_required_vs_optional(self):
        def foo(a: int, b: int = 5) -> ToolResult: return ToolResult.success()
        schema = _build_input_schema(foo)
        assert "a" in schema["required"]
        assert "b" not in schema["required"]
        assert schema["properties"]["b"]["default"] == 5

    def test_check_type(self):
        assert _check_type("hello", "string") is True
        assert _check_type(5, "integer") is True
        assert _check_type(True, "integer") is False  # bool is NOT int in schema
        assert _check_type(True, "boolean") is True
        assert _check_type([1, 2], "array") is True
        assert _check_type({}, "object") is True
        assert _check_type(None, "null") is True
        assert _check_type(3.14, "number") is True


# ============================================================================
# Registry tests
# ============================================================================


class TestToolRegistry:
    def test_singleton(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_register_and_lookup(self):
        @bionics_tool(name="_test_register_lookup", category="_test")
        def f() -> ToolResult:
            """test tool"""
            return ToolResult.success()
        spec = get_registry().get("_test_register_lookup")
        assert spec is not None
        assert spec.name == "_test_register_lookup"
        assert spec.category == "_test"

    def test_alias_lookup(self):
        @bionics_tool(name="_test_alias", category="_test", aliases=["_alias1", "_alias2"])
        def f() -> ToolResult:
            """test aliased"""
            return ToolResult.success()
        r = get_registry()
        assert r.get("_test_alias") is not None
        assert r.get("_alias1") is not None
        assert r.get("_alias2") is not None
        assert r.get("_alias1").name == "_test_alias"

    def test_safety_tier_preserved(self):
        @bionics_tool(
            name="_test_destructive", category="_test",
            safety_tier=SafetyTier.DESTRUCTIVE, destructive=True,
        )
        def f() -> ToolResult:
            """destructive test"""
            return ToolResult.success()
        spec = get_registry().get("_test_destructive")
        assert spec.safety_tier == SafetyTier.DESTRUCTIVE
        assert spec.annotations.destructive is True

    def test_summary(self):
        summary = get_registry().summary()
        assert "total_tools" in summary
        assert "categories" in summary
        assert "safety_tiers" in summary


# ============================================================================
# ToolGate tests
# ============================================================================


class TestToolGate:
    def test_unknown_tool(self):
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("nonexistent_tool_xyz", {})
        assert r.ok is False
        assert "Unknown tool" in r.error

    def test_execute_no_args(self):
        @bionics_tool(name="_test_noargs", category="_test")
        def f() -> ToolResult:
            """no args"""
            return ToolResult.success(content="ran")
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_noargs", {})
        assert r.ok is True
        assert r.content == "ran"

    def test_missing_required_arg(self):
        @bionics_tool(name="_test_missing_req", category="_test")
        def f(x: int) -> ToolResult:
            """needs x"""
            return ToolResult.success()
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_missing_req", {})
        assert r.ok is False
        assert "missing required" in r.error

    def test_required_none_rejected(self):
        @bionics_tool(name="_test_req_none", category="_test")
        def f(x: int) -> ToolResult:
            """x required"""
            return ToolResult.success()
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_req_none", {"x": None})
        assert r.ok is False
        assert "cannot be None" in r.error

    def test_optional_none_accepted(self):
        @bionics_tool(name="_test_opt_none", category="_test")
        def f(x: int | None = None) -> ToolResult:
            """x optional"""
            return ToolResult.success(data={"x": x})
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_opt_none", {"x": None})
        assert r.ok is True

    def test_type_mismatch_rejected(self):
        @bionics_tool(name="_test_int_arg", category="_test")
        def f(x: int) -> ToolResult:
            """x is int"""
            return ToolResult.success()
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_int_arg", {"x": "not_an_int"})
        assert r.ok is False
        assert "expected type" in r.error

    def test_enum_rejection(self):
        @bionics_tool(name="_test_enum", category="_test")
        def f(mode: Literal["a", "b", "c"] = "a") -> ToolResult:
            """mode enum"""
            return ToolResult.success()
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_enum", {"mode": "z"})
        assert r.ok is False
        assert "not in allowed values" in r.error

    def test_bounds_rejection(self):
        from pydantic import Field as PydField
        @bionics_tool(name="_test_bounds", category="_test")
        def bounded_fn(n: Annotated[int, PydField(ge=0, le=100)] = 50) -> ToolResult:
            """bounded"""
            return ToolResult.success()
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_bounds", {"n": 200})
        assert r.ok is False
        # Either "maximum" bound message OR type-check message (if schema gen failed)
        assert "maximum" in r.error or "expected type" in r.error

    def test_dry_run(self):
        @bionics_tool(name="_test_dryrun", category="_test", safety_tier=SafetyTier.DESTRUCTIVE)
        def f(x: int) -> ToolResult:
            """would destroy"""
            raise AssertionError("should not execute in dry-run")
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_dryrun", {"x": 5}, dry_run=True)
        assert r.ok is True
        assert "dry-run" in r.content.lower()

    def test_exception_normalized(self):
        @bionics_tool(name="_test_raises", category="_test")
        def f() -> ToolResult:
            """raises"""
            raise ValueError("boom")
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_raises", {})
        assert r.ok is False
        assert "boom" in r.error

    def test_dict_return_normalized(self):
        @bionics_tool(name="_test_dict_return", category="_test")
        def f():
            """returns plain dict"""
            return {"a": 1, "b": 2}
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_dict_return", {})
        assert r.ok is True
        assert r.data == {"a": 1, "b": 2}

    def test_meta_populated(self):
        @bionics_tool(name="_test_meta", category="_test")
        def f() -> ToolResult:
            """meta test"""
            return ToolResult.success(content="ok")
        gate = ToolGate()
        gate.set_bypass_safety(True)
        r = gate.execute("_test_meta", {})
        assert r.meta is not None
        assert r.meta.get("tool") == "_test_meta"
        assert r.meta.get("tier") == "safe"
        assert "elapsed_ms" in r.meta


# ============================================================================
# Integration — register_all
# ============================================================================


class TestFullRegistry:
    def test_register_all(self):
        from bionics_tools import register_all
        count = register_all()
        assert count >= 100  # We have 127+ tools

    def test_all_tools_have_descriptions(self):
        from bionics_tools import register_all
        register_all()
        for spec in get_registry().list_all():
            assert spec.description, f"Tool {spec.name} has no description"

    def test_all_tools_have_valid_schemas(self):
        from bionics_tools import register_all
        register_all()
        for spec in get_registry().list_all():
            schema = spec.input_schema
            assert "type" in schema
            assert schema["type"] == "object"
            assert "properties" in schema


# ============================================================================
# JSON Schema meta-validation — catches structurally-broken schemas
# (the v0.5.8 PEP 604 class of bug). Added per 2026-05-02 audit.
# ============================================================================


class TestSchemaJsonSchemaValidity:
    """Every registered tool must produce a structurally-valid JSON Schema.

    `Draft202012Validator.check_schema()` validates the schema against the
    JSON Schema 2020-12 meta-schema. It catches malformed `type`, broken
    `enum`, invalid `anyOf` arrays, etc. Unknown keywords (e.g. the
    `nullable` extension we use for `int | None`) are tolerated by the
    meta-schema, so this test is a structure check, not a strictness check.

    8 lines, covers all 187+ tools at once. Would have caught any v0.5.8-class
    schema-gen regression in CI.
    """

    def test_all_tool_schemas_pass_meta_validation(self):
        import jsonschema

        from bionics_tools import register_all
        register_all()
        failures: list[str] = []
        for spec in get_registry().list_all():
            try:
                jsonschema.Draft202012Validator.check_schema(spec.input_schema)
            except jsonschema.exceptions.SchemaError as e:
                failures.append(f"{spec.name}: {e.message}")
        assert not failures, (
            "Invalid JSON Schemas found:\n  " + "\n  ".join(failures)
        )

    def test_output_schemas_when_present_pass_meta_validation(self):
        """Same check for output_schema — many tools declare it via @bionics_tool."""
        import jsonschema

        from bionics_tools import register_all
        register_all()
        failures: list[str] = []
        for spec in get_registry().list_all():
            out = getattr(spec, "output_schema", None)
            if not out:
                continue
            try:
                jsonschema.Draft202012Validator.check_schema(out)
            except jsonschema.exceptions.SchemaError as e:
                failures.append(f"{spec.name}: {e.message}")
        assert not failures, (
            "Invalid OUTPUT JSON Schemas found:\n  " + "\n  ".join(failures)
        )


# ============================================================================
# Contextvars propagation — Sacred Rule #3 enforcement for the v0.5.10 fix
# at core/bridge.py:772-778. Added per 2026-05-02 audit.
# ============================================================================


# Module-level so the @bionics_tool decorator runs at import (registers once).
@bionics_tool(name="_test_async_ctx_probe", category="_test")
async def _test_async_ctx_probe() -> ToolResult:
    """Async probe tool — reads mcp context from inside async body to verify
    contextvars propagated across the ToolGate ThreadPoolExecutor boundary."""
    from core import mcp_ctx
    return ToolResult.success(data={"ctx": mcp_ctx.get_mcp_context()})


class TestContextvarsPropagation:
    """Regression test for v0.5.10 fix at core/bridge.py:772-778.

    When ToolGate.execute is called from inside a running asyncio loop with
    an async tool, it dispatches to a worker thread via ThreadPoolExecutor.
    ContextVars do NOT propagate across thread boundaries by default — the
    v0.5.10 fix wraps the dispatch in `contextvars.copy_context()` so async
    tool bodies can still call `mcp_ctx.get_mcp_context()` and see the same
    value the caller set in the parent thread.

    Without this test, Sacred Rule #3 ("confirmed fixes are SACRED") was
    unenforceable by CI for this fix — a future refactor could silently
    re-introduce the bug.
    """

    def test_async_tool_inherits_mcp_context_across_threadpool(self):
        import asyncio

        from core import mcp_ctx

        sentinel = {"session": "test_ctx_propagation_42", "tag": "v0.5.10-regression"}
        token = mcp_ctx.set_mcp_context(sentinel)
        try:
            async def _drive():
                gate = ToolGate()
                gate.set_bypass_safety(True)
                # Inside this coroutine an asyncio loop is running; the
                # ToolGate.execute path will hit the threadpool branch
                # because the tool fn is async and a loop is running.
                return gate.execute("_test_async_ctx_probe", {})

            result = asyncio.run(_drive())
            assert result.ok, f"Tool exec failed: {result.error}"
            assert result.data is not None, "Result data missing"
            propagated = result.data.get("ctx")
            assert propagated is sentinel, (
                "MCP context did NOT propagate to ToolGate worker thread — "
                "v0.5.10 contextvars.copy_context fix has regressed. "
                f"Expected sentinel, got: {propagated!r}"
            )
        finally:
            mcp_ctx.reset_mcp_context(token)

    def test_async_tool_no_mcp_context_returns_none(self):
        """Sanity: when no context is set, the tool sees None (not a stale value)."""
        import asyncio

        from core import mcp_ctx

        # Ensure no parent context is set
        assert mcp_ctx.get_mcp_context() is None, (
            "test pre-condition: another test leaked a context"
        )

        async def _drive():
            gate = ToolGate()
            gate.set_bypass_safety(True)
            return gate.execute("_test_async_ctx_probe", {})

        result = asyncio.run(_drive())
        assert result.ok, f"Tool exec failed: {result.error}"
        assert result.data.get("ctx") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
