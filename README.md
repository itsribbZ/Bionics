# Bionics

**An AI agent that controls Unreal Engine 5 with 199 tools.**

> One prompt → AnimGraph wired, Blueprints validated, assets spawned, PIE tested. The animation-pipeline layer no other UE5 AI tool ships.

<!-- TODO: replace with 30-second screen recording of BPDoctor scan + fix_all -->
<!-- ![BPDoctor vs My Tuesday Morning](docs/demos/media/bpdoctor_hero.gif) -->

See **[docs/demos/](docs/demos/)** for the BPDoctor hero GIF (T1.A) and the 4-minute "One Prompt Full Locomotion" demo script (T1.B).

---

## What it does

- **199 tools** across 37 categories — UE5 actors, Blueprints, AnimGraphs, StateTrees, Control Rigs, Niagara, audio, materials, PIE, rigging, retargeting, EventGraph (K2), Linked Anim Layers, async tasks, session progress tracking, divine_powers NL→UE5 entry point
- **Native C++ bridge (BionicsBridge plugin)** — architecturally expected ~5-20ms (in-process JSON-RPC over loopback HTTP) vs ~100-400ms for Python multicast remote-exec; benchmark pending. See `plugins/BionicsBridge/README.md` for the technical breakdown.
- **MCP server** (FastMCP 2.11+ / 3.x) — drops straight into Claude Code, Cursor, Windsurf, or any MCP-aware client. Full MCP 2025-11-25 spec: annotations, `outputSchema` on query tools, async Tasks for long-running ops.
- **BPDoctor** — 34-check static analysis with auto-fix for Blueprint/AnimBP errors (missing MM schema, dead cached poses, unconnected slots, empty state machines, blend-weight sum violations, etc.).
- **Animation pipeline tooling** — Motion Matching schema setup, IK Rig + IK Retargeter creation, batch retargeting of N animations, Linked Anim Layer AnimBPs, Control Rig asset creation and AnimBP binding.
- **Watch Mode** — read-only screen-analysis loop with TTS overlay that explains UE5 systems while you work.
- **Persistent memory with optional sqlite-vec semantic search** — cross-session SQLite memory and proven-sequence warm-starts. Opt-in vector search via `pip install bionics-agent[vector,embeddings_local]`.
- **Sub-agent fan-out** — named `AgentDefinition`s with tool subsets + `dispatch_parallel` for multi-perspective plans, critic ensembles, or parallel research.
- **OpenTelemetry** — opt-in `BIONICS_OTEL_ENABLE=1` emits OTLP spans per tool call.
- **3-tier safety + lifecycle hooks + guardrails** — safe / moderate / destructive, with confirmation gates and PreToolUse / PostToolUse / Stop hooks.

## Highlights (copy-paste ready)

### Bearer-token auth on the C++ bridge

```bash
# C++ plugin auto-generates a 256-bit token and writes it to:
#   <ProjectDir>/.bionics-bridge/instance.json
# The Python side auto-reads that file — you don't have to configure anything.
# To force a known token (CI):
export BIONICS_BRIDGE_TOKEN=my-known-token
```

CORS on the `/bridge` endpoint is locked to `http://127.0.0.1` — browser-origin requests from another page cannot use a stolen token.

### Session resume after a crash

```python
from core.agent import AgentCore
agent = AgentCore(state_machine, safety, capture, executor)
sessions = agent._session.list_sessions()          # all saved sessions
resumable = agent._session.list_running_sessions() # just the ones that crashed mid-run
if resumable:
    agent.resume_from_session(resumable[0]["id"])  # continues exactly where it died
```

### Sub-agent fan-out (parallel multi-perspective)

```python
from core.agent_definitions import AgentDefinition, dispatch_parallel_sync

planner = AgentDefinition(name="planner",  system_prompt="Decompose the task.", tools=["list_tools"])
critic  = AgentDefinition(name="critic",   system_prompt="Find risks in the plan.")

plan, critique = dispatch_parallel_sync(
    [planner, critic],
    ["Build combat locomotion for Sworder:721"],  # broadcast one prompt to both
)
```

