"""OpenTelemetry observability for Bionics tool calls.

Emits one OTLP span per `ToolGate.execute()` call by wiring PreToolUse /
PostToolUse / Stop hooks from `core/bridge.py`. Each span records:

- `bionics.tool.name`       — the resolved tool name
- `bionics.tool.category`   — category (input, capture, ue5_*, etc.)
- `bionics.tool.safety_tier`— safe | moderate | destructive
- `bionics.tool.ok`         — True if ToolResult.ok else False
- `bionics.tool.elapsed_ms` — from ToolGate timing
- `bionics.tool.arg_count`  — number of top-level arguments

Span status is set to ERROR when the tool failed, with `result.error` as the
description. Spans are bracketed pre→post via a thread-local stack so nested
or concurrent calls on the same thread remain correctly paired.

Installation is opt-in:

    # Programmatic (preferred for apps)
    from core.otel_hook import install_otel_hooks
    install_otel_hooks()

    # Env-driven (for MCP / CLI subprocesses)
    BIONICS_OTEL_ENABLE=1
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf   # or "grpc"

Graceful fallbacks: if `opentelemetry-sdk` is missing, installation is a no-op
that logs a warning. If no OTLP exporter package is available, a Console
exporter is used so tool spans still stream to stderr for debugging.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from core.bridge import (
    register_post_tool_use,
    register_pre_tool_use,
    register_stop_hook,
)

logger = logging.getLogger("bionics.otel")


# Module-level flags so install_otel_hooks() is idempotent.
_installed: bool = False
_install_lock = threading.Lock()

# Thread-local stack of active spans. Pre-hook pushes (span, token); post-hook pops.
_thread_local = threading.local()


def _get_stack() -> list[Any]:
    stack = getattr(_thread_local, "span_stack", None)
    if stack is None:
        stack = []
        _thread_local.span_stack = stack
    return stack


def _build_tracer_provider(service_name: str):
    """Construct a TracerProvider with the best available OTLP exporter.

    Preference order:
        1. OTLP HTTP/protobuf  (env: OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf)
        2. OTLP gRPC
        3. ConsoleSpanExporter (always available, debug mode)

    Returns the provider, or None if OTel SDK is missing.
    """
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError as e:
        logger.warning("opentelemetry-sdk missing — OTel hook disabled (%s)", e)
        return None

    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "bionics",
    })
    provider = TracerProvider(resource=resource)

    exporter = _select_exporter()
    if exporter is None:
        exporter = ConsoleSpanExporter()
        logger.info("OTel: using ConsoleSpanExporter (no OTLP endpoint configured)")

    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def _select_exporter():
    """Pick the best OTLP exporter available, or None to fall back to Console."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return None

    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf").strip().lower()

    if protocol in ("http/protobuf", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            logger.info("OTel: using OTLP HTTP exporter → %s", endpoint)
            return OTLPSpanExporter(endpoint=endpoint)
        except ImportError:
            logger.warning(
                "OTel: opentelemetry-exporter-otlp-proto-http not installed; "
                "falling back to gRPC then Console"
            )

    # gRPC fallback
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        logger.info("OTel: using OTLP gRPC exporter → %s", endpoint)
        return OTLPSpanExporter(endpoint=endpoint)
    except ImportError:
        logger.warning(
            "OTel: no OTLP exporter package installed; falling back to Console"
        )
        return None


def install_otel_hooks(service_name: str | None = None) -> bool:
    """Wire OTel spans into ToolGate lifecycle hooks. Idempotent.

    Returns True if installation succeeded (or was already done), False if
    OpenTelemetry is not available.
    """
    global _installed
    with _install_lock:
        if _installed:
            return True

        svc = service_name or os.environ.get(
            "BIONICS_OTEL_SERVICE_NAME", "bionics-agent"
        )
        provider = _build_tracer_provider(svc)
        if provider is None:
            return False

        try:
            from opentelemetry import trace
        except ImportError:
            return False

        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer("bionics.tool", "0.8.2")

        def _pre(tool_name: str, arguments: dict, context: dict) -> None:
            span = tracer.start_span(f"bionics.tool/{tool_name}")
            try:
                span.set_attribute("bionics.tool.name", tool_name)
                span.set_attribute("bionics.tool.category", context.get("category", ""))
                span.set_attribute(
                    "bionics.tool.safety_tier", context.get("safety_tier", "")
                )
                span.set_attribute("bionics.tool.arg_count", len(arguments or {}))
                if context.get("dry_run"):
                    span.set_attribute("bionics.tool.dry_run", True)
            except Exception as e:  # pragma: no cover
                logger.debug("OTel pre-hook attr set failed: %s", e)
            _get_stack().append(span)
            return None

        def _post(tool_name: str, arguments: dict, result, context: dict) -> None:
            stack = _get_stack()
            if not stack:
                return None
            span = stack.pop()
            try:
                ok = bool(getattr(result, "ok", True))
                span.set_attribute("bionics.tool.ok", ok)
                elapsed_ms = context.get("elapsed_ms")
                if elapsed_ms is not None:
                    span.set_attribute("bionics.tool.elapsed_ms", float(elapsed_ms))
                if not ok:
                    from opentelemetry.trace import Status, StatusCode
                    span.set_status(
                        Status(StatusCode.ERROR, getattr(result, "error", ""))
                    )
            except Exception as e:  # pragma: no cover
                logger.debug("OTel post-hook attr set failed: %s", e)
            finally:
                span.end()
            return None

        def _stop(reason: str, context: dict) -> None:
            # Flush any buffered spans before the process exits, then release the
            # installed flag so a forked child (or a test that restarts the
            # process) can re-install cleanly instead of seeing a stale `True`.
            global _installed
            try:
                provider.force_flush(timeout_millis=5_000)
                provider.shutdown()
            except Exception as e:  # pragma: no cover
                logger.debug("OTel shutdown failed: %s", e)
            with _install_lock:
                _installed = False

        register_pre_tool_use(_pre)
        register_post_tool_use(_post)
        register_stop_hook(_stop)

        _installed = True
        logger.info("OTel hooks installed (service.name=%s)", svc)
        return True


def install_from_env() -> bool:
    """Auto-install if `BIONICS_OTEL_ENABLE` is truthy. Returns True if installed."""
    flag = os.environ.get("BIONICS_OTEL_ENABLE", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    return install_otel_hooks()


def _reset_for_testing() -> None:
    """Clear the installed flag + thread-local stack. Test-only."""
    global _installed
    with _install_lock:
        _installed = False
    _thread_local.span_stack = []
