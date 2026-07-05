"""Named sub-agent definitions + parallel fan-out.

A sub-agent is a *recipe* for a lightweight Claude Messages API call with its
own system prompt, tool subset, and model override. Unlike `AgentCore` (the
full GUI/capture/verification stack), sub-agents are pure API calls — cheap
to spawn, trivial to run in parallel.

Use cases:

    # 1. Plan-and-critique — two perspectives in one wall-clock second.
    planner = AgentDefinition(
        name="planner",
        description="Proposes a plan",
        system_prompt="You are a planner. Decompose the task into steps.",
        tools=["list_tools"],
    )
    critic = AgentDefinition(
        name="critic",
        description="Pokes holes",
        system_prompt="You are a critic. Find risks in the plan.",
    )
    plan, critique = dispatch_parallel_sync(
        [planner, critic],
        ["Build a combat locomotion system", "Build a combat locomotion system"],
    )

    # 2. Multi-perspective research — fan out by domain.
    agents = [
        AgentDefinition(name="west", system_prompt="Research western AAA..."),
        AgentDefinition(name="east", system_prompt="Research eastern AAA..."),
    ]
    results = dispatch_parallel_sync(agents, ["Find motion matching patterns"] * 2)

    # 3. Validation ensemble — same prompt, different system prompts, vote.

Sub-agents share the same Anthropic client (see `core/anthropic_client.py`),
so fan-out doesn't multiply connection pools.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from core.anthropic_client import get_shared_client
from core.bridge import SafetyTier, ToolGate, get_registry

logger = logging.getLogger("bionics.subagents")


def _destructive_allowed() -> bool:
    """Read BIONICS_MCP_ALLOW_DESTRUCTIVE per-call. Mirrors mcp_server.py + task_manager.py."""
    return os.environ.get("BIONICS_MCP_ALLOW_DESTRUCTIVE", "").strip().lower() in (
        "true", "1", "yes", "on"
    )

# Default model — matches the rest of Bionics (config.yaml bionics.model).
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class AgentDefinition:
    """Recipe for a lightweight sub-agent.

    Fields:
        name:          Short identifier used in logs + AgentResult.agent_name.
        description:   Human-readable one-liner (why this agent exists).
        system_prompt: The system prompt sent to Claude.
        tools:         List of tool NAMES from the global registry the agent
                       may call. `None` = no tool use (pure text generation).
                       `["*"]` = all registered tools (use with care).
        model:         Claude model ID. Defaults to the project-wide default.
        max_tokens:    Max tokens per API call (default 4096).
        max_turns:     Safety cap on the tool-use loop (default 8).
        temperature:   Sampling temperature (default 0.0 = deterministic).
        tool_choice:   Optional tool-choice directive. Accepts any of:
                         - None   → Claude decides (default; mixes tool_use + text)
                         - "auto" → same as None (explicit)
                         - "any"  → Claude MUST emit a tool_use block (any tool)
                         - "required" → alias for "any"
                         - dict   → raw Messages API shape, e.g.
                                   {"type":"tool","name":"my_tool"} or
                                   {"type":"any","disable_parallel_tool_use":true}
                       Ignored when `tools` is None. Useful for forcing structured
                       output — pair with a single tool + strict schema to get
                       grammar-constrained sampling.
    """
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list[str] | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    max_turns: int = 8
    temperature: float = 0.0
    tool_choice: str | dict | None = None


def _normalize_tool_choice(raw: str | dict | None) -> dict | None:
    """Expand string shortcuts into the full Messages API dict shape."""
    if raw is None:
        return None
    if isinstance(raw, str):
        key = raw.lower()
        if key in ("auto",):
            return {"type": "auto"}
        if key in ("any", "required"):
            return {"type": "any"}
        raise ValueError(
            f"Unknown tool_choice shortcut {raw!r}. Use 'auto' | 'any' | 'required' or a dict."
        )
    return raw


@dataclass
class AgentResult:
    """Outcome of one sub-agent invocation."""
    agent_name: str
    ok: bool
    output: str                                   # Final assistant text
    tool_calls: list[dict] = field(default_factory=list)  # [{tool, args, result, ok}]
    turns: int = 0
    duration_ms: float = 0.0
    stop_reason: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


# ============================================================================
# Helpers
# ============================================================================


def _select_tool_schemas(tool_names: list[str] | None) -> list[dict]:
    """Convert a list of tool names into Claude `tools=` schemas.

    `None` → empty (no tool use). `["*"]` → every registered tool.
    Returns schemas in the Claude Messages API format:
        {name, description, input_schema}
    """
    if tool_names is None:
        return []
    registry = get_registry()
    selected = registry.list_all() if tool_names == ["*"] else [
        s for s in (registry.get(n) for n in tool_names) if s is not None
    ]
    return [
        {
            "name": spec.name,
            "description": spec.description.split("\n")[0],
            "input_schema": spec.input_schema,
        }
        for spec in selected
    ]


def _extract_text(content_blocks: list[Any]) -> str:
    """Pull text out of a Claude response content list, skipping thinking / tool_use."""
    pieces: list[str] = []
    for block in content_blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            pieces.append(getattr(block, "text", ""))
    return "".join(pieces).strip()


# ============================================================================
# Core invocation
# ============================================================================


def run_agent(
    definition: AgentDefinition,
    prompt: str,
    gate: ToolGate | None = None,
) -> AgentResult:
    """Execute a sub-agent to completion (synchronous).

    Runs a capped tool-use loop:
      1. Send messages to Claude with the agent's system prompt + tool schemas.
      2. If Claude emits tool_use blocks, run each via `ToolGate`, append
         tool_result blocks, and loop.
      3. When Claude returns stop_reason="end_turn" (or the turn cap trips),
         return the accumulated text + tool-call trace.

    Never raises — errors land in `AgentResult.error` and `ok=False`.
    """
    started = time.time()
    result = AgentResult(agent_name=definition.name, ok=False, output="")

    try:
        client = get_shared_client()
    except Exception as e:
        result.error = f"anthropic_client unavailable: {e}"
        result.duration_ms = (time.time() - started) * 1000
        return result

    # Always build a fresh gate per invocation. Sharing across dispatch_parallel
    # workers would mean N threads calling set_bypass_safety concurrently on the
    # same mutable object. Registry lookups are O(1) dict reads, so the per-call
    # construction cost is negligible.
    gate = ToolGate() if gate is None else gate
    gate.set_bypass_safety(True)  # Sub-agents run trusted for SAFE/MODERATE
    # Mirror mcp_server.py + TaskManager DESTRUCTIVE gate: when an AgentDefinition
    # explicitly enumerates a DESTRUCTIVE-tier tool, refuse unless the operator
    # opts in via BIONICS_MCP_ALLOW_DESTRUCTIVE. `definition.tools=None` means
    # "all tools" — that's an explicit trust contract, skip the pre-check.
    if definition.tools and not _destructive_allowed():
        reg = get_registry()
        destructive = [
            t for t in definition.tools
            if (spec := reg.get(t)) and spec.safety_tier == SafetyTier.DESTRUCTIVE
        ]
        if destructive:
            result.error = (
                f"Sub-agent '{definition.name}' requested DESTRUCTIVE tool(s) "
                f"{destructive} without BIONICS_MCP_ALLOW_DESTRUCTIVE=true."
            )
            result.duration_ms = (time.time() - started) * 1000
            return result
    tools = _select_tool_schemas(definition.tools)
    tool_choice = _normalize_tool_choice(definition.tool_choice)
    messages: list[dict] = [{"role": "user", "content": prompt}]

    try:
        for turn in range(definition.max_turns):
            result.turns = turn + 1
            kwargs: dict[str, Any] = {
                "model": definition.model,
                "max_tokens": definition.max_tokens,
                "temperature": definition.temperature,
                "system": definition.system_prompt,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
                if tool_choice is not None:
                    kwargs["tool_choice"] = tool_choice

            response = client.messages.create(**kwargs)

            result.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
            result.output_tokens += getattr(response.usage, "output_tokens", 0) or 0
            result.stop_reason = getattr(response, "stop_reason", "") or ""

            if result.stop_reason != "tool_use":
                result.output = _extract_text(response.content)
                result.ok = True
                break

            # Tool-use turn: execute every tool_use block and feed results back.
            tool_results: list[dict] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool_name = getattr(block, "name", "")
                tool_args = getattr(block, "input", {}) or {}
                tool_use_id = getattr(block, "id", "")
                tool_result = gate.execute(tool_name, tool_args)
                result.tool_calls.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "ok": tool_result.ok,
                    "content": tool_result.content[:500],
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": tool_result.content or tool_result.error or "",
                    "is_error": not tool_result.ok,
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            # Hit max_turns without a natural stop.
            result.output = _extract_text(response.content)
            result.error = f"max_turns exceeded ({definition.max_turns})"
            result.ok = bool(result.output)  # Partial credit if we have text

    except Exception as e:
        logger.exception("Sub-agent %s failed", definition.name)
        result.error = f"{type(e).__name__}: {e}"
    finally:
        result.duration_ms = (time.time() - started) * 1000

    return result


# ============================================================================
# Parallel fan-out
# ============================================================================


async def dispatch_parallel(
    definitions: list[AgentDefinition],
    prompts: list[str] | str,
    gate: ToolGate | None = None,
) -> list[AgentResult]:
    """Run N sub-agents concurrently. Returns results in the same order as inputs.

    `prompts` may be a single string (broadcast to every agent) or a list
    paired 1-to-1 with `definitions`.
    """
    if len(definitions) == 0:
        return []
    if isinstance(prompts, str):
        prompts = [prompts] * len(definitions)
    if len(prompts) != len(definitions):
        raise ValueError(
            f"prompts length {len(prompts)} != definitions length {len(definitions)}"
        )

    # Per-worker gate so `set_bypass_safety` isn't a cross-thread write on one
    # mutable object. When the caller supplies a gate explicitly we honor it —
    # callers that share a gate are declaring they've serialized access.
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, run_agent, d, p, gate)
        for d, p in zip(definitions, prompts)
    ]
    return list(await asyncio.gather(*tasks))


def dispatch_parallel_sync(
    definitions: list[AgentDefinition],
    prompts: list[str] | str,
    gate: ToolGate | None = None,
) -> list[AgentResult]:
    """Synchronous wrapper around `dispatch_parallel`.

    Safe to call from BOTH sync code AND inside an already-running event loop
    (e.g., from a FastMCP async tool handler). When a loop is already active,
    `asyncio.run()` would raise `RuntimeError: This event loop is already
    running`; we detect that and run the coroutine in a fresh loop on a
    worker thread instead.
    """
    try:
        asyncio.get_running_loop()
        # We are inside a running loop — cannot call asyncio.run here. Run
        # the coroutine in a fresh loop on a worker thread and block on it.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, dispatch_parallel(definitions, prompts, gate=gate))
            return future.result()
    except RuntimeError:
        # No running loop — safe to use asyncio.run directly.
        return asyncio.run(dispatch_parallel(definitions, prompts, gate=gate))
