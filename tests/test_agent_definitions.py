"""Tests for `core/agent_definitions.py` — sub-agent fan-out.

Uses a mock Anthropic client so tests don't require network / API key.
Exercises both the pure-text path and the tool-use loop, plus parallel
dispatch.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bionics_tools import register_all
from core.agent_definitions import (
    AgentDefinition,
    dispatch_parallel_sync,
    run_agent,
)
from core.bridge import SafetyTier, ToolResult, bionics_tool

register_all()


# ---------- Test tools -----------------------------------------------------

@bionics_tool(
    name="_subag_echo",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
)
def _subag_echo(text: str = "hi") -> ToolResult:
    return ToolResult.success(content=f"echo:{text}", data={"text": text})


# ---------- Mock helpers --------------------------------------------------

def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, input_data: dict, block_id: str = "block_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input_data, id=block_id)


def _make_response(content, stop_reason="end_turn", input_tokens=100, output_tokens=50):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


@pytest.fixture
def mock_client():
    """Patch `get_shared_client` to return a MagicMock client. Yields the mock."""
    with patch("core.agent_definitions.get_shared_client") as getter:
        client = MagicMock()
        getter.return_value = client
        yield client


# ---------- Pure-text agent path -------------------------------------------

def test_run_agent_returns_text_on_end_turn(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("Plan: step 1, step 2")],
        stop_reason="end_turn",
    )

    d = AgentDefinition(
        name="planner",
        system_prompt="You are a planner.",
    )
    result = run_agent(d, "Make a plan")

    assert result.ok is True
    assert result.agent_name == "planner"
    assert "Plan: step 1, step 2" in result.output
    assert result.turns == 1
    assert result.tool_calls == []
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 100
    assert result.output_tokens == 50


def test_run_agent_surfaces_client_error(monkeypatch):
    # Force `get_shared_client` to raise ValueError (no API key).
    from core import agent_definitions
    monkeypatch.setattr(
        agent_definitions, "get_shared_client", lambda: (_ for _ in ()).throw(ValueError("no key")),
    )
    d = AgentDefinition(name="brittle")
    result = run_agent(d, "anything")
    assert result.ok is False
    assert "no key" in result.error


def test_run_agent_handles_api_exception(mock_client):
    mock_client.messages.create.side_effect = RuntimeError("rate limit")
    d = AgentDefinition(name="retryer")
    result = run_agent(d, "anything")
    assert result.ok is False
    assert "rate limit" in result.error
    assert "RuntimeError" in result.error


# ---------- Tool-use loop --------------------------------------------------

def test_run_agent_executes_tool_use_and_loops(mock_client):
    # Turn 1: Claude asks for _subag_echo.
    # Turn 2: Claude returns final text after seeing the tool result.
    mock_client.messages.create.side_effect = [
        _make_response(
            [_tool_use_block("_subag_echo", {"text": "hi"}, "tu_1")],
            stop_reason="tool_use",
        ),
        _make_response(
            [_text_block("Got echo:hi")],
            stop_reason="end_turn",
        ),
    ]

    d = AgentDefinition(
        name="tool_user",
        system_prompt="Use tools.",
        tools=["_subag_echo"],
    )
    result = run_agent(d, "Echo something")

    assert result.ok is True
    assert result.turns == 2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "_subag_echo"
    assert result.tool_calls[0]["ok"] is True
    assert "Got echo:hi" in result.output


def test_run_agent_surfaces_failed_tool_call(mock_client):
    # Tool that doesn't exist — ToolGate returns .failure()
    mock_client.messages.create.side_effect = [
        _make_response(
            [_tool_use_block("_nonexistent_tool", {}, "tu_1")],
            stop_reason="tool_use",
        ),
        _make_response([_text_block("recovered")], stop_reason="end_turn"),
    ]

    d = AgentDefinition(name="faulty", tools=["_subag_echo"])
    result = run_agent(d, "Do something broken")

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["ok"] is False
    # Still returns ok=True overall — failed tool calls are normal events,
    # not agent-level failures.
    assert result.ok is True


def test_max_turns_caps_runaway_tool_loop(mock_client):
    # Always return tool_use → verify we stop at max_turns.
    mock_client.messages.create.return_value = _make_response(
        [_tool_use_block("_subag_echo", {"text": "loop"}, "tu_x")],
        stop_reason="tool_use",
    )

    d = AgentDefinition(name="loopy", tools=["_subag_echo"], max_turns=3)
    result = run_agent(d, "Go forever")

    assert result.turns == 3
    assert "max_turns" in result.error


# ---------- Parallel fan-out -----------------------------------------------

def test_dispatch_parallel_broadcasts_single_prompt(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("done")],
        stop_reason="end_turn",
    )

    defs = [AgentDefinition(name=f"agent_{i}") for i in range(3)]
    results = dispatch_parallel_sync(defs, "Same prompt for all")

    assert len(results) == 3
    assert all(r.ok for r in results)
    assert [r.agent_name for r in results] == ["agent_0", "agent_1", "agent_2"]


def test_dispatch_parallel_pairs_prompts(mock_client):
    counter = {"i": 0}
    outputs = ["alpha", "beta", "gamma"]

    def _create(**kwargs):
        i = counter["i"]
        counter["i"] += 1
        return _make_response([_text_block(outputs[i])], stop_reason="end_turn")

    mock_client.messages.create.side_effect = _create

    defs = [AgentDefinition(name=f"a{i}") for i in range(3)]
    results = dispatch_parallel_sync(defs, ["p1", "p2", "p3"])

    assert [r.output for r in results] == outputs


def test_dispatch_parallel_empty_returns_empty(mock_client):
    assert dispatch_parallel_sync([], []) == []


def test_dispatch_parallel_mismatched_lengths_raises(mock_client):
    with pytest.raises(ValueError, match="length"):
        dispatch_parallel_sync(
            [AgentDefinition(name="a")],
            ["p1", "p2"],
        )


# ---------- Tool-schema selection -----------------------------------------

def test_tools_none_yields_no_tools(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="no_tools", tools=None)
    run_agent(d, "hi")
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "tools" not in call_kwargs


def test_tools_star_yields_all_tools(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="all_tools", tools=["*"])
    run_agent(d, "hi")
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "tools" in call_kwargs
    # More than a handful of registered tools in the global registry.
    assert len(call_kwargs["tools"]) > 20


def test_named_tool_subset_passed_to_claude(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="subset", tools=["_subag_echo"])
    run_agent(d, "hi")
    call_kwargs = mock_client.messages.create.call_args[1]
    tool_names = [t["name"] for t in call_kwargs["tools"]]
    assert tool_names == ["_subag_echo"]


# ---------- Tool-choice forcing --------------------------------------------

def test_tool_choice_none_means_no_directive(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="no_choice", tools=["_subag_echo"], tool_choice=None)
    run_agent(d, "hi")
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "tool_choice" not in call_kwargs


def test_tool_choice_any_shortcut_expands(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="forced", tools=["_subag_echo"], tool_choice="any")
    run_agent(d, "hi")
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["tool_choice"] == {"type": "any"}


def test_tool_choice_required_alias(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="req", tools=["_subag_echo"], tool_choice="required")
    run_agent(d, "hi")
    assert mock_client.messages.create.call_args[1]["tool_choice"] == {"type": "any"}


def test_tool_choice_dict_passes_through(mock_client):
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(
        name="pin",
        tools=["_subag_echo"],
        tool_choice={"type": "tool", "name": "_subag_echo"},
    )
    run_agent(d, "hi")
    assert mock_client.messages.create.call_args[1]["tool_choice"] == {
        "type": "tool", "name": "_subag_echo",
    }


def test_tool_choice_ignored_when_no_tools(mock_client):
    """Without a tool list there is no tool-use path — tool_choice would error."""
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="no_tools_but_choice", tools=None, tool_choice="any")
    run_agent(d, "hi")
    call_kwargs = mock_client.messages.create.call_args[1]
    assert "tool_choice" not in call_kwargs


def test_tool_choice_unknown_shortcut_raises():
    """Malformed shortcut should fail fast, not silently send a bad request."""
    from core.agent_definitions import _normalize_tool_choice
    with pytest.raises(ValueError, match="tool_choice shortcut"):
        _normalize_tool_choice("bogus")


# ---------- Sub-agent DESTRUCTIVE gate (v0.5.7) ---------------------------
# Mirrors mcp_server.py + TaskManager DESTRUCTIVE gate. When an AgentDefinition
# explicitly enumerates a DESTRUCTIVE-tier tool, run_agent must refuse unless
# BIONICS_MCP_ALLOW_DESTRUCTIVE is truthy. Closes the v0.5.6 bypass where a
# fan-out worker could execute any DESTRUCTIVE tool because gate.bypass_safety
# was hardcoded True regardless of env.


@bionics_tool(
    name="_subag_destructive_marker",
    category="test",
    safety_tier=SafetyTier.DESTRUCTIVE,
    read_only=False,
)
def _subag_destructive_marker() -> ToolResult:
    return ToolResult.success(content="should-not-run", data={})


def test_subagent_destructive_blocked_without_env(monkeypatch, mock_client):
    monkeypatch.delenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", raising=False)
    d = AgentDefinition(name="naughty", tools=["_subag_destructive_marker"])
    result = run_agent(d, "do the thing")
    assert result.ok is False
    assert "DESTRUCTIVE" in result.error
    assert "BIONICS_MCP_ALLOW_DESTRUCTIVE" in result.error
    # The Anthropic client must NOT have been called — fail fast before any tokens spend.
    assert mock_client.messages.create.call_count == 0


def test_subagent_destructive_allowed_when_env_set(monkeypatch, mock_client):
    monkeypatch.setenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", "true")
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="trusted", tools=["_subag_destructive_marker"])
    result = run_agent(d, "do the thing")
    assert result.ok is True
    assert mock_client.messages.create.call_count == 1


def test_subagent_safe_only_no_gate_check(monkeypatch, mock_client):
    """SAFE-tier tools should always run, regardless of the env var — the gate
    only fires when DESTRUCTIVE-tier tools are explicitly listed."""
    monkeypatch.delenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", raising=False)
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="safe", tools=["_subag_echo"])
    result = run_agent(d, "echo")
    assert result.ok is True


def test_subagent_tools_none_skips_gate_check(monkeypatch, mock_client):
    """definition.tools=None means 'all tools allowed' — explicit trust contract.
    Pre-check is skipped and run_agent proceeds normally."""
    monkeypatch.delenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", raising=False)
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="all_tools", tools=None)
    result = run_agent(d, "anything")
    assert result.ok is True


def test_subagent_destructive_naming_includes_offending_tool(monkeypatch, mock_client):
    """Error message must name the rejected tool so the operator knows what to whitelist."""
    monkeypatch.delenv("BIONICS_MCP_ALLOW_DESTRUCTIVE", raising=False)
    d = AgentDefinition(name="x", tools=["_subag_echo", "_subag_destructive_marker"])
    result = run_agent(d, "hi")
    assert result.ok is False
    assert "_subag_destructive_marker" in result.error
    # SAFE tool should NOT appear in the rejection list.
    assert "_subag_echo" not in result.error


# ---------- v0.5.8: dispatch_parallel_sync inside running loop ------------
# Pre-v0.5.8, calling dispatch_parallel_sync from inside FastMCP's async tool
# handler crashed with `RuntimeError: This event loop is already running` —
# the primary MCP production call path. v0.5.8 detects the running loop and
# delegates to a worker thread instead.


def test_dispatch_parallel_sync_works_inside_running_loop(mock_client):
    """Validate the v0.5.8 fix — sync wrapper must NOT raise inside an active loop."""
    import asyncio
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="loop_safe", tools=["_subag_echo"])

    async def caller_inside_running_loop():
        # Production scenario: an async MCP tool handler invokes the sync wrapper.
        # Pre-fix this raised RuntimeError. Post-fix it returns the agent results
        # from a worker-thread event loop.
        return dispatch_parallel_sync([d], ["hi"])

    results = asyncio.run(caller_inside_running_loop())
    assert len(results) == 1
    assert results[0].ok is True


def test_dispatch_parallel_sync_works_outside_loop(mock_client):
    """Sanity: the no-running-loop path (legacy behavior) still works."""
    mock_client.messages.create.return_value = _make_response(
        [_text_block("ok")], stop_reason="end_turn",
    )
    d = AgentDefinition(name="plain", tools=["_subag_echo"])
    results = dispatch_parallel_sync([d], ["hi"])
    assert len(results) == 1
    assert results[0].ok is True
