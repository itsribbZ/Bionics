"""Bionics AutoPlanner - Natural language → research → plan → execute pipeline.

Workflow:
1. User describes what they want in plain English
2. AutoPlanner searches locally (UE5 project, existing scripts, C++ headers)
3. If not enough info, uses Claude for deep research
4. Generates a Bionics-compatible execution plan (JSON)
5. Optionally executes it immediately

Integrates with existing UE5 Python tools (animbp_doctor, etc.) as building blocks.
"""

import json
import logging
import os
import sys
import traceback as _traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic

logger = logging.getLogger("bionics.auto_planner")

# === Telemetry v2 (AAA logging) ===
# v2 schema is event-driven: every divine_powers() run emits a sequence of
# events (start, ecosystem_loaded, doctor_complete, plan_generated,
# step_executed×N, divine_powers_end, outcome_recorded) sharing a run_id.
# Old v1 entries (no schema_version field) remain readable; downstream
# consumers should treat missing schema_version as "1.0.0".
TELEMETRY_SCHEMA_VERSION = "2.0.0"
TELEMETRY_PROMPT_MAX = 4096      # full-text prompt cap per event
TELEMETRY_OUTPUT_MAX = 2048      # per-step stdout/stderr cap
TELEMETRY_ERROR_MSG_MAX = 1024
TELEMETRY_TRACEBACK_MAX = 4096
TELEMETRY_SCRIPT_MAX = 8192      # per-step script_content cap (fine-tune payload)


def _bionics_version() -> str:
    """Read bionics version from pyproject.toml (safe-fail to 'unknown')."""
    try:
        import re
        pp = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pp.exists():
            txt = pp.read_text(encoding="utf-8")
            m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.MULTILINE)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"


BIONICS_VERSION = _bionics_version()


# === Shared telemetry writer integration (T1/shared/telemetry_writer.py) ===
# Routes _telemetry_event calls through the shared TelemetryWriter so blend-master,
# Bionics, and any future code-gen pipeline share one schema source of truth.
# Falls open if the shared module isn't reachable — bionics keeps writing to its
# existing JSONL via the original _telemetry_event code path.
_SHARED_TELEMETRY_OK = False
_SharedTelemetryWriter = None
_shared_cost_row_fn = None
try:
    _shared_path = Path(__file__).resolve().parent.parent.parent / "shared"
    if _shared_path.exists() and str(_shared_path) not in sys.path:
        sys.path.insert(0, str(_shared_path))
    from telemetry_writer import TelemetryWriter as _SharedTelemetryWriter
    from telemetry_writer import cost_row as _shared_cost_row_fn
    _SHARED_TELEMETRY_OK = True
except Exception as _swe:
    sys.stderr.write(f"[bionics telemetry] shared writer unavailable, "
                     f"using local fallback: {_swe}\n")


def _snake(name: str) -> str:
    """CamelCase/PascalCase → snake_case matching UE5 Python binding convention.

    UE5 exposes C++ UFUNCTIONs to Python by converting identifier case.
    `BuildMotionMatchingAnimGraph` → `build_motion_matching_anim_graph`.
    """
    import re
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


PLANNER_SYSTEM_PROMPT = """You are Bionics AutoPlanner, an AI that converts natural language requests into precise, machine-executable automation plans for Unreal Engine 5.

## Your Job
Given a user's request and research context, generate a Bionics execution plan in JSON format.

## Context You Receive
- The user's natural language request
- Relevant local files found by searching the UE5 project (Python scripts, C++ headers, Blueprint paths)
- The user's existing Python tool inventory (Content/Python/) plus the live MCP tool surface
- UE5 project structure and class information

## Execution Methods (choose the most reliable for each step)
1. **ue5_python**: Execute a Python script inside UE5 via Remote Execution bridge (PREFERRED for programmatic operations)
2. **ue5_api**: Use the UE5 Remote Control HTTP API for property get/set
3. **vision**: Use screen vision + mouse/keyboard for UI interactions that can't be scripted
4. **existing_script**: Run one of the user's existing Python tools in Content/Python/

## Output Format
Return ONLY valid JSON:
{
    "name": "Short plan name",
    "description": "What this plan accomplishes",
    "research_summary": "Key findings from local/deep research that informed the plan",
    "steps": [
        {
            "index": 1,
            "description": "Human-readable description",
            "detailed_instructions": "Exact instructions for Bionics to follow",
            "execution_method": "ue5_python|ue5_api|vision|existing_script",
            "script_content": "Python code to execute (for ue5_python method)",
            "existing_script": "filename.py (for existing_script method)",
            "verification": "How to verify this step succeeded",
            "is_destructive": false,
            "requires_app": "Unreal Engine 5",
            "category": "navigation|input|configuration|creation|deletion|verification"
        }
    ],
    "prerequisites": ["List of requirements"],
    "warnings": ["Potential risks"],
    "estimated_time_seconds": 60,
    "rollback_strategy": "How to undo if something goes wrong"
}

## Rules
1. PREFER ue5_python over vision — scripted operations are faster and more reliable
2. Reuse existing Python scripts when they match the task
3. Each step must be independently verifiable
4. Mark destructive steps clearly
5. Include rollback strategy
6. Break complex operations into atomic steps
7. Reference specific file paths, class names, and property names from the research context
"""


from core.paths import get_bible_path, get_design_docs_path, get_ue_knowledge_path

# Map topics to Bible/Docs chapters for targeted search
DIVINE_KNOWLEDGE_MAP = {
    "movement":    ("01_Movement_Traversal", ["Movement", "CMC", "traversal", "jump", "slide", "dodge"]),
    "combat":      ("02_Combat_Weapons", ["Combat", "Weapon", "damage", "melee", "ranged", "gun", "sword"]),
    "animation":   ("03_Animation_Physics", ["Animation", "AnimBP", "AnimGraph", "blend", "montage", "locomotion", "skeleton"]),
    "procgen":     ("04_Procedural_Generation", ["procedural", "PCG", "terrain", "biome", "tile", "generation"]),
    "ai":          ("05_AI_Systems", ["AI", "enemy", "behavior", "patrol", "navmesh", "pathfinding"]),
    "networking":  ("06_Networking_Multiplayer", ["network", "multiplayer", "replication", "server"]),
    "ui":          ("07_UI_UX_HUD", ["UI", "HUD", "widget", "UMG", "menu", "health bar"]),
    "audio":       ("08_Audio_Design", ["audio", "sound", "music", "SFX", "footstep"]),
    "space":       ("09_Space_Travel_Vehicles", ["space", "vehicle", "transport", "travel"]),
    "save":        ("10_Save_Persistence", ["save", "load", "persistence", "checkpoint"]),
    "performance": ("11_Optimization_Performance", ["optimization", "performance", "FPS", "LOD", "cull"]),
    "world":       ("12_World_Systems", ["world", "level", "environment", "EXO", "POI"]),
    "inventory":   ("13_Inventory_Crafting_Economy", ["inventory", "crafting", "item", "loot", "economy"]),
    "quest":       ("14_Quest_Narrative", ["quest", "narrative", "story", "dialogue", "mission"]),
    "bugs":        ("15_Common_Bugs_Roadblocks", ["bug", "fix", "broken", "crash", "T-pose", "error"]),
}


