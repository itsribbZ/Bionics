"""Tests for UE5 EventGraph (K2) tool surface.

Covers the 5 EventGraph tools shipped in v0.5.11 (2026-05-02):
- bionics_tools/ue5_eventgraph.py — 5 tools (query + 3 add + 1 wire)

Backed by C++ plugin tools at:
- plugins/BionicsBridge/Source/BionicsBridgeEditor/Private/Tools/EventGraphTools.cpp
- + 5 corresponding *.h headers

Without a live UE5 bridge we can't run end-to-end, so these tests verify:
- All 5 tools register with correct category, safety tier, aliases
- Schema generation produces the expected required-arg list
- Each Python wrapper delegates to call_bridge_tool with the right tool name + args
"""

from unittest.mock import patch

# ============================================================================
# Helpers
# ============================================================================


def _mock_tool_result(success: bool = True, data: dict | None = None):
    """Build a ToolResult mirroring what bionics_tools/ue5_native.py:_call_tool
    actually returns from a successful C++ bridge call.

    Real shape: `ToolResult.success(content=text[:500], data=json.loads(text))`
    where `text` is the C++ tool's serialized response (e.g.
    `{"name": "K2Node_X_0", "guid": "...", "compile_errors": 0}`).

    Default data shape mimics a typical add-node response so chain-call tests
    that read `result.data["name"]` work against the mock the same way they'd
    work against the live bridge. Per-test callers can pass realistic shapes
    (e.g. `{"ubergraph_pages": 1, "events": [...]}` for query_eventgraph).

    The legacy `{"ok": True}` sentinel was removed 2026-05-02 (audit fix —
    it didn't represent any real bridge response and silently masked
    chain-call data-flow divergence).
    """
    from core.bridge import ToolResult
    if not success:
        return ToolResult.failure("mocked failure")
    if data is None:
        data = {
            "name": "K2Node_MockNode_0",
            "guid": "00000000-0000-0000-0000-mocknode0001",
            "compile_errors": 0,
            "connected": True,
        }
    return ToolResult.success(content="mocked", data=data)


def _patch_eventgraph_call_tool(return_value=None):
    """Patch the call_bridge_tool symbol resolved by ue5_eventgraph.py at import."""
    return patch("bionics_tools.ue5_eventgraph._call_tool",
                 return_value=return_value or _mock_tool_result())


# ============================================================================
# Registration metadata
# ============================================================================


class TestEventGraphRegistration:
    def test_all_five_eventgraph_tools_registered(self):
        """Ensure the 5 EventGraph tools are discoverable via the registry."""
        from bionics_tools import ue5_eventgraph  # noqa: F401 — import registers
        from core.bridge import get_registry

        names = set(get_registry().list_names())
        expected = {
            "ue5_query_eventgraph",
            "ue5_eventgraph_add_call_function",
            "ue5_eventgraph_add_variable_node",
            "ue5_eventgraph_add_event",
            "ue5_wire_eventgraph_pins",
        }
        missing = expected - names
        assert not missing, f"Missing EventGraph tools: {missing}"

    def test_query_tool_is_safe(self):
        from bionics_tools import ue5_eventgraph  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_query_eventgraph")
        assert spec is not None
        assert spec.safety_tier == SafetyTier.SAFE
        assert spec.category == "ue5_eventgraph"

    def test_mutating_tools_are_moderate(self):
        from bionics_tools import ue5_eventgraph  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        for name in (
            "ue5_eventgraph_add_call_function",
            "ue5_eventgraph_add_variable_node",
            "ue5_eventgraph_add_event",
            "ue5_wire_eventgraph_pins",
        ):
            spec = get_registry().get(name)
            assert spec is not None, name
            assert spec.safety_tier == SafetyTier.MODERATE, f"{name} should be MODERATE"
            assert spec.category == "ue5_eventgraph"


# ============================================================================
# Bridge delegation — verify each Python wrapper sends the right tool_name + args
# ============================================================================


