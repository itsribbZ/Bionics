"""Bionics Memory Tools — expose persistent memory + tool-use cache to LLM callers.

These tools let Claude (via MCP) and any Bionics tool write + query cross-session
state directly. Part of the Phase 4 SOTA upgrade (2026-04-16).

Tool surface:
    bionics_memory_remember      — store a fact keyed by (scope, topic, key)
    bionics_memory_recall        — list entries for (scope, topic)
    bionics_memory_search        — full-text search across memory
    bionics_memory_forget        — delete an entry
    bionics_memory_stats         — counts per scope
    bionics_tool_cache_stats     — Voyager cache summary (total/successful/reused)
    bionics_tool_cache_find      — look up proven sequences for a (topic, prompt)

Scope conventions (suggested, not enforced):
    task_outcome     — completed task results (what worked, what shipped)
    user_preference  — Jacob's explicit rules / likes / dislikes (e.g. "no fanboy phrasing")
    app_pattern      — per-application automation recipes ("to dismiss UE5 autosave: press F6")
    failure          — known failure modes to avoid ("Python RE cannot wire AnimGraph nodes")
    learned_fix      — proven fixes ("AnimBP T-pose -> check DefaultSlot Source pin")
"""

from __future__ import annotations

from typing import Annotated

from core.bridge import SafetyTier, ToolResult, bionics_tool
from core.memory import get_memory
from core.tool_cache import get_tool_cache

# =====================================================================
# Memory Tools
# =====================================================================


@bionics_tool(
    name="bionics_memory_remember",
    category="memory",
    safety_tier=SafetyTier.SAFE,
    aliases=["memory-remember"],
    title="Remember (write to persistent memory)",
)
def bionics_memory_remember(
    scope: Annotated[str, "Category (task_outcome / user_preference / app_pattern / failure / learned_fix)"],
    topic: Annotated[str, "Subject grouping (e.g. ANIMATION, COMBAT, UI, general)"],
    key: Annotated[str, "Unique identifier within (scope, topic)"],
    value: Annotated[str, "Content to store — free-form string or JSON"],
) -> ToolResult:
    """Write an entry to Bionics persistent memory.

    Idempotent on (scope, topic, key) — overwrites if the key already exists.
    """
    try:
        mem = get_memory()
        ok = mem.remember(scope, topic, key, value)
        if ok:
            return ToolResult.success(
                content=f"Remembered [{scope}/{topic}] {key}",
                data={"stored": True, "scope": scope, "topic": topic, "key": key},
            )
        return ToolResult.failure("memory.remember() returned False")
    except Exception as e:
        return ToolResult.failure(f"remember failed: {e}")


@bionics_tool(
    name="bionics_memory_recall",
    category="memory",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["memory-recall"],
    title="Recall (list memory entries by scope/topic)",
)
def bionics_memory_recall(
    scope: Annotated[str, "Category to list"],
    topic: Annotated[str, "Optional subject filter (empty = all topics in scope)"] = "",
    limit: int = 50,
) -> ToolResult:
    """Return memory entries for a given scope (optionally filtered by topic)."""
    try:
        mem = get_memory()
        entries = mem.recall(scope, topic, limit=limit)
        return ToolResult.success(
            content=f"{len(entries)} entries in [{scope}]" + (f"/{topic}" if topic else ""),
            data={"count": len(entries), "entries": entries},
        )
    except Exception as e:
        return ToolResult.failure(f"recall failed: {e}")


@bionics_tool(
    name="bionics_memory_search",
    category="memory",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["memory-search"],
    title="Search memory (full-text across all scopes)",
)
def bionics_memory_search(
    query: Annotated[str, "Search substring (case-insensitive, matches key + value)"],
    limit: int = 20,
) -> ToolResult:
    """Full-text search across all memory entries. Ranks by hit count + recency."""
    try:
        mem = get_memory()
        hits = mem.search(query, limit=limit)
        return ToolResult.success(
            content=f"{len(hits)} hits for '{query}'",
            data={"count": len(hits), "hits": hits},
        )
    except Exception as e:
        return ToolResult.failure(f"search failed: {e}")


@bionics_tool(
    name="bionics_memory_forget",
    category="memory",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    aliases=["memory-forget"],
    title="Forget (delete a memory entry)",
)
def bionics_memory_forget(
    scope: Annotated[str, "Category"],
    key: Annotated[str, "Key to delete"],
    topic: Annotated[str, "Optional topic filter"] = "",
) -> ToolResult:
    """Delete a memory entry. Irreversible."""
    try:
        mem = get_memory()
        deleted = mem.forget(scope, key, topic)
        return ToolResult.success(
            content="Deleted" if deleted else "No matching entry",
            data={"deleted": deleted},
        )
    except Exception as e:
        return ToolResult.failure(f"forget failed: {e}")


@bionics_tool(
    name="bionics_memory_stats",
    category="memory",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["memory-stats"],
    title="Memory stats (counts per scope)",
)
def bionics_memory_stats() -> ToolResult:
    """Return total entry count + per-scope breakdown."""
    try:
        mem = get_memory()
        scopes = ["task_outcome", "user_preference", "app_pattern", "failure", "learned_fix"]
        stats = {scope: mem.count(scope) for scope in scopes}
        stats["total"] = mem.count()
        return ToolResult.success(content=f"Memory: {stats['total']} total entries", data=stats)
    except Exception as e:
        return ToolResult.failure(f"stats failed: {e}")


# =====================================================================
# Tool-Use Cache Tools (Voyager pattern)
# =====================================================================


@bionics_tool(
    name="bionics_tool_cache_stats",
    category="memory",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["tool-cache-stats"],
    title="Tool-use cache stats (Voyager pattern)",
)
def bionics_tool_cache_stats() -> ToolResult:
    """Return Voyager tool-cache stats: total sequences, successful, times reused."""
    try:
        cache = get_tool_cache()
        stats = cache.stats()
        # cache.stats() returns {"error": "..."} on DB failure — surface as tool failure
        if "error" in stats:
            return ToolResult.failure(f"tool_cache_stats: {stats['error']}")
        return ToolResult.success(
            content=f"Cache: {stats.get('total_sequences', 0)} sequences, "
                    f"{stats.get('successful', 0)} successful, "
                    f"{stats.get('total_reuses', 0)} reuses",
            data=stats,
        )
    except Exception as e:
        return ToolResult.failure(f"tool_cache_stats failed: {e}")


@bionics_tool(
    name="bionics_tool_cache_find",
    category="memory",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["tool-cache-find"],
    title="Find proven tool sequences (warm-start template)",
)
def bionics_tool_cache_find(
    topic: Annotated[str, "Topic category (ANIMATION, COMBAT, AI, etc.)"],
    prompt: Annotated[str, "The task prompt to match against past runs"],
    min_success_count: int = 2,
    limit: int = 3,
) -> ToolResult:
    """Return proven tool-call sequences for similar past prompts.

    Useful as a warm-start template — if Bionics succeeded at this same task
    before, skip the cold-planning phase and reuse the sequence.
    """
    try:
        cache = get_tool_cache()
        proven = cache.find_proven(topic, prompt, min_success_count=min_success_count, limit=limit)
        if not proven:
            similar = cache.find_similar(prompt, limit=limit)
            return ToolResult.success(
                content=f"No proven match; {len(similar)} similar runs",
                data={"proven": [], "similar": similar},
            )
        return ToolResult.success(
            content=f"{len(proven)} proven sequences for [{topic}]",
            data={"proven": proven, "similar": []},
        )
    except Exception as e:
        return ToolResult.failure(f"tool_cache_find failed: {e}")
