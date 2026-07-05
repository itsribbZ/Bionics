"""Tests for the ToolGate lifecycle hook system.

Covers PreToolUse, PostToolUse, and Stop hooks wired through core/bridge.py.
Tests are isolated via clear_all_hooks() in fixtures.
"""
from __future__ import annotations

import pytest

from core.bridge import (
    SafetyTier,
    ToolGate,
    ToolResult,
    bionics_tool,
    clear_all_hooks,
    fire_stop_hooks,
    register_post_tool_use,
    register_pre_tool_use,
    register_stop_hook,
)


@pytest.fixture(autouse=True)
def _isolate_hooks():
    """Clear hooks before and after each test so state doesn't leak between tests."""
    clear_all_hooks()
    yield
    clear_all_hooks()


@pytest.fixture
def gate():
    """Fresh ToolGate with safety bypassed for test simplicity."""
    g = ToolGate()
    g.set_bypass_safety(True)
    return g


# Register a few simple tools once for test use. The registry is a process-wide
# singleton so these persist across tests; safe because their names are unique.
@bionics_tool(
    name="_hooktest_echo",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Hook Test Echo",
)
def _hooktest_echo(value: str = "default") -> ToolResult:
    return ToolResult.success(content=f"echoed:{value}", data={"value": value})


@bionics_tool(
    name="_hooktest_destructive",
    category="test",
    safety_tier=SafetyTier.SAFE,  # Keep SAFE so safety gate doesn't interfere
    destructive=False,
    title="Hook Test Destructive-shaped",
)
def _hooktest_destructive(target: str = "x") -> ToolResult:
    return ToolResult.success(content=f"wiped:{target}", data={"target": target})


class TestPreToolUseHooks:
    def test_pre_hook_fires_before_execution(self, gate):
        seen = []

        def hook(name, args, ctx):
            seen.append((name, dict(args), ctx.get("category")))
            return None

        register_pre_tool_use(hook)
        result = gate.execute("_hooktest_echo", {"value": "hello"})
        assert result.ok
        assert seen == [("_hooktest_echo", {"value": "hello"}, "test")]

    def test_pre_hook_can_block(self, gate):
        def block_hook(name, args, ctx):
            return {"block": True, "reason": "tripwire"}

        register_pre_tool_use(block_hook)
        result = gate.execute("_hooktest_echo", {"value": "anything"})
        assert not result.ok
        assert "tripwire" in result.error
        assert result.meta.get("blocked_by_hook") is True

    def test_pre_hook_can_mutate_arguments(self, gate):
        def mutate_hook(name, args, ctx):
            new_args = dict(args)
            new_args["value"] = "MUTATED"
            return {"arguments": new_args}

        register_pre_tool_use(mutate_hook)
        result = gate.execute("_hooktest_echo", {"value": "original"})
        assert result.ok
        assert result.data["value"] == "MUTATED"

    def test_pre_hooks_chain(self, gate):
        """Multiple hooks run in registration order and mutations compose."""
        def h1(name, args, ctx):
            return {"arguments": {**args, "value": args["value"] + "_h1"}}

        def h2(name, args, ctx):
            return {"arguments": {**args, "value": args["value"] + "_h2"}}

        register_pre_tool_use(h1)
        register_pre_tool_use(h2)
        result = gate.execute("_hooktest_echo", {"value": "start"})
        assert result.data["value"] == "start_h1_h2"

    def test_pre_hook_exception_is_non_fatal(self, gate):
        def bad_hook(name, args, ctx):
            raise RuntimeError("oops")

        def good_hook(name, args, ctx):
            return {"arguments": {**args, "value": "from_good"}}

        register_pre_tool_use(bad_hook)
        register_pre_tool_use(good_hook)
        result = gate.execute("_hooktest_echo", {"value": "original"})
        assert result.ok  # Bad hook skipped, good hook still applied.
        assert result.data["value"] == "from_good"


class TestPostToolUseHooks:
    def test_post_hook_fires_after_execution(self, gate):
        seen = []

        def hook(name, args, result, ctx):
            seen.append((name, dict(args), result.ok, ctx.get("elapsed_ms") is not None))
            return None

        register_post_tool_use(hook)
        gate.execute("_hooktest_echo", {"value": "x"})
        assert len(seen) == 1
        assert seen[0][0] == "_hooktest_echo"
        assert seen[0][2] is True  # result.ok
        assert seen[0][3] is True  # elapsed_ms present in context

    def test_post_hook_can_override_result(self, gate):
        def override(name, args, result, ctx):
            return {"result": ToolResult.failure("overridden by hook")}

        register_post_tool_use(override)
        result = gate.execute("_hooktest_echo", {"value": "x"})
        assert not result.ok
        assert result.error == "overridden by hook"

    def test_post_hook_exception_is_non_fatal(self, gate):
        def bad_hook(name, args, result, ctx):
            raise ValueError("boom")

        register_post_tool_use(bad_hook)
        result = gate.execute("_hooktest_echo", {"value": "hi"})
        assert result.ok  # Original result flows through despite hook failure.
        assert result.data["value"] == "hi"


class TestStopHooks:
    def test_stop_hook_fires_with_reason(self):
        seen = []

        def hook(reason, ctx):
            seen.append((reason, dict(ctx)))

        register_stop_hook(hook)
        fire_stop_hooks("unit_test", {"session_id": "abc123"})
        assert seen == [("unit_test", {"session_id": "abc123"})]

    def test_stop_hook_exception_is_non_fatal(self):
        def bad(reason, ctx):
            raise RuntimeError("nope")

        good_seen = []

        def good(reason, ctx):
            good_seen.append(reason)

        register_stop_hook(bad)
        register_stop_hook(good)
        fire_stop_hooks("unit_test")
        assert good_seen == ["unit_test"]


class TestHookIsolation:
    def test_clear_all_hooks(self, gate):
        fired = []
        register_pre_tool_use(lambda *a: fired.append("pre") or None)
        register_post_tool_use(lambda *a: fired.append("post") or None)
        gate.execute("_hooktest_echo", {"value": "x"})
        assert fired == ["pre", "post"]
        fired.clear()
        clear_all_hooks()
        gate.execute("_hooktest_echo", {"value": "x"})
        assert fired == []