### Force Claude to emit a tool call (never free-text)

```python
from core.agent_definitions import AgentDefinition
pinned = AgentDefinition(
    name="structured",
    tools=["my_schema_tool"],
    tool_choice={"type": "tool", "name": "my_schema_tool"},  # or "any" / "required"
)
```

### OTel observability

```bash
# Opt-in, exports OTLP spans per tool call
export BIONICS_OTEL_ENABLE=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # Tempo / Jaeger / any OTLP receiver
python mcp_server.py
```

Span attrs: `bionics.tool.{name,category,safety_tier,ok,elapsed_ms,arg_count}`.

### Vector memory (opt-in)

```bash
pip install -e ".[vector]"           # sqlite-vec
pip install -e ".[embeddings_local]" # + sentence-transformers (~80 MB model)
```

```python
from core.memory import BionicsMemory
from core.embeddings import HashEmbedder          # zero-dep, deterministic
# from core.embeddings import LocalEmbedder       # quality upgrade, 80 MB model

store = BionicsMemory(embedder=HashEmbedder())
store.remember("task_outcome", "ANIMATION", "mm_setup_v1", {"worked": True})
hits = store.search("motion matching locomotion", mode="semantic", limit=5)
# each hit has .distance (lower = closer)
```

### Voyager-style self-verification (retry proven sequences)

```python
from core.tool_cache import get_tool_cache
cache = get_tool_cache()
hit = cache.replay_with_verification(
    topic="ANIMATION",
    prompt="build motion matching for the trooper",
    execute_fn=lambda seq: my_executor.run(seq),   # returns bool
    max_attempts=3,
)
# Each failed sequence has its confidence decayed — stale sequences self-heal out.
```

### Async task manager (long-running UE5 ops)

```python
from core.bridge import ToolGate
gate = ToolGate(); gate.set_bypass_safety(True)
task_id = gate.execute("bionics_task_submit", {"tool_name": "ue5_live_coding", "args": {}}).data["task_id"]
# ...returns immediately, work runs on a thread pool...
status = gate.execute("bionics_task_status", {"task_id": task_id}).data
# When status.status == "completed":
result = gate.execute("bionics_task_result", {"task_id": task_id}).data
```

## Quick start

```bash
# 1. Clone
git clone https://github.com/itsribbZ/Bionics.git
cd Bionics

# 2. Install in editable mode (exposes `bionics`, `bionics-gui`, `bionics-mcp` CLI commands).
#    Requires Python 3.12+ (3.14 tested). Optional extras unlock observability and vector memory.
pip install -e .
# ...or, for a dev contributor install with pytest/ruff/mypy:
# pip install -e ".[dev]"
# ...or with OTel + sqlite-vec enabled:
# pip install -e ".[otel,vector]"

# 3. Set your Anthropic API key
# Windows:  setx ANTHROPIC_API_KEY sk-ant-...
# Linux/Mac: export ANTHROPIC_API_KEY=sk-ant-...

# 4. Copy the config template and set your paths
cp config.yaml.example config.yaml
# (edit config.yaml — at minimum set paths.ue5_project to your .uproject directory)

# 5. Run one of three modes
python main.py                 # PyQt6 GUI (Auto Mode + Watch Mode)
python mcp_server.py           # MCP stdio/HTTP server (for Claude Code etc.)
python cli.py list             # Command-line tool runner
```

**Optional (recommended for UE5 workflows):** build the BionicsBridge C++ plugin into your UE5 project to drop tool-call latency — architecturally expected ~5-20ms (in-process loopback HTTP) vs the ~100-400ms of Python remote-exec; benchmark pending. See [`plugins/BionicsBridge/README.md`](plugins/BionicsBridge/README.md).

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.12+ (3.14 tested) |
| OS | Windows 10/11 (full support). Mac/Linux partial — `pywinauto` and `keyboard` are Windows-only; cross-platform port tracked. |
| UE5 | 5.4+ (5.7 tested). "Python Editor Script Plugin" + "Web Remote Control" must be enabled for the Python fallback path. |
| Anthropic API key | Sonnet 4.5 recommended |
| Visual Studio (optional) | Required only to build the BionicsBridge C++ plugin |

