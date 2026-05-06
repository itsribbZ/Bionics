"""Bionics Tool Bridge - Unified tool registry + execution gate.

This is the SINGLE SOURCE OF TRUTH for all Bionics capabilities.

Every automation action is registered as a @bionics_tool. Every entry point
(MCP server, CLI, HTTP server, GUI) calls into this registry. This eliminates
duplication and lets any new tool be instantly accessible from everywhere.

Design:
- ToolRegistry: singleton holding all registered tools
- @bionics_tool: decorator that auto-extracts schema from type hints + docstring
- ToolGate: wraps execution with safety tier gating and error handling
- ToolResult: unified structured return format

Inspired by soft-ue-cli's JSON-RPC architecture + MCP spec, but integrated
with Bionics's existing safety layer, executor, and UE5 bridge.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any

logger = logging.getLogger("bionics.bridge")


# ============================================================================
# Types
# ============================================================================


class SafetyTier(str, Enum):
    """Safety classification for a tool. Maps to existing safety.py tiers."""
    SAFE = "safe"              # Auto-execute (no confirmation)
    MODERATE = "moderate"      # 1 confirmation required
    DESTRUCTIVE = "destructive"  # 2 confirmations required


@dataclass
class ToolAnnotations:
    """MCP-compatible hints about tool behavior.

    These annotations help client UIs decide when to prompt the user.
    """
    read_only: bool = False      # No side effects (e.g. capture_screen)
    destructive: bool = False    # Cannot be undone (e.g. delete_asset)
    idempotent: bool = False     # Same input → same output every time
    open_world: bool = False     # Interacts with external state
    title: str = ""              # Human-readable title


@dataclass
class ToolResult:
    """Unified result from a tool execution.

    This is returned from every tool. MCP/CLI/HTTP wrappers know how to
    serialize it to their transport format.
    """
    ok: bool = True
    content: str = ""                    # Human-readable text
    data: dict | None = None             # Structured JSON payload
    error: str = ""
    meta: dict | None = None             # Timing, retries, etc.

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "content": self.content,
            "data": self.data or {},
            "error": self.error,
            "meta": self.meta or {},
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def success(cls, content: str = "", data: dict | None = None, **meta) -> ToolResult:
        return cls(ok=True, content=content, data=data, meta=meta or None)

    @classmethod
    def failure(cls, error: str, **meta) -> ToolResult:
        return cls(ok=False, error=error, meta=meta or None)


@dataclass
class ToolSpec:
    """Full specification of a registered tool."""
    name: str
    description: str
    category: str
    fn: Callable
    input_schema: dict                   # JSON Schema draft 2020-12
    safety_tier: SafetyTier
    annotations: ToolAnnotations
    is_async: bool = False
    examples: list[dict] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    # MCP 2025-11-25 outputSchema — describes the shape of ToolResult.data
    # when the tool succeeds. Lets clients type-check structuredContent and
    # chain outputs into follow-up tool calls without re-parsing prose.
    output_schema: dict | None = None

    def to_dict(self) -> dict:
        """Serialize for introspection (CLI --list, MCP tools/list)."""
        d = {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "safety_tier": self.safety_tier.value,
            "input_schema": self.input_schema,
            "annotations": {
                "read_only": self.annotations.read_only,
                "destructive": self.annotations.destructive,
                "idempotent": self.annotations.idempotent,
                "open_world": self.annotations.open_world,
                "title": self.annotations.title,
            },
            "aliases": self.aliases,
            "examples": self.examples,
        }
        if self.output_schema is not None:
            d["output_schema"] = self.output_schema
        return d


# ============================================================================
# Registry
# ============================================================================


class ToolRegistry:
    """Singleton registry of all registered Bionics tools.

    Tools self-register via the @bionics_tool decorator at module import time.
    Use `get_registry()` to access the global instance.
    """

    _instance: ToolRegistry | None = None
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._tools = {}
                    inst._categories = {}
                    inst._aliases = {}
                    inst._write_lock = threading.Lock()
                    cls._instance = inst
        return cls._instance

    def register(self, spec: ToolSpec) -> None:
        with self._write_lock:
            if spec.name in self._tools:
                logger.warning(f"Overwriting existing tool: {spec.name}")
            self._tools[spec.name] = spec
            self._categories.setdefault(spec.category, []).append(spec.name)
            for alias in spec.aliases:
                if alias in self._tools:
                    logger.warning(
                        f"Alias '{alias}' for tool '{spec.name}' shadows existing tool name"
                    )
                if alias in self._aliases and self._aliases[alias] != spec.name:
                    logger.warning(
                        f"Alias '{alias}' for '{spec.name}' overwrites existing alias "
                        f"(was: {self._aliases[alias]})"
                    )
                self._aliases[alias] = spec.name
        logger.debug(
            f"Registered: {spec.name} [{spec.category}] tier={spec.safety_tier.value}"
        )

    def get(self, name: str) -> ToolSpec | None:
        """Look up tool by name or alias.

        Acquires `_write_lock` so a concurrent register() (with possible dict
        resize) cannot trigger 'dictionary changed size during iteration'. The
        critical section is a single dict lookup — contention is microsecond.
        """
        with self._write_lock:
            if name in self._tools:
                return self._tools[name]
            resolved = self._aliases.get(name)
            if resolved:
                return self._tools.get(resolved)
            return None

    def list_all(self) -> list[ToolSpec]:
        with self._write_lock:
            return list(self._tools.values())

    def list_by_category(self, category: str) -> list[ToolSpec]:
        with self._write_lock:
            names = self._categories.get(category, [])
            return [self._tools[n] for n in names]

    def list_names(self) -> list[str]:
        with self._write_lock:
            return sorted(self._tools.keys())

    def categories(self) -> list[str]:
        with self._write_lock:
            return sorted(self._categories.keys())

    def count(self) -> int:
        with self._write_lock:
            return len(self._tools)

    def __len__(self):
        with self._write_lock:
            return len(self._tools)

    def summary(self) -> dict:
        """High-level registry summary.

        Holds `_write_lock` to match the other read methods — protects against
        a concurrent register() racing the dict iteration over `_tools` /
        `_categories`. Critical section is small (3 dict iterations).
        """
        with self._write_lock:
            return {
                "total_tools": len(self._tools),
                "categories": {
                    cat: len(names) for cat, names in sorted(self._categories.items())
                },
                "safety_tiers": {
                    tier.value: sum(1 for t in self._tools.values() if t.safety_tier == tier)
                    for tier in SafetyTier
                },
            }


_registry: ToolRegistry | None = None
_get_registry_lock = threading.Lock()


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        with _get_registry_lock:
            if _registry is None:
                _registry = ToolRegistry()
    return _registry


# ============================================================================
# Decorator
# ============================================================================


def bionics_tool(
    name: str = "",
    *,
    category: str = "general",
    safety_tier: SafetyTier = SafetyTier.SAFE,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
    title: str = "",
    aliases: list[str] | None = None,
    examples: list[dict] | None = None,
    strict: bool = False,
    output_schema: dict | None = None,
):
    """Register a function as a Bionics tool.

    Tool metadata is extracted from:
      - `name` parameter OR the function name (snake_case)
      - Function docstring (first line → description)
      - Type hints → JSON Schema
      - Default values → schema defaults + required list

    Example:
        @bionics_tool(
            name="click_at",
            category="input",
            safety_tier=SafetyTier.MODERATE,
            destructive=False,
        )
        def click_at(
            x: Annotated[int, "X pixel coord"],
            y: Annotated[int, "Y pixel coord"],
            button: Literal["left","right","middle"] = "left",
        ) -> ToolResult:
            '''Click at screen coordinates.'''
            ...
    """
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        docstring = (fn.__doc__ or "").strip()
        description = docstring.split("\n")[0] if docstring else tool_name
        # Full description keeps the rest
        full_desc = docstring if docstring else tool_name

        input_schema = _build_input_schema(fn)
        # Strict mode (grammar-constrained sampling): forbid unknown keys in
        # tool input. Claude's tool-use pathway respects additionalProperties:
        # false — eliminates schema-violation retry loops on destructive tools.
        if strict:
            input_schema["additionalProperties"] = False
        is_async = inspect.iscoroutinefunction(fn)

        annotations = ToolAnnotations(
            read_only=read_only,
            destructive=destructive,
            idempotent=idempotent,
            open_world=open_world,
            title=title or tool_name.replace("_", " ").title(),
        )

        spec = ToolSpec(
            name=tool_name,
            description=full_desc,
            category=category,
            fn=fn,
            input_schema=input_schema,
            safety_tier=safety_tier,
            annotations=annotations,
            is_async=is_async,
            examples=examples or [],
            aliases=aliases or [],
            output_schema=output_schema,
        )

        get_registry().register(spec)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper._bionics_spec = spec  # type: ignore
        return wrapper

    return decorator


# ============================================================================
# Schema Generation
# ============================================================================


def _build_input_schema(fn: Callable) -> dict:
    """Build JSON Schema (draft 2020-12) from a function signature."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}, "required": []}

    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception as e:
        logger.warning(f"Type hints failed for {fn.__name__}: {e}")
        hints = {}

    properties: dict[str, dict] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        # Skip special params
        if param_name in ("self", "cls", "ctx", "context"):
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        hint = hints.get(param_name, str)
        schema = _type_to_schema(hint)

        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        else:
            # Attach default (for booleans/numbers/strings/None)
            if param.default is None:
                schema.setdefault("default", None)
            elif isinstance(param.default, (str, int, float, bool, list, dict)):
                schema["default"] = param.default

        properties[param_name] = schema

    result = {
        "type": "object",
        "properties": properties,
    }
    if required:
        result["required"] = required
    return result


