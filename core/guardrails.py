"""Input/output guardrails built on top of the ToolGate hook system.

Guardrails are a thin typed layer over `pre_tool_use` / `post_tool_use` hooks
from `core/bridge.py`. They provide:

- **Parallel execution**: each guardrail runs independently; the first one to
  trip wins, the others run anyway for telemetry.
- **Tripwire pattern**: a guardrail may `raise GuardrailTripwire` to abort
  the current agent turn outright (propagated as a tool failure with context).
- **Scoped selectors**: guardrails may register for a specific set of tools,
  categories, or all tools.
- **Default guardrails**: `register_default_guardrails()` wires the prompt-
  injection detector for string-typed arguments that come from OCR or
  user-provided text — a live attack surface on a desktop-automation agent
  (per the hellscape audit, OCR'd screen text is a prompt-injection vector).

Usage:

    from core.guardrails import guardrail, register_default_guardrails

    @guardrail(stage="pre", tools={"type_text", "ue5_run_python"})
    def block_rm_rf(name, args, ctx):
        code = str(args.get("script", ""))
        if "rm -rf /" in code:
            return {"block": True, "reason": "rm -rf / detected"}

    register_default_guardrails()  # wires the prompt-injection detector
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

from core.bridge import HookAbort, register_post_tool_use, register_pre_tool_use

logger = logging.getLogger("bionics.guardrails")


class GuardrailTripwire(HookAbort):
    """Raised by a guardrail to abort the current agent turn entirely.

    Inherits from `HookAbort` (which inherits from `BaseException`) so it
    propagates out of `ToolGate.execute` even though normal `Exception`
    subclasses raised by hooks are caught and logged. Callers (agent loop /
    MCP wrapper) can catch `GuardrailTripwire` specifically to distinguish
    security-halt from generic failures.
    """


# Known prompt-injection patterns. Case-insensitive substring match on any
# string argument. Kept minimal and high-signal — false positives are costly
# because they block legitimate tool calls. Expand via register_injection_pattern().
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?)", re.I),
    re.compile(r"(?:you\s+are\s+now|pretend\s+(?:you\s+)?(?:are|to\s+be))\s+(?:a\s+)?(?:new|different|another)", re.I),
    re.compile(r"system\s*:\s*ignore", re.I),
    re.compile(r"<\s*/?\s*(?:system|admin|root)\s*>", re.I),
    re.compile(r"(?:BEGIN|END)\s+(?:NEW\s+)?(?:SYSTEM|INSTRUCTIONS?)\s+PROMPT", re.I),
]


def register_injection_pattern(pattern: str | re.Pattern) -> None:
    """Add a regex to the prompt-injection pattern list. Case-insensitive default."""
    if isinstance(pattern, str):
        pattern = re.compile(pattern, re.I)
    _INJECTION_PATTERNS.append(pattern)


def detect_injection(text: str) -> re.Pattern | None:
    """Return the first matching pattern or None."""
    if not isinstance(text, str) or not text:
        return None
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            return pat
    return None


def guardrail(
    *,
    stage: str = "pre",
    tools: Iterable[str] | None = None,
    categories: Iterable[str] | None = None,
    tripwire: bool = False,
) -> Callable:
    """Decorator that registers a function as a guardrail hook.

    Args:
        stage: "pre" (before tool execution, can block/mutate) or "post"
            (after tool, can override result).
        tools: Optional set of tool names. If set, guardrail only fires for
            these tools.
        categories: Optional set of tool categories (e.g. "ue5_blueprint").
        tripwire: If True, a return value of `{"block": True}` converts into
            a `GuardrailTripwire` exception. Use for system-halt scenarios.

    The decorated function receives the same args as the underlying hook.
    """
    tool_set = set(tools) if tools else None
    cat_set = set(categories) if categories else None

    def decorator(fn: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            # pre-hook: (tool_name, arguments, context)
            # post-hook: (tool_name, arguments, result, context)
            tool_name = args[0] if args else ""
            ctx = args[-1] if isinstance(args[-1], dict) else {}
            if tool_set is not None and tool_name not in tool_set:
                return None
            if cat_set is not None and ctx.get("category") not in cat_set:
                return None
            out = fn(*args, **kwargs)
            if tripwire and isinstance(out, dict) and out.get("block"):
                raise GuardrailTripwire(
                    f"Guardrail '{fn.__name__}' tripped on {tool_name}: "
                    f"{out.get('reason', 'blocked')}"
                )
            return out

        wrapper.__name__ = fn.__name__
        wrapper.__wrapped__ = fn  # type: ignore
        if stage == "pre":
            register_pre_tool_use(wrapper)
        elif stage == "post":
            register_post_tool_use(wrapper)
        else:
            raise ValueError(f"guardrail stage must be 'pre' or 'post', got {stage!r}")
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Default guardrails — wired by register_default_guardrails() (opt-in).
# ---------------------------------------------------------------------------


def _prompt_injection_guard(tool_name: str, arguments: dict, context: dict):
    """Scan all string-valued args for prompt-injection patterns.

    Runs on every tool call. Low false-positive rate because the patterns are
    specific and high-signal. Blocks with a detailed reason; the agent can
    retry with sanitized input.
    """
    for key, val in (arguments or {}).items():
        if not isinstance(val, str):
            continue
        match = detect_injection(val)
        if match is not None:
            reason = (
                f"prompt-injection pattern '{match.pattern}' detected in "
                f"argument '{key}'. Blocked to prevent instruction hijacking. "
                f"Sanitize the input or remove the suspected text block."
            )
            logger.warning(
                f"guardrail: prompt-injection block on {tool_name}.{key}"
            )
            return {"block": True, "reason": reason}
    return None


def register_default_guardrails() -> None:
    """Register Bionics' default guardrail set. Call once at startup.

    Currently registers:
    - Prompt-injection pattern detector on all tool calls (pre).
    """
    register_pre_tool_use(_prompt_injection_guard)
    logger.info("Default guardrails registered: prompt_injection_guard")