## Connecting to Claude Code

Add Bionics as an MCP server. At your project root (or globally in `~/.claude/.mcp.json`):

```json
{
  "mcpServers": {
    "bionics": {
      "command": "python",
      "args": ["<ABSOLUTE_PATH_TO_BIONICS>/mcp_server.py"],
      "cwd": "<ABSOLUTE_PATH_TO_BIONICS>"
    }
  }
}
```

Restart Claude Code. All 199 tools show up as native tool-use.

## Tool categories

All 37 categories, live-measured from the registry (`register_all()` → 199 tools). Run `python cli.py list` for the full per-tool listing.

| Category | Tools | What it does |
|---|---:|---|
| `input` | 9 | click, type, hotkey, drag, scroll, mouse control |
| `capture` | 3 | screenshot, region capture, monitor listing |
| `vision` | 3 | template match, OCR, wait-for-image |
| `system` | 7 | windows, clipboard, processes, system info |
| `general` | 1 | wait |
| `meta` | 5 | list / describe tools, categories, config, version |
| `plans` | 4 | save / list / load / execute multi-step automation plans |
| `memory` | 7 | persistent cross-session + Voyager tool-cache |
| `tasks` | 6 | async task manager — submit / status / result / cancel / list / clear |
| `audit` | 5 | audit-log tail + session listing / resume / progress |
| `bionics` | 1 | `divine_powers` NL→UE5 entry point |
| `bp_doctor` | 2 | AnimBP Doctor CLI wrapper — locate + scan |
| `ue5_actor` | 17 | spawn / query / delete / modify actors, properties, components, CVars, console |
| `ue5_blueprint` | 15 | Blueprint graph CRUD + interfaces |
| `ue5_asset` | 11 | asset create / save / delete / query + DataAsset bulk-set |
| `ue5_animgraph` | 14 | AnimGraph node create / wire / query, state machines, Linked Anim Layer (C++ plugin) |
| `ue5_eventgraph` | 5 | EventGraph (K2) events, function calls, variable nodes, pin wiring (C++ plugin) |
| `ue5_bpdoctor` | 4 | 34-check Blueprint static analysis + auto-fix |
| `ue5_rigging` | 4 | IK Rig + IK Retargeter + batch retarget |
| `ue5_retarget` | 1 | native batch retarget (UE5.7 `duplicate_and_retarget`) |
| `ue5_autorig` | 2 | fail-closed humanoid autorig + skeleton bone validation |
| `ue5_controlrig` | 3 | Control Rig asset + AnimBP assignment |
| `ue5_niagara` | 2 | VFX emitter spawn + user-exposed param bind |
| `ue5_audio` | 2 | SoundWave import + SoundAttenuation configure |
| `ue5_statetree` | 2 | StateTree inspection + task add |
| `ue5_material` | 4 | material inspect / compile / scalar + vector set |
| `ue5_pie` | 5 | Play-In-Editor start / stop / pause / resume / state |
| `ue5_native` | 11 | C++ bridge status + native actor / asset / CVar / console ops |
| `ue5_runtime` | 7 | level save / load / new, viewport capture, logs, project info, class hierarchy |
| `ue5_python` | 2 | run Python code / file inside the editor |
| `ue5_build` | 2 | Live Coding compile (native bridge + fallback) |
| `ue5_profiling` | 3 | Insights trace start / stop + stat commands |
| `ue5_debug` | 1 | native log tail |
| `ue5_widget` | 2 | widget query + runtime widget listing |
| `ue5_uasvc` | 2 | UE5 Asset Service — preflight + native skeletal import |
| `watch` | 11 | Watch Mode start / stop / pause / resume / task / context / sessions |
| `market` | 14 | MarketBot: PDF parse + Claude-generated posts |

## Architecture