def _type_to_schema(hint: Any) -> dict:
    """Convert a Python type hint to a JSON Schema fragment."""
    # None type
    if hint is type(None):
        return {"type": "null"}

    origin = typing.get_origin(hint)
    args = typing.get_args(hint)

    # Annotated[X, metadata...]
    if origin is typing.Annotated or (
        hasattr(typing, "_AnnotatedAlias") and isinstance(hint, typing._AnnotatedAlias)
    ):
        if args:
            inner = args[0]
            schema = _type_to_schema(inner)
            for extra in args[1:]:
                _apply_metadata(schema, extra)
            return schema
        return {"type": "string"}

    # Literal[...]
    if origin is typing.Literal:
        values = list(args)
        # Infer type from first value
        first = values[0] if values else ""
        if isinstance(first, str):
            return {"type": "string", "enum": values}
        if isinstance(first, int):
            return {"type": "integer", "enum": values}
        return {"enum": values}

    # Union / Optional — covers both typing.Union[X, Y] and PEP 604 X | Y syntax.
    # typing.get_origin(int | None) returns types.UnionType on Python 3.10+;
    # typing.UnionType only exists on Python 3.14+, so don't rely on it.
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in args if a is not type(None)]
        has_none = len(non_none) < len(args)
        if len(non_none) == 1:
            schema = _type_to_schema(non_none[0])
            if has_none:
                schema["nullable"] = True
            return schema
        variants = [_type_to_schema(a) for a in non_none]
        if has_none:
            variants.append({"type": "null"})
        return {"anyOf": variants}

    # list / tuple
    if origin in (list, tuple):
        item_type = args[0] if args else None
        schema = {"type": "array"}
        if item_type is not None:
            schema["items"] = _type_to_schema(item_type)
        return schema

    # dict
    if origin is dict:
        return {"type": "object"}

    # Primitive types
    type_map = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        list: {"type": "array"},
        dict: {"type": "object"},
        bytes: {"type": "string", "format": "byte"},
    }
    if hint in type_map:
        return dict(type_map[hint])

    # Fallback
    return {"type": "string"}