class TestBridgeDelegation:
    def test_query_eventgraph_delegates(self):
        from bionics_tools.ue5_eventgraph import ue5_query_eventgraph

        with _patch_eventgraph_call_tool() as mock:
            ue5_query_eventgraph(
                asset_path="/Game/BP/BP_Char",
                include_hidden_pins=True,
            )
            mock.assert_called_once_with("query_eventgraph", {
                "asset_path": "/Game/BP/BP_Char",
                "include_hidden_pins": True,
            })

    def test_add_call_function_delegates_with_target_class(self):
        from bionics_tools.ue5_eventgraph import ue5_eventgraph_add_call_function

        with _patch_eventgraph_call_tool() as mock:
            ue5_eventgraph_add_call_function(
                asset_path="/Game/BP/BP_Char",
                function_name="SpawnEmitterAtLocation",
                target_class="GameplayStatics",
                pos_x=400,
                pos_y=200,
            )
            mock.assert_called_once_with("add_eventgraph_call_function", {
                "asset_path": "/Game/BP/BP_Char",
                "function_name": "SpawnEmitterAtLocation",
                "target_class": "GameplayStatics",
                "pos_x": 400,
                "pos_y": 200,
            })

    def test_add_call_function_self_target_default_empty(self):
        """target_class default is '' (means call a function on the BP's parent class)."""
        from bionics_tools.ue5_eventgraph import ue5_eventgraph_add_call_function

        with _patch_eventgraph_call_tool() as mock:
            ue5_eventgraph_add_call_function(
                asset_path="/Game/BP/BP_Char",
                function_name="PlayMontage",
            )
            args = mock.call_args[0]
            assert args[0] == "add_eventgraph_call_function"
            assert args[1]["target_class"] == ""

    def test_add_variable_get(self):
        from bionics_tools.ue5_eventgraph import ue5_eventgraph_add_variable_node

        with _patch_eventgraph_call_tool() as mock:
            ue5_eventgraph_add_variable_node(
                asset_path="/Game/BP/BP_Char",
                variable_name="Health",
                operation="get",
            )
            mock.assert_called_once_with("add_eventgraph_variable_node", {
                "asset_path": "/Game/BP/BP_Char",
                "variable_name": "Health",
                "operation": "get",
                "pos_x": 0,
                "pos_y": 0,
            })

    def test_add_variable_set(self):
        from bionics_tools.ue5_eventgraph import ue5_eventgraph_add_variable_node

        with _patch_eventgraph_call_tool() as mock:
            ue5_eventgraph_add_variable_node(
                asset_path="/Game/BP/BP_Char",
                variable_name="bIsInvulnerable",
                operation="set",
            )
            args = mock.call_args[0][1]
            assert args["operation"] == "set"

    def test_add_event_engine(self):
        from bionics_tools.ue5_eventgraph import ue5_eventgraph_add_event

        with _patch_eventgraph_call_tool() as mock:
            ue5_eventgraph_add_event(
                asset_path="/Game/BP/BP_Char",
                event_type="engine",
                event_name="ReceiveBeginPlay",
            )
            args = mock.call_args[0][1]
            assert args["event_type"] == "engine"
            assert args["event_name"] == "ReceiveBeginPlay"

    def test_add_event_custom(self):
        from bionics_tools.ue5_eventgraph import ue5_eventgraph_add_event

        with _patch_eventgraph_call_tool() as mock:
            ue5_eventgraph_add_event(
                asset_path="/Game/BP/BP_Char",
                event_type="custom",
                event_name="OnHitStop",
            )
            args = mock.call_args[0][1]
            assert args["event_type"] == "custom"
            assert args["event_name"] == "OnHitStop"

    def test_wire_pins_delegates_with_auto_compile(self):
        from bionics_tools.ue5_eventgraph import ue5_wire_eventgraph_pins

        with _patch_eventgraph_call_tool() as mock:
            ue5_wire_eventgraph_pins(
                asset_path="/Game/BP/BP_Char",
                source_node="K2Node_Event_0",
                source_pin="then",
                target_node="K2Node_CallFunction_0",
                target_pin="execute",
                auto_compile=False,
            )
            mock.assert_called_once_with("wire_eventgraph_pins", {
                "asset_path": "/Game/BP/BP_Char",
                "source_node": "K2Node_Event_0",
                "source_pin": "then",
                "target_node": "K2Node_CallFunction_0",
                "target_pin": "execute",
                "auto_compile": False,
            })

    def test_wire_pins_auto_compile_default_true(self):
        from bionics_tools.ue5_eventgraph import ue5_wire_eventgraph_pins

        with _patch_eventgraph_call_tool() as mock:
            ue5_wire_eventgraph_pins(
                asset_path="/Game/BP/BP_Char",
                source_node="A",
                source_pin="then",
                target_node="B",
                target_pin="execute",
            )
            args = mock.call_args[0][1]
            assert args["auto_compile"] is True


# ============================================================================
# Schema sanity — the JSON Schema published to MCP must list required args correctly
# ============================================================================


class TestSchemaSanity:
    def test_query_eventgraph_required_arg(self):
        from bionics_tools import ue5_eventgraph  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_query_eventgraph")
        schema = spec.input_schema
        assert "asset_path" in schema.get("required", [])

    def test_call_function_requires_function_name(self):
        from bionics_tools import ue5_eventgraph  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_eventgraph_add_call_function")
        required = set(spec.input_schema.get("required", []))
        assert {"asset_path", "function_name"}.issubset(required)

    def test_variable_node_requires_operation(self):
        from bionics_tools import ue5_eventgraph  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_eventgraph_add_variable_node")
        required = set(spec.input_schema.get("required", []))
        assert {"asset_path", "variable_name", "operation"}.issubset(required)

    def test_wire_pins_requires_all_four_endpoints(self):
        from bionics_tools import ue5_eventgraph  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_wire_eventgraph_pins")
        required = set(spec.input_schema.get("required", []))
        assert {"asset_path", "source_node", "source_pin",
                "target_node", "target_pin"}.issubset(required)