```
                        ┌──────────────────────────┐
  Claude Code / Cursor  │ MCP (stdio / HTTP)       │
  Windsurf / CLI       ─┤ mcp_server.py            │
                        │   199 tools registered    │
                        └──────────┬───────────────┘
                                   │
                                   ▼
                        ┌──────────────────────────┐
                        │ core/bridge.py           │
                        │   ToolRegistry, Gate,     │
                        │   Safety tiers            │
                        └──────────┬───────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
┌───────────────┐    ┌──────────────────────┐    ┌────────────────────┐
│ core/agent.py │    │ UE5 Remote Control   │    │ BionicsBridge      │
│  Auto Mode    │    │ HTTP :30010 (~400ms) │    │ C++ plugin         │
│  Watch Mode   │    │ Python RE socket     │    │ :8090 JSON-RPC     │
│  divine_powers│    │ :9998 (fallback)     │    │ ~5-20ms native     │
└───────────────┘    └──────────────────────┘    └────────────────────┘
```

> Latency figures above (~400ms RE / ~5-20ms native) are architectural estimates, not measured benchmarks — a benchmark is pending.

## Pre-built plans

The `plans/` directory contains automation scripts runnable via `python cli.py run <plan_name>` or the GUI's Plan Library. Most shipped plans reference the Sworder:721 project — use them as templates rather than running them verbatim. New project-agnostic plans are queued on the roadmap.

## Safety

Every destructive tool runs through a 3-tier gate:

| Tier | Examples | Default |
|---|---|---|
| **safe** | `mouse_move`, `screenshot`, `read_screen`, query tools | no confirm |
| **moderate** | `click`, `type_text`, `hotkey`, `open_file` | 1 confirm |
| **destructive** | `ue5_delete_asset`, `delete_actor`, `overwrite_file` | 2 confirms |

Under MCP (no GUI), destructive tools require explicitly setting `BIONICS_MCP_ALLOW_DESTRUCTIVE=1` in the environment.

## Testing

```bash
pytest tests/    # 580+ tests — core modules, tool registry (locked reads + summary), safety, UE5 rigging, memory, integration, OTel, task manager (DESTRUCTIVE gate + future-snapshot wait + auto-evict + clear tool), session (traversal guard), vector memory, sub-agent fan-out (DESTRUCTIVE gate + async-context-safe sync wrapper), Voyager verification
```

## Status

- **Version**: 0.8.4 (2026-05-29) — autorig now builds the 7-chain retarget canon (was 9) with `len(CHAINS)` as the single source of truth, live-verified against UE5 5.7 over the `:8090` bridge; see [CHANGELOG.md](CHANGELOG.md) for the full history. 582-test suite (collect-verified).
- **Stability**: v1.0 hardening in progress (see hellscape audit roadmap)
- **Active development**

## Roadmap

Short term: the internal hellscape audit (memory snapshot at `project_hellscape_audit_2026-04-23.md`) lays out T0 ship blockers, T1 WWZ-launch assets, T2 SOTA 2026 parity (native tool-use protocol, session resumability, lifecycle hooks, vector memory, subagent fan-out, MCP Tasks/Sampling/annotations), T3 moat deepening (Reflexion, zoom-before-click, Process Reward Models, internal eval harness), T4 ecosystem/community.

## Contributing

Contributions welcome. The codebase is split cleanly between:

- `core/` — agent loop, executor, capture, safety, state, memory, verification, bridge
- `bionics_tools/` — tool modules (grouped by category)
- `plugins/BionicsBridge/` — UE5 C++ plugin
- `gui/` — PyQt6 interfaces
- `plans/` — pre-built automation sequences
- `tests/` — pytest suite

Tool authoring is a single `@bionics_tool` decorator — see `bionics_tools/ue5_niagara.py` for a minimal example (2 tools, 192 lines).

## License

All rights reserved. Source available for portfolio review; not licensed for reuse. See [LICENSE](LICENSE).

## Links

- BionicsBridge (C++ plugin, native latency): [`plugins/BionicsBridge/README.md`](plugins/BionicsBridge/README.md)
- Anthropic Claude API: https://docs.anthropic.com
- Model Context Protocol: https://modelcontextprotocol.io
- FastMCP: https://gofastmcp.com