def _apply_metadata(schema: dict, meta: Any) -> None:
    """Apply Annotated[] metadata to a schema (description, bounds, etc.)."""
    if isinstance(meta, str):
        # Plain string annotation = description
        schema["description"] = meta
        return

    # pydantic Field-style metadata
    for attr, key in [
        ("description", "description"),
        ("ge", "minimum"),
        ("le", "maximum"),
        ("gt", "exclusiveMinimum"),
        ("lt", "exclusiveMaximum"),
        ("min_length", "minLength"),
        ("max_length", "maxLength"),
        ("pattern", "pattern"),
    ]:
        if hasattr(meta, attr):
            val = getattr(meta, attr)
            if val is not None:
                schema[key] = val


# ============================================================================
# Execution Gate
# ============================================================================


# ============================================================================
# Lifecycle hooks — PreToolUse / PostToolUse / Stop
#
# Modeled on the Claude Agent SDK hook lifecycle (2026 canonical shape). Hooks
# are fired by ToolGate.execute() before and after each tool call, and by
# AgentCore on stop/session-end. They enable:
#   - Guardrail sandwich (block malicious tool calls before they run)
#   - Audit trail at the right abstraction (tool-level, not agent-level)
#   - Programmatic observability (OpenTelemetry wire-up, metrics collection)
#   - Rate-limiting / cost budgets per-tool
#
# Hooks are registered process-wide and are expected to be fast (< 50ms each).
# They run synchronously on the ToolGate.execute() call thread.
# ============================================================================

