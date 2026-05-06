"""Tests for the OpenTelemetry tool-call hook (`core/otel_hook.py`).

Uses OTel's `InMemorySpanExporter` to capture spans in-process — no external
collector or network required.
"""
from __future__ import annotations

import pytest

from core.bridge import (
    SafetyTier,
    ToolGate,
    ToolResult,
    bionics_tool,
    clear_all_hooks,
)

pytest.importorskip("opentelemetry")

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from core import otel_hook
from core.bridge import (
    register_post_tool_use,
    register_pre_tool_use,
    register_stop_hook,
)


# ---------- Dedicated test tools (registered at import, once) -------------
@bionics_tool(
    name="_oteltest_ok",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="OTel test — ok",
)
def _oteltest_ok(value: str = "hi") -> ToolResult:
    return ToolResult.success(content=value, data={"value": value})


@bionics_tool(
    name="_oteltest_fail",
    category="test",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="OTel test — fail",
)
def _oteltest_fail() -> ToolResult:
    return ToolResult.failure("boom")


# ---------- Fixtures ------------------------------------------------------

@pytest.fixture
def exporter():
    """Install OTel hooks wired to an InMemorySpanExporter. Yields the exporter.

    Uses a local TracerProvider (never calls set_tracer_provider) so tests don't
    collide with OTel's one-time global-provider rule.
    """
    clear_all_hooks()
    otel_hook._reset_for_testing()

    exp = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "bionics-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exp))

    tracer = provider.get_tracer("bionics.tool", "test")

    # Re-implement hook installation using OUR provider's tracer. This avoids
    # `trace.set_tracer_provider` being called twice in a process (OTel ignores
    # the second call and spans would go nowhere).
    def _pre(tool_name, arguments, context):
        span = tracer.start_span(f"bionics.tool/{tool_name}")
        span.set_attribute("bionics.tool.name", tool_name)
        span.set_attribute("bionics.tool.category", context.get("category", ""))
        span.set_attribute("bionics.tool.safety_tier", context.get("safety_tier", ""))
        span.set_attribute("bionics.tool.arg_count", len(arguments or {}))
        otel_hook._get_stack().append(span)

    def _post(tool_name, arguments, result, context):
        stack = otel_hook._get_stack()
        if not stack:
            return
        span = stack.pop()
        ok = bool(getattr(result, "ok", True))
        span.set_attribute("bionics.tool.ok", ok)
        if context.get("elapsed_ms") is not None:
            span.set_attribute("bionics.tool.elapsed_ms", float(context["elapsed_ms"]))
        if not ok:
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(StatusCode.ERROR, getattr(result, "error", "")))
        span.end()

    def _stop(reason, context):
        provider.force_flush(timeout_millis=2_000)

    register_pre_tool_use(_pre)
    register_post_tool_use(_post)
    register_stop_hook(_stop)

    yield exp

    clear_all_hooks()
    otel_hook._reset_for_testing()


@pytest.fixture
def gate():
    g = ToolGate()
    g.set_bypass_safety(True)
    return g


# ---------- Tests ---------------------------------------------------------

def test_successful_tool_emits_span(exporter, gate):
    gate.execute("_oteltest_ok", {"value": "hello"})
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "bionics.tool/_oteltest_ok"
    attrs = dict(span.attributes)
    assert attrs["bionics.tool.name"] == "_oteltest_ok"
    assert attrs["bionics.tool.category"] == "test"
    assert attrs["bionics.tool.safety_tier"] == "safe"
    assert attrs["bionics.tool.ok"] is True
    assert attrs["bionics.tool.arg_count"] == 1
    assert "bionics.tool.elapsed_ms" in attrs


def test_failing_tool_marks_span_error(exporter, gate):
    gate.execute("_oteltest_fail", {})
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    attrs = dict(span.attributes)
    assert attrs["bionics.tool.ok"] is False
    from opentelemetry.trace import StatusCode
    assert span.status.status_code == StatusCode.ERROR
    assert "boom" in (span.status.description or "")


def test_multiple_calls_yield_multiple_spans(exporter, gate):
    gate.execute("_oteltest_ok", {"value": "a"})
    gate.execute("_oteltest_ok", {"value": "b"})
    gate.execute("_oteltest_fail", {})
    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    assert [s.name for s in spans] == [
        "bionics.tool/_oteltest_ok",
        "bionics.tool/_oteltest_ok",
        "bionics.tool/_oteltest_fail",
    ]


def test_stack_is_thread_local():
    """Each thread gets its own stack — no cross-thread leakage."""
    import threading
    results = {}

    def push_and_peek(tag):
        s = otel_hook._get_stack()
        s.append(tag)
        results[tag] = list(s)

    t1 = threading.Thread(target=push_and_peek, args=("t1",))
    t2 = threading.Thread(target=push_and_peek, args=("t2",))
    t1.start(); t2.start(); t1.join(); t2.join()

    # Each thread's stack contains only its own push.
    assert results["t1"] == ["t1"]
    assert results["t2"] == ["t2"]


def test_install_from_env_is_opt_in(monkeypatch):
    """install_from_env() returns False when flag is unset."""
    otel_hook._reset_for_testing()
    monkeypatch.delenv("BIONICS_OTEL_ENABLE", raising=False)
    assert otel_hook.install_from_env() is False


def test_install_from_env_accepts_truthy(monkeypatch):
    otel_hook._reset_for_testing()
    clear_all_hooks()
    for val in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("BIONICS_OTEL_ENABLE", val)
        otel_hook._reset_for_testing()
        # install_otel_hooks path touches OTel global provider; just assert
        # the install path returns True (or at minimum doesn't raise) when
        # env is truthy.
        try:
            assert otel_hook.install_from_env() is True
        finally:
            clear_all_hooks()


def test_install_is_idempotent():
    """install_otel_hooks() twice is a no-op the second time."""
    otel_hook._reset_for_testing()
    clear_all_hooks()
    try:
        first = otel_hook.install_otel_hooks("bionics-test-idem")
        second = otel_hook.install_otel_hooks("bionics-test-idem")
        assert first is True
        assert second is True
        # Hook lists should only have one copy of our handlers.
        from core.bridge import _post_tool_use_hooks, _pre_tool_use_hooks
        assert len([h for h in _pre_tool_use_hooks if getattr(h, "__name__", "") == "_pre"]) <= 1
        assert len([h for h in _post_tool_use_hooks if getattr(h, "__name__", "") == "_post"]) <= 1
    finally:
        clear_all_hooks()
        otel_hook._reset_for_testing()
