"""Bionics Tool Registrations.

Importing this package triggers @bionics_tool decorators in every submodule,
populating the global ToolRegistry. After import, `get_registry()` contains
all available tools.

Organization:
    system.py        — Input, capture, vision, clipboard, windows, processes
    bionics_core.py  — Plans, watch, undo, audit, meta tools
    ue5_actor.py     — Actor/level/component spawn/query/modify/delete
    ue5_blueprint.py — Blueprint editing (graphs, pins, nodes, interfaces)
    ue5_asset.py     — Asset create/save/delete/query/preview
    ue5_runtime.py   — PIE, console, logs, python execution
    ue5_editor.py    — StateTree, widget, material, profiling, build, LiveCoding
    ue5_animgraph.py — AnimGraph full automation + BPDoctor integration (via C++ plugin)
    ue5_eventgraph.py — EventGraph (K2) editing — combat polish enabler (via C++ plugin, v0.5.11)
    ue5_rigging.py   — IK Rig + IK Retargeter + batch retarget (Bible Step 3)
    ue5_retarget.py  — native batch retarget (UE5.7 duplicate_and_retarget, :8090) — M5 Stage 2
    ue5_controlrig.py — Control Rig asset + AnimBP assign (Bible Step 6)
    ue5_animlayer.py — Linked Anim Layer AnimBP create (Bible Step 2, 2026-04-17)
    ue5_niagara.py   — VFX spawn + User Exposed Param bind (2026-04-17)
    ue5_audio.py     — SoundWave import + SoundAttenuation configure (2026-04-17)
    memory_tools.py  — Persistent memory + Voyager tool-use cache (Phase 4 SOTA)
    task_tools.py    — Async task manager MCP tools (MCP 2025-11-25 Tasks, 2026-04-23)
"""

from core.bridge import SafetyTier, ToolGate, ToolResult, bionics_tool, get_registry

__all__ = [
    "get_registry",
    "ToolGate",
    "ToolResult",
    "SafetyTier",
    "bionics_tool",
    "register_all",
    "register_core_only",
    "register_ue5_only",
]


def register_all() -> int:
    """Import every tool module, registering all tools. Returns tool count."""
    # Bionics-native
    # UE5 tool suites
    from bionics_tools import (
        bionics_core,  # noqa: F401
        bp_doctor,  # noqa: F401 — AnimBPDoctor CLI wrapper
        market,  # noqa: F401
        memory_tools,  # noqa: F401 — Persistent memory + Voyager cache (Phase 4)
        system,  # noqa: F401
        task_tools,  # noqa: F401 — Async task manager (MCP 2025-11-25 Tasks)
        ue5_actor,  # noqa: F401
        ue5_animgraph,  # noqa: F401 — AnimGraph + BPDoctor via C++ plugin
        ue5_animlayer,  # noqa: F401 — Linked Anim Layer create (Bible Step 2)
        ue5_asset,  # noqa: F401
        ue5_audio,  # noqa: F401 — SoundWave import + attenuation
        ue5_autorig,  # noqa: F401 — fail-closed bone-validate + IKRig (:8090)
        ue5_blueprint,  # noqa: F401
        ue5_controlrig,  # noqa: F401 — Control Rig (Bible Step 6)
        ue5_editor,  # noqa: F401
        ue5_eventgraph,  # noqa: F401 — EventGraph (K2) editing via C++ plugin (v0.5.11)
        ue5_native,  # noqa: F401 — C++ plugin bridge
        ue5_niagara,  # noqa: F401 — VFX spawn + param bind
        ue5_retarget,  # noqa: F401 — native batch retarget (M5 Stage 2, UE5.7 :8090)
        ue5_rigging,  # noqa: F401 — IK Rig + IK Retargeter (Bible Step 3)
        ue5_runtime,  # noqa: F401
        ue5_uasvc,  # noqa: F401 — UE5 Asset Service: native skeletal import (:8090)
        watch_mode,  # noqa: F401
    )

    return get_registry().count()


def register_core_only() -> int:
    """Register only Bionics-native tools (no UE5)."""
    from bionics_tools import (
        bionics_core,  # noqa: F401
        memory_tools,  # noqa: F401 — always available (no UE5 dep)
        system,  # noqa: F401
        task_tools,  # noqa: F401 — async task manager
    )
    return get_registry().count()


def register_ue5_only() -> int:
    """Register only UE5 tools."""
    from bionics_tools import (
        ue5_actor,  # noqa: F401
        ue5_animgraph,  # noqa: F401 — AnimGraph + BPDoctor
        ue5_animlayer,  # noqa: F401 — Linked Anim Layer
        ue5_asset,  # noqa: F401
        ue5_audio,  # noqa: F401 — Audio import
        ue5_autorig,  # noqa: F401 — fail-closed bone-validate + IKRig (:8090)
        ue5_blueprint,  # noqa: F401
        ue5_controlrig,  # noqa: F401 — Control Rig
        ue5_editor,  # noqa: F401
        ue5_eventgraph,  # noqa: F401 — EventGraph (K2) editing
        ue5_native,  # noqa: F401 — C++ plugin bridge
        ue5_niagara,  # noqa: F401 — Niagara VFX
        ue5_retarget,  # noqa: F401 — native batch retarget (M5 Stage 2, UE5.7 :8090)
        ue5_rigging,  # noqa: F401 — IK Rig + IK Retargeter
        ue5_runtime,  # noqa: F401
        ue5_uasvc,  # noqa: F401 — UE5 Asset Service: native skeletal import (:8090)
    )
    return get_registry().count()