# Hook callable signatures:
#   pre_tool_use:  (tool_name, arguments, context) -> {"block": bool, "reason": str,
#                                                      "arguments": dict} | None
#   post_tool_use: (tool_name, arguments, result, context) -> {"result": ToolResult} | None
#   stop:          (reason, context) -> None


class HookAbort(BaseException):
    """Base for hook exceptions that must propagate out of ToolGate.execute.

    Normal `Exception` subclasses raised inside hooks are caught and logged so
    one badly-written hook can't crash every tool call. `HookAbort` inherits
    from `BaseException` so it bypasses that `except Exception` net — use this
    when a hook wants to halt the entire agent turn (e.g. a guardrail tripwire).
    """


_hooks_lock = threading.Lock()
_pre_tool_use_hooks: list[Callable] = []
_post_tool_use_hooks: list[Callable] = []
_stop_hooks: list[Callable] = []


def register_pre_tool_use(hook: Callable) -> None:
    """Register a PreToolUse hook. Hook signature:
    `(tool_name: str, arguments: dict, context: dict) -> dict | None`

    Return `{"block": True, "reason": "..."}` to abort execution.
    Return `{"arguments": {...}}` to mutate the tool's arguments.
    Return None (or empty dict) to let the call proceed unchanged.
    """
    with _hooks_lock:
        _pre_tool_use_hooks.append(hook)


def register_post_tool_use(hook: Callable) -> None:
    """Register a PostToolUse hook. Hook signature:
    `(tool_name: str, arguments: dict, result: ToolResult, context: dict) -> dict | None`

    Return `{"result": new_result}` to override the result.
    Return None to let the original result flow through.
    """
    with _hooks_lock:
        _post_tool_use_hooks.append(hook)


def register_stop_hook(hook: Callable) -> None:
    """Register a Stop hook. Hook signature:
    `(reason: str, context: dict) -> None`

    Called by AgentCore on agent stop / session end. Use for resource cleanup,
    final metric flushes, log closures, etc.
    """
    with _hooks_lock:
        _stop_hooks.append(hook)


def clear_all_hooks() -> None:
    """Clear every registered hook. Useful for test isolation."""
    with _hooks_lock:
        _pre_tool_use_hooks.clear()
        _post_tool_use_hooks.clear()
        _stop_hooks.clear()


def _fire_pre_tool_use(tool_name: str, arguments: dict, context: dict) -> tuple[dict, dict | None]:
    """Fire all PreToolUse hooks. Returns (mutated_args, block_dict_or_None).

    If any hook returns `{"block": True}`, the first such return wins and
    subsequent hooks are skipped. Otherwise arguments may be mutated sequentially.
    """
    current_args = arguments
    with _hooks_lock:
        hooks_snapshot = list(_pre_tool_use_hooks)
    for hook in hooks_snapshot:
        try:
            out = hook(tool_name, current_args, context)
        except Exception as e:
            logging.getLogger("bionics.hooks").warning(
                f"pre_tool_use hook {hook!r} raised: {e}"
            )
            continue
        if not out:
            continue
        if out.get("block"):
            return current_args, out
        if "arguments" in out and isinstance(out["arguments"], dict):
            current_args = out["arguments"]
    return current_args, None