class AutoPlanner:
    """Converts natural language requests into executable Bionics plans.

    Divine Powers mode: searches the Bible, Design System docs, references.json,
    the live MCP tool surface, C++ source, and if not enough — deploys Trinity deep research.

    MVP Doctor integration: accepts structured Diagnosis objects directly via
    plan_from_diagnosis() — no natural language translation needed.
    """

    def __init__(
        self,
        ue5_project_path: str = "",
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
    ):
        self._project_path = Path(ue5_project_path) if ue5_project_path else None
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model
        self._client: Anthropic | None = None
        self._tool_index: dict[str, str] = {}
        self._bible_refs: list[dict] = []
        self._on_log: Callable[[str], None] | None = None
        # Shared telemetry writer (None = falls through to local _telemetry_event impl)
        self._tw = None
        if _SHARED_TELEMETRY_OK and _SharedTelemetryWriter is not None:
            try:
                tel_dir = Path.home() / ".claude" / "telemetry" / "brain"
                self._tw = _SharedTelemetryWriter(
                    skill="bionics",
                    version=BIONICS_VERSION,
                    log_path=tel_dir / "bionics_runs.jsonl",
                    version_field_name="bionics_version",
                )
            except Exception as e:
                self._log(f"shared TelemetryWriter init failed (non-blocking): {e}")
                self._tw = None

    def set_log_callback(self, callback: Callable[[str], None]):
        self._on_log = callback

    def _log(self, msg: str):
        logger.info(msg)
        if self._on_log:
            self._on_log(msg)

    def _get_client(self) -> Anthropic:
        if self._client is None:
            from core.anthropic_client import get_shared_client
            self._client = get_shared_client(self._api_key)
        return self._client

    def set_project_path(self, path: str):
        self._project_path = Path(path)
        self._tool_index = {}

    def load_bible(self) -> list[dict]:
        """Load the Sworder:721 Bible references — the divine knowledge base."""
        bible_path = get_bible_path()
        if not bible_path:
            self._log("Bible path not configured — set paths.bible in config.yaml")
            return []
        refs_path = bible_path / "references.json"
        if refs_path.exists():
            try:
                data = json.loads(refs_path.read_text(encoding="utf-8"))
                self._bible_refs = data.get("references", [])
                self._log(f"Bible loaded: {len(self._bible_refs)} divine references")
            except Exception as e:
                self._log(f"Bible load error: {e}")
                self._bible_refs = []
        return self._bible_refs

    def search_bible(self, query: str) -> list[dict]:
        """Search the Bible references for relevant knowledge."""
        if not self._bible_refs:
            self.load_bible()

        query_lower = query.lower()
        matches = []
        for ref in self._bible_refs:
            searchable = (
                ref.get("key", "") + " " +
                ref.get("value", "") + " " +
                ref.get("context", "") + " " +
                ref.get("category", "")
            ).lower()
            if any(term in searchable for term in query_lower.split()):
                matches.append(ref)

        return matches

    def search_divine_docs(self, query: str) -> list[dict]:
        """Search the Design System docs (the extended Bible) for relevant PDFs."""
        DOCS_PATH = get_design_docs_path()
        if not DOCS_PATH or not DOCS_PATH.exists():
            return []

        query_lower = query.lower()
        results = []

        # Determine which chapters are relevant
        relevant_chapters = []
        for topic, (chapter_dir, keywords) in DIVINE_KNOWLEDGE_MAP.items():
            if any(kw.lower() in query_lower for kw in keywords):
                relevant_chapters.append(chapter_dir)

        # Search relevant chapters for PDFs
        for chapter in relevant_chapters:
            chapter_path = DOCS_PATH / chapter
            if chapter_path.exists():
                for pdf in chapter_path.glob("*.pdf"):
                    results.append({
                        "name": pdf.name,
                        "path": str(pdf),
                        "chapter": chapter,
                    })

        # Also search all PDF names for query terms
        for pdf in DOCS_PATH.rglob("*.pdf"):
            name_lower = pdf.stem.lower().replace("_", " ")
            if any(term in name_lower for term in query_lower.split()):
                if not any(r["path"] == str(pdf) for r in results):
                    results.append({
                        "name": pdf.name,
                        "path": str(pdf),
                        "chapter": pdf.parent.name,
                    })

        self._log(f"Divine docs search: {len(results)} relevant documents found")
        return results

    def index_tools(self) -> dict[str, str]:
        """Index all existing Python tools AND Python-callable C++ helpers in the UE5 project.

        Scans two sources:
          1. `Content/Python/*.py`            — Python scripts (legacy indexing)
          2. `Source/*Editor/**/*.h`          — C++ UFUNCTION(BlueprintCallable, ...) helpers
                                                 that are Python-exposed via `unreal.ClassName.method()`

        The C++ scan catches load-bearing helpers like `SWPoseSearchHelper::BuildMotionMatchingAnimGraph()`
        and `SWAnimBPGenerator::GenerateAnimBP()` which implement the Bible-aligned animation pipeline
        but would be invisible to the Python-only index. Added 2026-04-16 per Phase 2 remediation.
        """
        if not self._project_path:
            return {}

        self._tool_index = {}

        # --- 1. Python scripts in Content/Python/ ---
        python_dir = self._project_path / "Content" / "Python"
        if python_dir.exists():
            for py_file in sorted(python_dir.glob("*.py")):
                try:
                    text = py_file.read_text(encoding="utf-8", errors="ignore")
                    desc = ""
                    lines = text.split("\n")
                    for line in lines[:10]:
                        line = line.strip()
                        if line.startswith('"""') or line.startswith("'''"):
                            desc = line.strip("\"' ")
                            break
                        elif line.startswith("#") and not line.startswith("#!"):
                            desc = line.lstrip("# ")
                            break
                    if not desc:
                        desc = py_file.stem.replace("_", " ").title()
                    self._tool_index[py_file.name] = desc
                except Exception:
                    self._tool_index[py_file.name] = py_file.stem

        # --- 2. C++ helpers in Source/*Editor/**/*.h — Python-exposed via UFUNCTION ---
        source_dir = self._project_path / "Source"
        if source_dir.exists():
            import re
            # Regex for method signature line: return_type MethodName(...
            # Handles: `static bool Foo(`, `UObject* Foo(`, `TArray<FName> Foo(`, `void Foo(`, etc.
            sig_re = re.compile(
                r"^\s*(?:virtual\s+|static\s+|inline\s+|explicit\s+)*"
                r"(?:const\s+)?"
                r"[\w:]+(?:\s*<[^>]+>)?\s*[*&]?\s+"
                r"(\w+)\s*\("
            )
            class_re = re.compile(r"class\s+[A-Z_]+_API\s+(U\w+)")

            for editor_mod in source_dir.glob("*Editor"):
                if not editor_mod.is_dir():
                    continue
                for header in editor_mod.rglob("*.h"):
                    try:
                        text = header.read_text(encoding="utf-8", errors="ignore")
                        class_m = class_re.search(text)
                        class_name = class_m.group(1) if class_m else header.stem
                        lines = text.split("\n")
                        for i, line in enumerate(lines):
                            if "UFUNCTION" not in line:
                                continue
                            if not any(
                                tok in line
                                for tok in ("BlueprintCallable", "BlueprintPure", "Exec")
                            ):
                                continue
                            # Find the next non-empty line (the function signature)
                            for j in range(i + 1, min(i + 5, len(lines))):
                                sig_line = lines[j]
                                if not sig_line.strip():
                                    continue
                                m = sig_re.match(sig_line)
                                if not m:
                                    continue
                                method_name = m.group(1)
                                # Walk backwards for doc comment
                                doc = ""
                                for k in range(i - 1, max(i - 20, -1), -1):
                                    ln = lines[k].strip()
                                    if ln.endswith("*/"):
                                        # Collect doc block
                                        block = []
                                        for kk in range(k, max(k - 20, -1), -1):
                                            bl = lines[kk].strip().lstrip("*/").lstrip("*").strip()
                                            if bl:
                                                block.append(bl)
                                            if lines[kk].strip().startswith("/**"):
                                                break
                                        doc = " ".join(reversed(block)).replace("@brief ", "").strip()
                                        break
                                    if ln.startswith("//"):
                                        doc = ln.lstrip("/ ").strip()
                                        break
                                    if ln and not ln.startswith("*"):
                                        break
                                if not doc:
                                    doc = "C++ helper"
                                # Truncate verbose docs
                                if len(doc) > 160:
                                    doc = doc[:157] + "..."
                                py_call = f"unreal.{class_name}.{_snake(method_name)}()"
                                key = f"{class_name}::{method_name} (C++)"
                                self._tool_index[key] = f"{doc} — Python: {py_call}"
                                break
                    except Exception as e:
                        logger.debug(f"indexer: skipped header {header.name} ({e})")
                        continue

        self._log(f"Indexed {len(self._tool_index)} tools (Python + C++ UFUNCTIONs)")
        return self._tool_index

    def search_local(self, query: str) -> dict:
        """Search the UE5 project locally for relevant files and information."""
        if not self._project_path:
            return {"files": [], "scripts": [], "headers": [], "blueprints": []}

        results = {
            "files": [],
            "scripts": [],
            "headers": [],
            "blueprints": [],
        }
        query_lower = query.lower()
        query_terms = query_lower.split()

        # Toke local LLM pre-filter — cheap S0-S2 factual lookup before Claude.
        # Returns None silently if bridge offline or answer confidence too low.
        local_hint = self._toke_local_lookup(query)
        if local_hint:
            results["toke_local_hint"] = local_hint

        # Search Python scripts
        python_dir = self._project_path / "Content" / "Python"
        if python_dir.exists():
            for py_file in python_dir.glob("*.py"):
                name_lower = py_file.stem.lower()
                if any(term in name_lower for term in query_terms):
                    try:
                        content = py_file.read_text(encoding="utf-8", errors="ignore")
                        results["scripts"].append({
                            "name": py_file.name,
                            "path": str(py_file),
                            "preview": content[:500],
                            "size": len(content),
                        })
                    except Exception as e:
                        logger.debug(f"search_local: skipped script {py_file.name} ({e})")

        # Search C++ headers
        source_dir = self._project_path / "Source"
        if source_dir.exists():
            for h_file in source_dir.rglob("*.h"):
                name_lower = h_file.stem.lower()
                if any(term in name_lower for term in query_terms):
                    try:
                        content = h_file.read_text(encoding="utf-8", errors="ignore")
                        results["headers"].append({
                            "name": h_file.name,
                            "path": str(h_file),
                            "preview": content[:500],
                        })
                    except Exception as e:
                        logger.debug(f"search_local: skipped header name-match {h_file.name} ({e})")

            # Also search header CONTENTS for query terms
            for h_file in source_dir.rglob("*.h"):
                try:
                    content = h_file.read_text(encoding="utf-8", errors="ignore")
                    if any(term in content.lower() for term in query_terms):
                        if not any(r["path"] == str(h_file) for r in results["headers"]):
                            # Find the matching lines
                            matches = []
                            for i, line in enumerate(content.split("\n")):
                                if any(term in line.lower() for term in query_terms):
                                    matches.append(f"L{i+1}: {line.strip()}")
                            if matches:
                                results["headers"].append({
                                    "name": h_file.name,
                                    "path": str(h_file),
                                    "preview": "\n".join(matches[:10]),
                                })
                except Exception as e:
                    logger.debug(f"search_local: skipped header content-match {h_file.name} ({e})")

        self._log(f"Local search for '{query}': {len(results['scripts'])} scripts, {len(results['headers'])} headers")
        return results

    def generate_plan(
        self,
        prompt: str,
        deep_research: bool = False,
        divine: bool = False,
    ) -> dict:
        """Generate a Bionics execution plan from a natural language prompt.

        Args:
            prompt: What the user wants to accomplish
            deep_research: If True, uses more thorough Claude analysis
            divine: If True, searches the Bible and Design System docs first
        """
        self._log(f"{'DIVINE POWERS ACTIVATED' if divine else 'AutoPlanner'}: '{prompt}'")

        # Step 1: Index existing tools
        if not self._tool_index:
            self.index_tools()

        # Step 2: Search the Bible (divine knowledge)
        bible_matches = []
        divine_docs = []
        if divine:
            self._log("Consulting the Bible...")
            bible_matches = self.search_bible(prompt)
            if bible_matches:
                self._log(f"  {len(bible_matches)} divine references found")
            divine_docs = self.search_divine_docs(prompt)
            if divine_docs:
                self._log(f"  {len(divine_docs)} design system documents found")

        # Step 2b: AnimGraph Knowledge Base (if animation-related)
        animgraph_context = ""
        try:
            from ue5_modules.animgraph.knowledge_base import AnimGraphKB
            prompt_lower = prompt.lower()
            anim_keywords = ["animgraph", "anim bp", "animbp", "blend space", "montage",
                             "locomotion", "state machine", "slot", "output pose",
                             "skeleton", "t-pose", "animation"]
            if any(kw in prompt_lower for kw in anim_keywords):
                self._log("AnimGraph KB: querying expert knowledge...")
                # Get relevant nodes
                nodes = AnimGraphKB.get_node_for_task(prompt)
                if nodes:
                    animgraph_context += "ANIMGRAPH EXPERT KNOWLEDGE:\n"
                    animgraph_context += f"  Standard chain: {AnimGraphKB.get_standard_chain()}\n"
                    animgraph_context += f"  Recommended nodes: {', '.join(n.display_name for n in nodes)}\n"
                    for n in nodes:
                        animgraph_context += f"    {n.display_name} (search: '{n.search_name}'): {n.notes}\n"
                # Get relevant rules
                for rule_cat in ["pin_connection", "context_menu", "compilation", "common_mistakes"]:
                    rules = AnimGraphKB.get_rules(rule_cat)
                    if rules:
                        animgraph_context += f"  {rule_cat} rules:\n"
                        for r in rules[:3]:
                            animgraph_context += f"    - {r}\n"
                self._log(f"  AnimGraph KB provided {len(animgraph_context)} chars of expert context")
                confidence_score_bonus = 30
        except Exception:
            # Catch ImportError + any AnimGraphKB API drift (AttributeError, TypeError, etc.)
            # so confidence_score_bonus is always defined before line 498.
            animgraph_context = ""
            confidence_score_bonus = 0

        # Step 3: Local research
        self._log("Searching local project...")
        local_results = self.search_local(prompt)

        # Step 4: Assess confidence — do we have enough info?
        confidence_score = 0
        if bible_matches:
            confidence_score += 30
        if divine_docs:
            confidence_score += 20
        if local_results["scripts"]:
            confidence_score += 25
        if local_results["headers"]:
            confidence_score += 25
        if animgraph_context:
            confidence_score += confidence_score_bonus

        self._log(f"Knowledge confidence: {confidence_score}%")
        needs_trinity = confidence_score < 50 and not deep_research

        if needs_trinity:
            self._log("INSUFFICIENT LOCAL KNOWLEDGE — Trinity deep research recommended")
            deep_research = True  # Escalate to deep research

        # Step 5: Build context for Claude
        context_parts = []

        # Bible references (divine knowledge — highest priority)
        if bible_matches:
            bible_text = ""
            for ref in bible_matches:
                bible_text += f"\n  [{ref['category']}] {ref['key']}: {ref['value']}"
                if ref.get('context'):
                    bible_text += f"\n    Context: {ref['context']}"
                if ref.get('source'):
                    bible_text += f"\n    Source: {ref['source']}"
            context_parts.append(f"BIBLE REFERENCES (authoritative project knowledge):{bible_text}")

        # AnimGraph expert knowledge (highest priority for animation tasks)
        if animgraph_context:
            context_parts.append(animgraph_context)

        # Design system docs
        if divine_docs:
            docs_text = "\n".join(f"  - [{d['chapter']}] {d['name']}" for d in divine_docs[:10])
            context_parts.append(f"DESIGN SYSTEM DOCUMENTS (available for reference):\n{docs_text}")

        # Tool inventory
        if self._tool_index:
            tool_list = "\n".join(f"  - {name}: {desc}" for name, desc in sorted(self._tool_index.items()))
            context_parts.append(f"EXISTING PYTHON TOOLS ({len(self._tool_index)} scripts in Content/Python/):\n{tool_list}")

        # Local search results
        if local_results["scripts"]:
            scripts_text = ""
            for s in local_results["scripts"][:5]:
                scripts_text += f"\n--- {s['name']} ---\n{s['preview']}\n"
            context_parts.append(f"RELEVANT SCRIPTS FOUND:\n{scripts_text}")

        if local_results["headers"]:
            headers_text = ""
            for h in local_results["headers"][:5]:
                headers_text += f"\n--- {h['name']} ---\n{h['preview']}\n"
            context_parts.append(f"RELEVANT C++ HEADERS:\n{headers_text}")

        # UE5 project info
        if self._project_path:
            context_parts.append(f"UE5 PROJECT: {self._project_path}")
            context_parts.append(f"PYTHON DIR: {self._project_path / 'Content' / 'Python'}")

        context = "\n\n".join(context_parts)

        # Step 4: Generate plan via Claude
        # v0.7.6: bumped max_tokens 8192 → 16384 for deep_research after live-fire
        # surfaced an "Unterminated string at char 25697" mid-script_content
        # truncation; Sonnet 4.6 supports up to 64K output tokens, so 16K is a
        # comfortable headroom for plans with multiple multi-line script bodies.
        self._log("Generating execution plan via Claude...")

        client = self._get_client()
        max_tokens = 16384 if deep_research else 4096

        user_message = (
            f"USER REQUEST:\n{prompt}\n\n"
            f"LOCAL RESEARCH RESULTS:\n{context}\n\n"
            f"Generate a Bionics execution plan. Return ONLY valid JSON. "
            f"If a step needs a long script_content, prefer a concise reference "
            f"or split it into multiple steps rather than truncating mid-string."
        )

        def _call_planner_api(msg: str, budget: int):
            return client.messages.create(
                model=self._model,
                max_tokens=budget,
                system=PLANNER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": msg}],
            )

        try:
            response = _call_planner_api(user_message, max_tokens)
        except Exception as e:
            self._log(f"Claude API error: {e}")
            raise RuntimeError(f"AutoPlanner Claude API call failed: {e}") from e

        if not response.content or not hasattr(response.content[0], 'text'):
            raise RuntimeError("Claude returned empty response — no plan generated")

        response_text = response.content[0].text
        stop_reason = getattr(response, "stop_reason", "")

        def _strip_fences(s: str) -> str:
            if "```json" in s:
                return s.split("```json")[1].split("```")[0]
            if "```" in s:
                return s.split("```")[1].split("```")[0]
            return s

        # v0.7.6: retry-on-malformed-JSON with explicit repair prompt.
        # Claude occasionally emits malformed JSON (unterminated strings on
        # max_tokens hit, unescaped quotes inside script_content, trailing
        # commas). Single retry — no infinite loop — with a focused regenerate
        # ask. If retry also fails, raise a structured error including the
        # truncation diagnostic so the divine_powers wrapper can degrade
        # gracefully rather than crashing the whole tool.
        json_str = _strip_fences(response_text).strip()
        try:
            plan = json.loads(json_str)
        except json.JSONDecodeError as e:
            truncated = stop_reason == "max_tokens"
            self._log(
                f"JSON parse error (stop_reason={stop_reason}, truncated={truncated}): {e}"
            )
            self._log("Retrying with repair prompt...")
            repair_msg = (
                f"Your previous response was not valid JSON. Error: {e}. "
                + ("It was also truncated due to max_tokens. Be more concise this time. "
                   if truncated else "")
                + "Regenerate the SAME plan but ensure the JSON is syntactically valid: "
                "balanced braces/brackets, terminated strings, escaped quotes inside "
                "string values (e.g. embedded code with quotes), no trailing commas. "
                "Return ONLY the JSON object — no markdown fences, no commentary.\n\n"
                f"USER REQUEST:\n{prompt}\n\n"
                f"LOCAL RESEARCH RESULTS:\n{context}"
            )
            try:
                retry_response = _call_planner_api(repair_msg, max_tokens)
                retry_text = retry_response.content[0].text
                plan = json.loads(_strip_fences(retry_text).strip())
                self._log("Repair retry succeeded.")
            except (json.JSONDecodeError, Exception) as e2:
                self._log(f"Repair retry also failed: {e2}")
                raise ValueError(
                    f"Failed to generate valid plan after retry "
                    f"(stop_reason={stop_reason}, original_error={e}, retry_error={e2})"
                ) from e

        # Step 5: Save the plan
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = prompt[:40].replace(" ", "_").replace("/", "-").replace("\\", "-")
        from core.paths import PROJECT_ROOT
        plan_path = PROJECT_ROOT / "plans" / f"auto_{timestamp}_{safe_name}.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        self._log(f"Plan generated: {plan.get('name', 'unnamed')} ({len(plan.get('steps', []))} steps)")
        self._log(f"Saved to: {plan_path}")

        return {
            "plan": plan,
            "plan_path": str(plan_path),
            "local_research": local_results,
            "tool_count": len(self._tool_index),
        }

    def _emit_step_event(self, run_id: str | None, step_idx, method: str,
                          description: str, script: str, start_t: float,
                          step_result: dict) -> None:
        """Emit a step_executed telemetry event. No-op if run_id is None.

        Captures the (state, action, result) tuple per step — this is the
        fine-tune training payload. script_content is what the LLM produced;
        output/error is the UE5 verdict.
        """
        if not run_id:
            return
        import time as _t
        duration_ms = int((_t.time() - start_t) * 1000)
        self._telemetry_event(
            run_id, "step_executed",
            step_n=step_idx,
            method=method,
            description=self._telemetry_truncate(description, 500),
            script=self._telemetry_truncate(script, TELEMETRY_SCRIPT_MAX),
            script_length=len(script or ""),
            duration_ms=duration_ms,
            success=step_result.get("success"),
            output=self._telemetry_truncate(step_result.get("output", ""), TELEMETRY_OUTPUT_MAX),
            error=self._telemetry_truncate(step_result.get("error", ""), TELEMETRY_ERROR_MSG_MAX),
            note=step_result.get("note"),
        )

    def _execute_plan_steps(self, plan: dict, bridge, run_id: str | None = None) -> list[dict]:
        """Execute Python/script steps from a plan via UE5 bridge.

        Shared execution logic used by generate_and_execute, diagnose_plan_execute,
        and divine_powers. Uses the public bridge.execute_python (3-strategy fallback).

        Patch-hint detection (v0.7.5): steps whose description starts with
        "[C++ PATCH HINT]" or whose script_content is just comments/whitespace
        are NOT executed — they're recorded with success=None and a note. The
        planner uses this prefix to surface C++ work that requires manual editing
        of source files inside UE5's source tree, not Python execution. Returning
        success=False on these would be misleading (nothing was attempted).

        Empty-error backstop (v0.7.5): when bridge.execute_python returns
        success=False with an empty error string, the result['error'] field is
        synthesized from the output text so the operator has SOME signal about
        why the step failed, instead of an unobservable silent failure.

        Telemetry v2: when run_id is provided, emits a step_executed event per
        step capturing method, description, script (capped), output, error,
        and duration. This is the fine-tune training payload.
        """
        import time as _step_time
        results = []
        for step in plan.get("steps", []):
            method = step.get("execution_method", "")
            idx = step.get("index", "?")
            description = step.get("description", "") or ""
            _step_start = _step_time.time()
            _step_script = step.get("script_content", "") or step.get("existing_script", "") or ""

            if method == "ue5_python" and step.get("script_content"):
                script_content = step["script_content"]

                # v0.7.5 + v0.7.7 patch-hint detection: skip steps the planner
                # emits as C++ patch reminders rather than executable Python.
                # Live-fires showed the planner uses multiple prefix variants
                # depending on prompt context: `[C++ PATCH HINT]` (v0.7.0 audit
                # session) and `[C++ EDIT]` (v0.7.6 verification run). Match
                # any bracketed prefix that signals "C/C++ work, not Python."
                desc_lstrip = description.lstrip()
                is_patch_hint = any(
                    desc_lstrip.startswith(prefix)
                    for prefix in (
                        "[C++ PATCH HINT]",
                        "[C++ PATCH]",
                        "[C++ EDIT]",
                        "[CPP PATCH]",
                        "[CPP EDIT]",
                        "[C PATCH]",
                        "[C EDIT]",
                    )
                )
                if not is_patch_hint:
                    # Strip comments + whitespace to detect "all-comments" content
                    code_lines = [
                        ln for ln in script_content.splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
                    if not code_lines:
                        is_patch_hint = True
                if is_patch_hint:
                    note = "C++ patch hint — manual edit required (no Python executed)"
                    self._log(f"Step {idx}: SKIP (patch hint) - {description[:120]}")
                    results.append({
                        "step": idx,
                        "success": None,
                        "output": "",
                        "note": note,
                    })
                    self._emit_step_event(run_id, idx, method, description,
                                           _step_script, _step_start, results[-1])
                    continue

                self._log(f"Step {idx}: Executing Python in UE5...")
                exec_result = bridge.execute_python(script_content)
                output = exec_result.data.get("output", [])
                if isinstance(output, list):
                    output_text = "\n".join(
                        l.get("output", "") if isinstance(l, dict) else str(l)
                        for l in output
                    ).strip()
                else:
                    output_text = str(output)
                self._log(f"Step {idx}: {'OK' if exec_result.success else 'FAIL'} - {output_text[:200]}")

                # v0.7.5 empty-error backstop
                if not exec_result.success and not exec_result.error:
                    if output_text:
                        synthesized_error = (
                            f"UE5 bridge returned success=False with no error message. "
                            f"Captured output: {output_text[:300]}"
                        )
                    else:
                        synthesized_error = (
                            "UE5 bridge returned success=False with no error message and no output. "
                            "Likely causes: script raised silently, bridge transport dropped the response, "
                            "or script_content evaluated to inert code (no side effects)."
                        )
                else:
                    synthesized_error = exec_result.error if not exec_result.success else ""

                results.append({
                    "step": idx,
                    "success": exec_result.success,
                    "output": output_text[:500],
                    "error": synthesized_error,
                })
                self._emit_step_event(run_id, idx, method, description,
                                       _step_script, _step_start, results[-1])

            elif method == "existing_script" and step.get("existing_script"):
                # Basename-enforce script_name to block path traversal — Claude-generated
                # plans are trusted but not infallible; prevents `../../etc/passwd` patterns.
                script_name = Path(step["existing_script"]).name
                if self._project_path:
                    script_path = self._project_path / "Content" / "Python" / script_name
                else:
                    self._log(f"Step {idx}: No project path set — can't run {script_name}")
                    results.append({"step": idx, "success": False, "error": "No project path"})
                    self._emit_step_event(run_id, idx, method, description,
                                           _step_script, _step_start, results[-1])
                    continue
                self._log(f"Step {idx}: Running {script_name}...")
                exec_result = bridge.execute_python(f"exec(open(r'{script_path}').read())")
                results.append({
                    "step": idx,
                    "success": exec_result.success,
                    "script": script_name,
                    "error": exec_result.error if not exec_result.success else "",
                })
                self._emit_step_event(run_id, idx, method, description,
                                       _step_script, _step_start, results[-1])

            else:
                self._log(f"Step {idx}: {method} requires Bionics agent")
                results.append({"step": idx, "success": None, "note": f"Method '{method}' requires Bionics agent"})
                self._emit_step_event(run_id, idx, method, description,
                                       _step_script, _step_start, results[-1])

        passed = sum(1 for r in results if r.get("success") is True)
        self._log(f"Execution: {passed}/{len(results)} steps passed")
        return results

    def generate_and_execute(
        self,
        prompt: str,
        bridge=None,
        deep_research: bool = False,
        divine: bool = False,
    ) -> dict:
        """Generate a plan and execute Python steps directly via UE5 bridge.

        Args:
            divine: Activate divine powers — consult the Bible first
        """
        result = self.generate_plan(prompt, deep_research, divine)

        if bridge is None or not bridge.is_connected:
            self._log("No UE5 bridge — plan generated but not executed")
            return result

        self._log("Executing Python steps via UE5 bridge...")
        result["execution_results"] = self._execute_plan_steps(result["plan"], bridge)
        return result

    # ------------------------------------------------------------------
    # MVP Doctor integration
    # ------------------------------------------------------------------

    def plan_from_diagnosis(self, diagnosis, divine: bool = True) -> dict:
        """Generate a fix plan directly from an MVP Doctor Diagnosis.

        This is the structured handoff — no natural language translation.
        The diagnosis object's to_planner_prompt() provides a precise,
        machine-readable prompt that maps findings to fix methods.

        Args:
            diagnosis: An mvp_doctor.Diagnosis instance
            divine: Whether to consult Bible/docs for context (default True)

        Returns:
            Same dict as generate_plan() — {plan, plan_path, ...}
        """
        prompt = diagnosis.to_planner_prompt()
        if not prompt or "No fixes needed" in prompt:
            self._log("MVP Doctor: All checks passed — no plan needed")
            return {"plan": {"name": "no_fixes", "steps": []}, "plan_path": ""}

        self._log(f"MVP Doctor -> AutoPlanner: {len(diagnosis.unfixed)} findings to fix")
        return self.generate_plan(prompt, deep_research=True, divine=divine)

    def diagnose_plan_execute(self, doctor, bridge=None, divine: bool = True) -> dict:
        """One-call pipeline: MVPDoctor.diagnose() -> plan -> execute.

        Args:
            doctor: An MVPDoctor instance
            bridge: UE5Bridge for execution (optional)
            divine: Consult Bible/docs

        Returns:
            Dict with diagnosis summary, plan, and execution results
        """
        self._log("Running diagnose -> plan -> execute pipeline...")

        # Step 1: Diagnose
        diagnosis = doctor.diagnose()
        self._log(diagnosis.summary())

        if diagnosis.is_demo_ready:
            self._log("Demo is ready! No fixes needed.")
            return {
                "diagnosis": diagnosis.to_dict(),
                "demo_ready": True,
                "plan": None,
                "execution_results": None,
            }

        # Step 2: Plan
        plan_result = self.plan_from_diagnosis(diagnosis, divine=divine)

        # Step 3: Execute the ALREADY-GENERATED plan (no re-generation)
        if bridge is not None and bridge.is_connected:
            plan = plan_result.get("plan", {})
            plan_result["execution_results"] = self._execute_plan_steps(plan, bridge)

        return {
            "diagnosis": diagnosis.to_dict(),
            "demo_ready": False,
            "plan": plan_result.get("plan"),
            "plan_path": plan_result.get("plan_path"),
            "execution_results": plan_result.get("execution_results"),
        }

    # =====================================================================
    # Pipeline Integration Helpers — Phase 3 (2026-04-16)
    #
    # Wire Bionics into the broader Claude Code ecosystem: Brain telemetry,
    # Toke local LLM pre-filter, ue-knowledge zone loading, Author chains,
    # session_state.json signalling. All methods are SAFE TO FAIL — telemetry
    # and integration failures never block divine_powers() execution.
    # =====================================================================

    # =====================================================================
    # Telemetry v2 — event-driven, AAA logging
    #
    # All writes go to ~/.claude/telemetry/brain/bionics_runs.jsonl as
    # append-only JSON lines. Each line carries schema_version, ts, run_id,
    # event, plus event-specific fields. SAFE TO FAIL — telemetry never
    # blocks divine_powers() execution.
    # =====================================================================

    @staticmethod
    def _telemetry_truncate(s, n: int):
        """Cap a payload string with a clear marker. Returns '' for None."""
        if s is None:
            return ""
        s = s if isinstance(s, str) else json.dumps(s, default=str)
        if len(s) <= n:
            return s
        return s[:n] + f"...[truncated {len(s) - n} chars]"

    def _telemetry_event(self, run_id: str, event: str, **payload) -> bool:
        """Append a v2 telemetry event to bionics_runs.jsonl.

        Returns True on success, False on failure. Never raises.

        Delegates to the shared TelemetryWriter when available (single schema
        source of truth across blend-master + Bionics). Falls back to the local
        writer below if shared is unreachable — bionics keeps working either way.
        """
        if self._tw is not None:
            return self._tw.event(run_id, event, **payload)
        # Fallback: local writer (preserved for shared-unavailable scenarios)
        try:
            tel_dir = Path.home() / ".claude" / "telemetry" / "brain"
            tel_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "ts": datetime.now().isoformat(),
                "run_id": run_id,
                "event": event,
                "pid": os.getpid(),
                "bionics_version": BIONICS_VERSION,
            }
            entry.update(payload)
            path = tel_dir / "bionics_runs.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass  # fsync unsupported on some FS; best-effort
            return True
        except Exception as e:
            self._log(f"Telemetry event '{event}' failed (non-blocking): {e}")
            return False

    # === Bionics-specific auto-scoring (parallel to blend-master's mesh-quality scorer) ===
    # Computes pass/partial/fail verdict from divine_powers run results so the
    # JSONL training corpus has a supervision label for fine-tune. Different
    # checks than blend-master (which scores mesh quality); bionics scores
    # code-gen execution quality.
    _BIONICS_DURATION_BUDGET_MS = 120000   # 2-minute soft ceiling per divine_powers
    _BIONICS_FINDINGS_NOISE_THRESHOLD = 5  # > this many doctor findings = noisy

    def _bionics_compute_auto_score(self, *, success: bool, duration_ms: int,
                                      findings: int, steps_executed: int,
                                      steps_succeeded: int,
                                      error_type: str | None) -> dict:
        """Compute (score, reasons, verdict, checks) for a divine_powers run.

        Score formula matches blend-master's auto_score.py shape so Hesper
        and the fine-tune dataset builder can mine both pipelines uniformly.

        Critical-check fails (cost 0.30 each):
          - bionics_no_exception        — error_type is None
          - bionics_all_steps_succeeded — steps_succeeded == steps_executed

        Other-check fails (cost 0.15 each):
          - bionics_findings_under_noise — findings < _BIONICS_FINDINGS_NOISE_THRESHOLD
          - bionics_under_duration_budget — duration_ms < _BIONICS_DURATION_BUDGET_MS
          - bionics_run_marked_success    — success == True

        Verdict thresholds:
          - pass    : score ≥ 0.85 AND no critical fail AND success
          - partial : score ≥ 0.50
          - fail    : score < 0.50
        """
        checks = {
            "bionics_no_exception": error_type is None,
            "bionics_all_steps_succeeded": (
                steps_executed > 0 and steps_succeeded == steps_executed
            ),
            "bionics_findings_under_noise": findings < self._BIONICS_FINDINGS_NOISE_THRESHOLD,
            "bionics_under_duration_budget": duration_ms < self._BIONICS_DURATION_BUDGET_MS,
            "bionics_run_marked_success": bool(success),
        }
        critical_keys = ("bionics_no_exception", "bionics_all_steps_succeeded")

        score = 1.0
        for k, v in checks.items():
            if v is False:
                score -= 0.30 if k in critical_keys else 0.15
        score = max(0.0, min(1.0, score))

        passes = [k for k, v in checks.items() if v is True]
        fails = [f"FAIL:{k}" for k, v in checks.items() if v is False]
        reasons = passes + fails

        critical_fail = any(checks[k] is False for k in critical_keys)
        if score >= 0.85 and not critical_fail and success:
            verdict = "pass"
        elif score >= 0.50:
            verdict = "partial"
        else:
            verdict = "fail"

        return {
            "score": round(score, 3),
            "reasons": reasons,
            "verdict": verdict,
            "checks": checks,
        }

    # === Cost-row mirror (Aurora ROI signal for bionics divine_powers runs) ===
    # Bionics uses the Claude API for planning + per-step generation. Without
    # per-call token metering yet, conservative would-have-cost estimate is
    # ~$0.05 for a typical multi-step UE5 automation plan (much heavier than
    # blend-master's single-script bpy gen at $0.012). Refine when bionics
    # ships orchestrator-level token telemetry to compare against.
    _BIONICS_RUN_SAVINGS_USD = 0.05

    def _emit_cost_row(self, run_id: str, success: bool, total_duration_ms: int,
                        actual_cost_usd: float = 0.0) -> bool:
        """Mirror this divine_powers run to cost_efficiency.jsonl (Aurora-ready).

        actual_cost_usd defaults to 0.0 until bionics ships per-call API
        metering; would_have_cost_usd is the conservative savings claim.
        """
        if not (_SHARED_TELEMETRY_OK and _shared_cost_row_fn is not None):
            return False
        try:
            return _shared_cost_row_fn(
                agent="bionics_planner",
                run_id=run_id,
                skill="bionics",
                tier="S3",  # divine_powers is S3-tier multi-step planning
                budget_usd=0.50,  # would-be Claude API ceiling per godspeed v4.10
                actual_cost_usd=float(actual_cost_usd),
                would_have_cost_usd=self._BIONICS_RUN_SAVINGS_USD if success else 0.0,
                iterations=1,
                verdict="pass" if success else "fail",
                breach=False,
                extra={
                    "duration_ms": int(total_duration_ms or 0),
                    "model": self._model,
                },
            )
        except Exception as e:
            self._log(f"cost_row failed (non-blocking): {e}")
            return False

    def _bionics_telemetry_start(self, topic: str, prompt: str,
                                  topics_all: list | None = None,
                                  bridge=None) -> str:
        """Emit divine_powers_start event. Returns the run_id.

        v2 schema captures: full prompt (capped), all detected topics,
        project_path, bridge connection state, model, bionics version.
        """
        run_id = f"bionics_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        prompt_str = prompt or ""
        self._telemetry_event(
            run_id, "divine_powers_start",
            topic_primary=topic,
            topics=list(topics_all) if topics_all else [topic],
            prompt=self._telemetry_truncate(prompt_str, TELEMETRY_PROMPT_MAX),
            prompt_length=len(prompt_str),
            project_path=str(self._project_path) if self._project_path else None,
            bridge_connected=bool(
                bridge is not None and getattr(bridge, "is_connected", False)
            ),
            model=self._model,
        )
        return run_id

    def _bionics_telemetry_end(self, run_id: str, success: bool,
                                duration_ms: int, findings: int, steps: int,
                                *,
                                doctor_ms: int = 0, plan_ms: int = 0,
                                exec_ms: int = 0, steps_succeeded: int = 0,
                                error_type: str | None = None,
                                error_message: str | None = None,
                                error_traceback: str | None = None) -> None:
        """Emit divine_powers_end event with timing breakdown + error info.

        Backward-compatible: existing positional call sites still work.
        New kwargs add timing breakdown and exception detail when available.
        """
        # Auto-score the run BEFORE emitting end event (so the order is
        # ... step events ... auto_scored ... divine_powers_end).
        # Supervision label for fine-tune training pairs.
        try:
            _as = self._bionics_compute_auto_score(
                success=success, duration_ms=duration_ms,
                findings=findings, steps_executed=steps,
                steps_succeeded=steps_succeeded, error_type=error_type,
            )
            if self._tw is not None:
                self._tw.emit_auto_score(
                    run_id, _as["score"], _as["reasons"],
                    verdict=_as["verdict"], checks=_as["checks"],
                )
            else:
                self._telemetry_event(
                    run_id, "auto_scored",
                    auto_score=_as["score"],
                    auto_score_reasons=_as["reasons"],
                    verdict=_as["verdict"],
                    checks=_as["checks"],
                )
        except Exception as _ase:
            self._log(f"bionics auto_score failed (non-blocking): {_ase}")

        self._telemetry_event(
            run_id, "divine_powers_end",
            success=success,
            total_duration_ms=duration_ms,
            timing={
                "doctor_ms": doctor_ms,
                "plan_ms": plan_ms,
                "exec_ms": exec_ms,
            },
            findings=findings,
            steps_executed=steps,
            steps_succeeded=steps_succeeded,
            error_type=error_type,
            error_message=self._telemetry_truncate(error_message, TELEMETRY_ERROR_MSG_MAX) if error_message else None,
            error_traceback=self._telemetry_truncate(error_traceback, TELEMETRY_TRACEBACK_MAX) if error_traceback else None,
        )
        # Mirror to cost_efficiency.jsonl (Aurora ROI signal)
        self._emit_cost_row(run_id, success=success, total_duration_ms=duration_ms)

    def _session_state_update(self, phase: str, topic: str = "") -> None:
        """Update ~/.claude/session_state.json so statusline/monitors see Bionics activity.

        Phases: 'running' (divine_powers in flight) | 'idle' (returned).
        Preserves any other keys in session_state.json — only writes the
        'bionics' sub-dict.
        """
        try:
            state_path = Path.home() / ".claude" / "session_state.json"
            state = {}
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8") or "{}")
                except (ValueError, json.JSONDecodeError):
                    state = {}
            state["bionics"] = {
                "phase": phase,
                "topic": topic,
                "ts": datetime.now().isoformat(),
            }
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            self._log(f"Session state update failed (non-blocking): {e}")

    def _toke_local_lookup(self, query: str, timeout_s: int = 30) -> str | None:
        """Route a short factual query through Toke's local LLM (Qwen 2.5 14B).

        Returns the answer string if the bridge routed to local with sufficient
        confidence; returns None if it escalated to Claude (or bridge offline).
        Zero API cost when it hits. Safe for "what is X" / "how does Y work"
        lookups — NOT for S3+ architectural or code-generation work.
        """
        import subprocess
        bridge = Path.home() / ".claude" / "skills" / "godspeed" / "toke_local_bridge.py"
        if not bridge.exists():
            return None
        try:
            result = subprocess.run(
                ["python", str(bridge), "query", query],
                capture_output=True, text=True, timeout=timeout_s,
            )
            if result.returncode != 0:
                return None
            out = json.loads(result.stdout.strip())
            if out.get("routed_to") == "local" and not out.get("is_critical"):
                return out.get("answer")
        except Exception as e:
            self._log(f"Toke local lookup skipped: {e}")
        return None

    # Bionics topic → UE Knowledge zone file (Sworder project reference library).
    _UE_KNOWLEDGE_ZONES = {
        "ANIMATION": "UE5_Animation_Runtime_Reference.md",
        "COMBAT": "UE5_GAS_Abilities_Reference.md",
        "AI": "UE5_AI_BehaviorTree_Reference.md",
        "MOVEMENT": "UE5_Character_Movement_Reference.md",
        "PERFORMANCE": "UE5_Build_Optimization_Reference.md",
        "EXTRACTION": "UE5_World_Partition_Reference.md",
        "ASSET": "UE5_Materials_Rendering_Reference.md",
    }

    def _load_ue_knowledge_context(self, topics) -> dict[str, str]:
        """Load UE Knowledge zone heads for detected topics.

        Returns a dict {topic_name: zone_text_head}. Reads the first 3000 chars
        of each matched zone file — enough to give the planner the authoritative
        UE5 API reference for the topic. Falls back to empty dict if kb root
        doesn't exist (non-Sworder session).
        """
        kb_root = get_ue_knowledge_path()
        if not kb_root or not kb_root.exists():
            return {}
        context = {}
        for topic in topics:
            # Category.value is lowercase (e.g. "animation"); zone map uses uppercase keys.
            raw = topic.value if hasattr(topic, "value") else str(topic)
            topic_name = raw.upper()
            zone_file = self._UE_KNOWLEDGE_ZONES.get(topic_name)
            if not zone_file:
                continue
            zone_path = kb_root / zone_file
            if zone_path.exists():
                try:
                    text = zone_path.read_text(encoding="utf-8", errors="ignore")[:3000]
                    context[topic_name] = text
                except Exception:
                    continue
        return context

    # Bionics topic → Author logical chain (prefix sequence to load in dependency order).
    # Mirrors godspeed Pipeline Router CHAIN-NN mappings. B:N=Bible ch, U:PN=UE Knowledge,
    # M:PN=Model anims, G:N=GDD section, R:N=Roadmap.
    _AUTHOR_CHAINS = {
        "ANIMATION": ["B:11", "U:P5", "M:P16", "M:P15", "M:P10"],
        "COMBAT": ["G:8", "B:8", "B:1", "U:P4", "U:P5", "U:P3", "U:P13", "B:11"],
        "AI": ["G:2", "B:1", "U:P15", "U:P14", "U:P5", "U:P13", "B:2", "B:11"],
        "MOVEMENT": ["U:P3", "B:11"],
        "EXTRACTION": ["B:5", "U:P17", "U:P13", "B:11"],
        "PERFORMANCE": ["R:7", "B:6", "B:13", "U:P18", "B:11"],
        "ASSET": ["G:3", "M:P14", "M:P15", "U:P6", "B:11"],
    }

    def _load_author_chain(self, topics) -> list[str]:
        """Return Author chain prefixes in dependency order for given topics.

        Dedupes while preserving order across multi-topic prompts.
        Pure data — doesn't read files. Chain execution (Bible reads etc.)
        remains the planner's job; this just surfaces the canonical order.
        """
        chain = []
        seen = set()
        for topic in topics:
            raw = topic.value if hasattr(topic, "value") else str(topic)
            topic_name = raw.upper()
            for prefix in self._AUTHOR_CHAINS.get(topic_name, []):
                if prefix not in seen:
                    seen.add(prefix)
                    chain.append(prefix)
        return chain

    # -------------------------------------------------------------------
    # Phase 4 wiring helpers — persistent memory + Voyager cache
    # Called from divine_powers() automatically; safe-to-fail everywhere.
    # -------------------------------------------------------------------

    def _query_voyager_cache(self, topic: str, prompt: str) -> dict:
        """Look up proven tool sequences for this prompt. Returns warm-start dict.

        Shape: {"proven": [list of sequences], "similar": [fallback fuzzy matches]}.
        Safe — returns empty dict on any failure (cache unavailable, SQLite locked).
        """
        try:
            from core.tool_cache import get_tool_cache
            cache = get_tool_cache()
            proven = cache.find_proven(topic, prompt, min_success_count=2, limit=3)
            similar = cache.find_similar(prompt, limit=3) if not proven else []
            return {"proven": proven, "similar": similar}
        except Exception as e:
            self._log(f"Voyager cache query failed (non-blocking): {e}")
            return {"proven": [], "similar": []}

    def _record_divine_outcome(self, run_id: str, topic: str, prompt: str,
                                topics: list, demo_ready: bool,
                                findings: int, steps: int, duration_ms: int,
                                plan: dict, execution_results: list,
                                success: bool) -> None:
        """Persist divine_powers() run outcome to memory + Voyager cache.

        Writes two records:
          1. memory[task_outcome / topic / run_id] = {full run summary}
          2. tool_cache[topic, prompt, sequence] = records the plan's tool calls

        Telemetry v2: also emits outcome_recorded event with the success flags
        of both writes so downstream consumers can verify persistence.
        """
        _memory_ok = False
        _memory_err = None
        _cache_ok = False
        _cache_err = None
        try:
            from core.memory import get_memory
            mem = get_memory()
            mem.remember("task_outcome", topic, run_id, {
                "ts": datetime.now().isoformat(),
                "prompt_head": prompt[:200],
                "topics": topics,
                "demo_ready": demo_ready,
                "findings_count": findings,
                "steps_executed": steps,
                "duration_ms": duration_ms,
                "success": success,
            })
            _memory_ok = True
        except Exception as e:
            _memory_err = str(e)
            self._log(f"Memory write failed (non-blocking): {e}")

        # Voyager cache — compact sequence summary
        try:
            from core.tool_cache import get_tool_cache
            cache = get_tool_cache()
            if plan and plan.get("steps"):
                sequence = []
                for i, step in enumerate(plan.get("steps", [])):
                    step_result = (execution_results[i]
                                   if i < len(execution_results) else {})
                    sequence.append({
                        "method": step.get("execution_method", "unknown"),
                        "description": step.get("description", "")[:120],
                        "success": bool(step_result.get("success"))
                                   if step_result else None,
                    })
                cache.record(topic, prompt, sequence,
                             success=success, duration_ms=duration_ms,
                             confidence=1.0 if success else 0.3)
                _cache_ok = True
            else:
                _cache_ok = True  # nothing to record, count as OK
        except Exception as e:
            _cache_err = str(e)
            self._log(f"Tool cache record failed (non-blocking): {e}")

        # Telemetry v2: outcome_recorded — durable-write confirmation
        self._telemetry_event(
            run_id, "outcome_recorded",
            memory_write_ok=_memory_ok,
            memory_error=self._telemetry_truncate(_memory_err, 500) if _memory_err else None,
            tool_cache_write_ok=_cache_ok,
            tool_cache_error=self._telemetry_truncate(_cache_err, 500) if _cache_err else None,
        )

    def divine_powers(self, prompt: str, bridge=None) -> dict:
        """The unified divine powers pipeline.

        Routes a natural language prompt through the full stack:
          1. Detect topics from prompt (AnimBP? Combat? AI? Movement?)
          2. Run MVP Doctor with targeted checks for those topics
          3. Generate a fix plan from Doctor findings + Bible/docs context
          4. Execute fixes via UE5 bridge
          5. Return results

        This is the single entry point for: Prompt -> Doctor -> Blueprint -> Bionics -> Done.

        Args:
            prompt: Natural language request (e.g. "fix the AnimBP T-pose")
            bridge: UE5Bridge instance (for live checks + execution)
        """
        import time as _time

        from core.mvp_doctor import MVPDoctor

        _start_time = _time.time()
        self._log(f"DIVINE POWERS: '{prompt}'")

        # Step 1: Detect what this prompt is about
        topics = MVPDoctor.detect_topics(prompt)
        topic_names = [t.value for t in topics]
        self._log(f"Topics detected: {topic_names}")

        # --- Phase 3 wiring: telemetry start + session state + ecosystem context ---
        _primary_topic = topic_names[0] if topic_names else "GENERAL"
        _run_id = self._bionics_telemetry_start(
            _primary_topic, prompt,
            topics_all=topic_names, bridge=bridge,
        )
        self._session_state_update("running", _primary_topic)

        # Load canonical engine context for the planner (additive — never blocks)
        _ue_kb_context = self._load_ue_knowledge_context(topics)
        _author_chain = self._load_author_chain(topics)
        if _author_chain:
            self._log(f"Author chain: {' -> '.join(_author_chain)}")
        if _ue_kb_context:
            self._log(f"UE Knowledge zones loaded: {list(_ue_kb_context.keys())}")

        # --- Phase 4 wiring: Voyager warm-start lookup ---
        _warm_start = self._query_voyager_cache(_primary_topic, prompt)
        if _warm_start["proven"]:
            self._log(f"Voyager cache: {len(_warm_start['proven'])} proven sequences available for warm-start")
        elif _warm_start["similar"]:
            self._log(f"Voyager cache: {len(_warm_start['similar'])} similar runs (below proven threshold)")

        # Telemetry v2: emit ecosystem_loaded — captures which KB layers + cache
        # state shaped this run. Critical for downstream root-cause analysis.
        self._telemetry_event(
            _run_id, "ecosystem_loaded",
            author_chain=_author_chain or [],
            ue_kb_zones=list(_ue_kb_context.keys()) if _ue_kb_context else [],
            ue_kb_bytes=sum(len(v or "") for v in (_ue_kb_context or {}).values()),
            voyager_proven_count=len(_warm_start.get("proven", [])),
            voyager_similar_count=len(_warm_start.get("similar", [])),
        )

        _success = False
        _findings_count = 0
        _steps_count = 0
        _steps_succeeded = 0
        _doctor_ms = 0
        _plan_ms = 0
        _exec_ms = 0
        _error_type = None
        _error_message = None
        _error_traceback = None
        try:
            # Step 2: Run Doctor with targeted checks
            _doctor_start = _time.time()
            doctor = MVPDoctor(
                ue5_project_path=str(self._project_path) if self._project_path else "",
                ue5_bridge=bridge,
            )
            diagnosis = doctor.diagnose(categories=topics)
            _doctor_ms = int((_time.time() - _doctor_start) * 1000)
            self._log(diagnosis.summary())
            _findings_count = len(diagnosis.findings) if hasattr(diagnosis, "findings") else 0

            # Telemetry v2: doctor_complete — findings + unfixed + demo_ready
            _findings_summary = []
            if hasattr(diagnosis, "findings"):
                for _f in (diagnosis.findings or [])[:20]:
                    _findings_summary.append({
                        "category": str(getattr(_f, "category", "") or ""),
                        "severity": str(getattr(_f, "severity", "") or ""),
                        "issue": self._telemetry_truncate(
                            str(getattr(_f, "issue", "")
                                or getattr(_f, "message", "")
                                or getattr(_f, "description", "")),
                            300,
                        ),
                    })
            self._telemetry_event(
                _run_id, "doctor_complete",
                duration_ms=_doctor_ms,
                findings_count=_findings_count,
                unfixed_count=len(diagnosis.unfixed) if hasattr(diagnosis, "unfixed") else 0,
                demo_ready=bool(getattr(diagnosis, "is_demo_ready", False)),
                findings_summary=_findings_summary,
            )

            # Step 3: Generate plan with enriched ecosystem context
            doctor_prompt = diagnosis.to_planner_prompt()

            # Build ecosystem context block — canonical engine + KB routing
            ecosystem_context = ""
            if _author_chain:
                ecosystem_context += f"\nAUTHOR CHAIN (canonical KB load order): {' -> '.join(_author_chain)}\n"
            if _ue_kb_context:
                ecosystem_context += "\nUE KNOWLEDGE (authoritative engine reference):\n"
                for topic, snippet in _ue_kb_context.items():
                    ecosystem_context += f"\n--- {topic} zone head ---\n{snippet[:1500]}\n"

            combined_prompt = (
                f"USER REQUEST: {prompt}\n\n"
                f"{doctor_prompt}\n"
                f"{ecosystem_context}\n"
                f"Fix the issues found by the Doctor AND fulfill the user's request. "
                f"Doctor findings are current-state. Author chain is the canonical "
                f"Bible/UE Knowledge load order. UE Knowledge zone heads are the "
                f"authoritative engine reference. Prioritize what the user asked for."
            )

            _plan_start = _time.time()
            _deep_research_used = bool(diagnosis.unfixed)
            if diagnosis.unfixed:
                self._log(f"Doctor found {len(diagnosis.unfixed)} issues - generating fix plan...")
                plan_result = self.generate_plan(combined_prompt, deep_research=True, divine=True)
            else:
                self._log("Doctor found no issues - generating plan from prompt only...")
                plan_result = self.generate_plan(prompt, deep_research=False, divine=True)
            _plan_ms = int((_time.time() - _plan_start) * 1000)

            # Telemetry v2: plan_generated — plan-step summary (the action set)
            _plan_steps = ((plan_result or {}).get("plan") or {}).get("steps", []) or []
            _plan_summary = [
                {
                    "step_n": s.get("index", i + 1),
                    "method": s.get("execution_method"),
                    "description": self._telemetry_truncate(s.get("description", ""), 200),
                    "has_script": bool(s.get("script_content") or s.get("existing_script")),
                }
                for i, s in enumerate(_plan_steps[:50])
            ]
            self._telemetry_event(
                _run_id, "plan_generated",
                duration_ms=_plan_ms,
                plan_path=plan_result.get("plan_path"),
                step_count=len(_plan_steps),
                deep_research=_deep_research_used,
                plan_summary=_plan_summary,
            )

            # Step 4: Execute (if bridge available) — emits step_executed per step
            execution_results = []
            if bridge is not None and bridge.is_connected:
                _exec_start = _time.time()
                execution_results = self._execute_plan_steps(
                    plan_result.get("plan", {}), bridge, run_id=_run_id,
                )
                _exec_ms = int((_time.time() - _exec_start) * 1000)
            _steps_count = len(execution_results)
            _steps_succeeded = sum(1 for r in execution_results if r.get("success") is True)
            _success = True

            return {
                "prompt": prompt,
                "topics": topic_names,
                "diagnosis": diagnosis.to_dict(),
                "plan": plan_result.get("plan"),
                "plan_path": plan_result.get("plan_path"),
                "execution_results": execution_results,
                "demo_ready": diagnosis.is_demo_ready,
                "run_id": _run_id,
                "ecosystem_context": {
                    "ue_knowledge_zones": list(_ue_kb_context.keys()),
                    "author_chain": _author_chain,
                    "voyager_warm_start": _warm_start,
                },
            }
        except Exception as _exc:
            # Capture exception detail for the end event, then re-raise so
            # callers still see the failure. SAFE: telemetry write is best-effort.
            _error_type = type(_exc).__name__
            _error_message = str(_exc)
            _error_traceback = _traceback.format_exc()
            raise
        finally:
            # Telemetry end + session state clear fires on every exit path
            _duration_ms = int((_time.time() - _start_time) * 1000)
            self._bionics_telemetry_end(
                _run_id, _success, _duration_ms,
                _findings_count, _steps_count,
                doctor_ms=_doctor_ms, plan_ms=_plan_ms, exec_ms=_exec_ms,
                steps_succeeded=_steps_succeeded,
                error_type=_error_type, error_message=_error_message,
                error_traceback=_error_traceback,
            )
            self._session_state_update("idle", _primary_topic)

            # Phase 4 wiring: persist outcome to memory + Voyager cache
            try:
                _plan = locals().get("plan_result", {}).get("plan") if _success else None
                _exec = locals().get("execution_results", []) if _success else []
                self._record_divine_outcome(
                    run_id=_run_id, topic=_primary_topic, prompt=prompt,
                    topics=topic_names,
                    demo_ready=bool(locals().get("diagnosis")
                                    and locals()["diagnosis"].is_demo_ready),
                    findings=_findings_count, steps=_steps_count,
                    duration_ms=_duration_ms, plan=_plan or {},
                    execution_results=_exec, success=_success,
                )
            except Exception as _e:
                self._log(f"Outcome persistence skipped: {_e}")