def _fire_post_tool_use(tool_name: str, arguments: dict, result: ToolResult, context: dict) -> ToolResult:
    """Fire all PostToolUse hooks. Returns possibly-overridden result."""
    current_result = result
    with _hooks_lock:
        hooks_snapshot = list(_post_tool_use_hooks)
    for hook in hooks_snapshot:
        try:
            out = hook(tool_name, arguments, current_result, context)
        except Exception as e:
            logging.getLogger("bionics.hooks").warning(
                f"post_tool_use hook {hook!r} raised: {e}"
            )
            continue
        if out and "result" in out:
            current_result = out["result"]
    return current_result


def fire_stop_hooks(reason: str, context: dict | None = None) -> None:
    """Fire all registered Stop hooks. Called by AgentCore.stop()."""
    context = context or {}
    with _hooks_lock:
        hooks_snapshot = list(_stop_hooks)
    for hook in hooks_snapshot:
        try:
            hook(reason, context)
        except Exception as e:
            logging.getLogger("bionics.hooks").warning(
                f"stop hook {hook!r} raised: {e}"
            )


class ToolGate:
    """Executes registered tools through safety + normalization pipeline.

    All entry points (MCP, CLI, HTTP, GUI) should call into this instead of
    invoking tool functions directly. The gate ensures:
      - Tool lookup + alias resolution
      - Safety tier confirmation
      - Argument validation against schema
      - Exception normalization to ToolResult
      - Optional verification post-execution
      - PreToolUse / PostToolUse hook firing for guardrails + audit
    """

    def __init__(
        self,
        safety_layer=None,
        verifier=None,
        logger_name: str = "bionics.gate",
    ):
        self.safety = safety_layer
        self.verifier = verifier
        self.logger = logging.getLogger(logger_name)
        self._bypass_safety: bool = False  # For headless/scripted mode

    def set_bypass_safety(self, enabled: bool) -> None:
        """Bypass the safety layer (only for trusted scripted callers)."""
        self._bypass_safety = enabled
        if enabled:
            self.logger.warning("Safety gate BYPASSED — trusted mode active")

    def execute(
        self,
        tool_name: str,
        arguments: dict | None = None,
        *,
        dry_run: bool = False,
        confirm_override: bool = False,
    ) -> ToolResult:
        """Execute a registered tool by name.

        Args:
            tool_name: Tool identifier (or alias).
            arguments: Dict of keyword arguments for the tool.
            dry_run: Validate only, don't execute.
            confirm_override: Skip safety confirmation (pre-approved).

        Returns:
            ToolResult with normalized structure.
        """
        arguments = arguments or {}
        spec = get_registry().get(tool_name)
        if spec is None:
            available = get_registry().list_names()[:10]
            return ToolResult.failure(
                f"Unknown tool: '{tool_name}'. Use 'list_tools' to see all. "
                f"First 10: {available}"
            )

        # Fire PreToolUse hooks — may mutate arguments or block execution.
        hook_context = {
            "tool_name": tool_name,
            "safety_tier": spec.safety_tier.value,
            "category": spec.category,
            "dry_run": dry_run,
            "bypass_safety": self._bypass_safety,
        }
        arguments, blocked = _fire_pre_tool_use(tool_name, arguments, hook_context)
        if blocked is not None:
            reason = blocked.get("reason", "blocked by pre_tool_use hook")
            self.logger.info(f"Tool {tool_name} blocked by hook: {reason}")
            return ToolResult.failure(
                f"Blocked by pre_tool_use hook: {reason}",
                tool=tool_name, blocked_by_hook=True,
            )

        # Validate arguments against schema (after any hook mutation)
        validation_error = self._validate_args(arguments, spec.input_schema)
        if validation_error:
            return ToolResult.failure(
                f"Argument validation failed for '{tool_name}': {validation_error}",
                tool=tool_name, provided_args=list(arguments.keys()),
            )

        # Dry run — return plan without executing
        if dry_run:
            return ToolResult.success(
                content=f"[dry-run] Would execute: {tool_name}",
                data={
                    "tool": tool_name,
                    "arguments": arguments,
                    "safety_tier": spec.safety_tier.value,
                    "category": spec.category,
                },
            )

        # Safety gate
        if (
            spec.safety_tier != SafetyTier.SAFE
            and not self._bypass_safety
            and not confirm_override
            and self.safety is not None
        ):
            details = json.dumps(arguments, default=str)[:200]
            check = self.safety.check_action(tool_name, details=details)
            if not check.approved:
                return ToolResult.failure(
                    f"Action denied by safety layer: {check.deny_reason}",
                    tier=spec.safety_tier.value,
                    denied=True,
                )

        # Execute
        import asyncio as _asyncio
        import inspect as _inspect
        import time as _time
        start = _time.time()
        try:
            result = spec.fn(**arguments)
            # Coroutine guard — if tool is async (or returned a coroutine inadvertently), await it.
            # Handles spec.is_async flag AND runtime detection for unmarked async defs.
            if _inspect.iscoroutine(result):
                try:
                    _loop = _asyncio.get_event_loop()
                    if _loop.is_running():
                        # Already inside an async context (FastMCP path). Spawn a worker
                        # thread, but propagate contextvars (e.g. mcp_ctx._mcp_context_var)
                        # via copy_context() so async tools can still call get_mcp_context().
                        import concurrent.futures as _cf
                        import contextvars as _cv
                        _ctx = _cv.copy_context()
                        _coro = result
                        def _run_with_ctx():
                            return _ctx.run(_asyncio.run, _coro)
                        with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                            result = _ex.submit(_run_with_ctx).result()
                    else:
                        result = _loop.run_until_complete(result)
                except RuntimeError:
                    result = _asyncio.run(result)
        except TypeError as e:
            return ToolResult.failure(
                f"Tool '{tool_name}' signature mismatch: {e}",
                tool=tool_name,
            )
        except Exception as e:
            self.logger.exception(f"Tool {tool_name} raised exception")
            return ToolResult.failure(
                f"{type(e).__name__}: {e}",
                tool=tool_name,
            )
        elapsed_ms = (_time.time() - start) * 1000

        # Normalize result
        normalized = self._normalize_result(result, tool_name)
        if normalized.meta is None:
            normalized.meta = {}
        normalized.meta["tool"] = tool_name
        normalized.meta["elapsed_ms"] = round(elapsed_ms, 2)
        normalized.meta["tier"] = spec.safety_tier.value

        # Fire PostToolUse hooks — may override the result.
        hook_context["elapsed_ms"] = normalized.meta["elapsed_ms"]
        normalized = _fire_post_tool_use(tool_name, arguments, normalized, hook_context)

        return normalized

    def _validate_args(self, arguments: dict, schema: dict) -> str | None:
        """Basic JSON Schema validation (required + type checking). Returns error string or None."""
        required = schema.get("required", [])
        for req in required:
            if req not in arguments:
                return f"missing required argument '{req}'"
            # A required field passed as None is still invalid
            if arguments[req] is None:
                prop_schema = schema.get("properties", {}).get(req, {})
                # Unless schema explicitly allows null
                if prop_schema.get("nullable"):
                    continue
                any_of = prop_schema.get("anyOf") or []
                if any(s.get("type") == "null" for s in any_of):
                    continue
                return f"required argument '{req}' cannot be None"

        properties = schema.get("properties", {})
        for key, value in arguments.items():
            if key not in properties:
                continue  # Allow extra args (tool may accept **kwargs)
            prop_schema = properties[key]
            # None is allowed if schema permits null (Optional/Union)
            if value is None:
                if prop_schema.get("nullable") or "null" in (prop_schema.get("type") or []):
                    continue
                any_of = prop_schema.get("anyOf") or []
                if any(s.get("type") == "null" for s in any_of):
                    continue
                # Not required anyway — None can be filtered out
                continue

            expected_type = prop_schema.get("type")
            if expected_type is None:
                # Handle anyOf — value must match at least one variant
                any_of = prop_schema.get("anyOf")
                if any_of:
                    if not any(_check_type(value, s.get("type", "")) for s in any_of):
                        types = [s.get("type") for s in any_of]
                        return (
                            f"argument '{key}' does not match any of {types}, "
                            f"got '{type(value).__name__}'"
                        )
                continue

            if not _check_type(value, expected_type):
                return (
                    f"argument '{key}' expected type '{expected_type}', "
                    f"got '{type(value).__name__}'"
                )
            # Bounds
            if expected_type in ("integer", "number"):
                mn = prop_schema.get("minimum")
                mx = prop_schema.get("maximum")
                if mn is not None and value < mn:
                    return f"argument '{key}'={value} below minimum {mn}"
                if mx is not None and value > mx:
                    return f"argument '{key}'={value} above maximum {mx}"
            # Enum
            enum = prop_schema.get("enum")
            if enum is not None and value not in enum:
                return f"argument '{key}'={value!r} not in allowed values {enum}"
            # String length
            if expected_type == "string":
                min_len = prop_schema.get("minLength")
                max_len = prop_schema.get("maxLength")
                if min_len is not None and len(value) < min_len:
                    return f"argument '{key}' shorter than minLength {min_len}"
                if max_len is not None and len(value) > max_len:
                    return f"argument '{key}' longer than maxLength {max_len}"
        return None

    def _normalize_result(self, result: Any, tool_name: str) -> ToolResult:
        """Convert any tool return value into a ToolResult."""
        if isinstance(result, ToolResult):
            return result
        if result is None:
            return ToolResult.success(content="ok")
        if isinstance(result, dict):
            # If it looks like a ToolResult dict
            if "ok" in result and ("content" in result or "data" in result or "error" in result):
                return ToolResult(
                    ok=bool(result.get("ok", True)),
                    content=str(result.get("content", "")),
                    data=result.get("data"),
                    error=str(result.get("error", "")),
                    meta=result.get("meta"),
                )
            # Plain dict → structured data
            return ToolResult.success(
                content=json.dumps(result, default=str)[:500],
                data=result,
            )
        if isinstance(result, (list, tuple)):
            return ToolResult.success(
                content=f"returned {len(result)} items",
                data={"items": list(result)},
            )
        if isinstance(result, bool):
            return ToolResult.success(
                content="true" if result else "false",
                data={"result": result},
            )
        if isinstance(result, (int, float)):
            return ToolResult.success(
                content=str(result),
                data={"result": result},
            )
        if isinstance(result, str):
            return ToolResult.success(content=result)
        # Fallback: stringify
        return ToolResult.success(
            content=str(result)[:500],
            data={"result": str(result)},
        )


def _check_type(value: Any, schema_type: str) -> bool:
    """Check if a value conforms to a JSON Schema type string."""
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "array":
        return isinstance(value, (list, tuple))
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "null":
        return value is None
    return True


# ============================================================================
# Entry Point Helpers
# ============================================================================


def tool_dispatch(tool_name: str, arguments: dict | None = None) -> dict:
    """Tool execution with safety bypass — gated behind env var.

    Only available when BIONICS_TOOL_DISPATCH_UNSAFE=1 is set.
    Returns the tool result as a dict. Raises nothing — all errors in the dict.
    """
    if not os.environ.get("BIONICS_TOOL_DISPATCH_UNSAFE"):
        return {"ok": False, "error": "tool_dispatch requires BIONICS_TOOL_DISPATCH_UNSAFE=1 env var"}

    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute(tool_name, arguments or {})
    return result.to_dict()


def list_tools_json() -> str:
    """Return all registered tools as a JSON string (for introspection)."""
    return json.dumps(
        [spec.to_dict() for spec in get_registry().list_all()],
        indent=2,
        default=str,
    )


def print_tool_tree():
    """Print a tree view of registered tools (for CLI --list)."""
    reg = get_registry()
    print(f"\nBionics Tool Registry — {len(reg)} tools\n{'=' * 60}")
    for category in sorted(reg.categories()):
        tools = reg.list_by_category(category)
        print(f"\n[{category}]  ({len(tools)} tools)")
        for tool in sorted(tools, key=lambda t: t.name):
            tier_mark = {
                SafetyTier.SAFE: " ",
                SafetyTier.MODERATE: "⚠",
                SafetyTier.DESTRUCTIVE: "⛔",
            }.get(tool.safety_tier, "?")
            print(f"  {tier_mark} {tool.name:30s} {tool.description[:60]}")
    print()
