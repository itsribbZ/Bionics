"""Bionics MVP Doctor — Diagnoses what blocks a playable demo and feeds fixes to AutoPlanner.

Pipeline:
    doctor = MVPDoctor(ue5_project, ue5_bridge)
    diagnosis = doctor.diagnose()        # structured findings
    # Feed to AutoPlanner:
    plan = auto_planner.plan_from_diagnosis(diagnosis)
    # Or run the full loop:
    report = doctor.diagnose_and_fix(auto_planner, bridge)

Checks are organized by the demo exit criteria:
    1. Movement feels responsive AND weighty
    2. Sword combat feels satisfying in grey box
    3. Player completes full extract: spawn -> fight -> loot -> extract -> rewards
    4. Zero compile warnings on UE 5.7.4
    5. SWHealthChecks pass

Each check produces a Finding with severity, category, description, and a
machine-readable fix hint that AutoPlanner can consume directly.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("bionics.mvp_doctor")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Severity(Enum):
    CRITICAL = auto()   # Blocks demo entirely
    HIGH = auto()       # Major functionality broken
    MEDIUM = auto()     # Works but wrong/suboptimal
    LOW = auto()        # Cosmetic or minor
    INFO = auto()       # Informational, not a problem


class Category(Enum):
    COMPILE = "compile"
    WIRING = "wiring"             # Systems not connected to each other
    ASSET = "asset"               # Missing/misconfigured assets
    EDITOR = "editor"             # Level/editor-side issues
    AI = "ai"                     # Enemy behavior
    COMBAT = "combat"
    MOVEMENT = "movement"
    EXTRACTION = "extraction"
    HEALTH_CHECK = "health_check" # SWHealthChecks results
    PERFORMANCE = "performance"
    ANIMATION = "animation"       # AnimBP, BlendSpace, montages, skeleton


class FixMethod(Enum):
    """How Bionics should fix this finding."""
    CPP_EDIT = "cpp_edit"           # Edit C++ source file
    UE5_PYTHON = "ue5_python"       # Run Python in UE5 editor
    UE5_API = "ue5_api"             # UE5 Remote Control API call
    EDITOR_MANUAL = "editor_manual" # Needs manual editor interaction
    RECOMPILE = "recompile"         # Just needs a recompile
    NONE = "none"                   # Informational only


@dataclass
class Finding:
    """A single diagnostic finding."""
    id: str                         # e.g. "ENEMY_NO_STATETREE"
    title: str                      # Human-readable one-liner
    description: str                # Detailed explanation
    severity: Severity
    category: Category
    fix_method: FixMethod = FixMethod.NONE
    fix_hint: str = ""              # Machine-readable fix instruction for AutoPlanner
    file_path: str = ""             # Relevant source file (if any)
    line_number: int = 0            # Line in source file (if any)
    auto_fixable: bool = False      # Can Bionics fix this without user approval?
    fixed: bool = False             # Set to True after successful fix

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.name,
            "category": self.category.value,
            "fix_method": self.fix_method.value,
            "fix_hint": self.fix_hint,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "auto_fixable": self.auto_fixable,
            "fixed": self.fixed,
        }

    def to_planner_prompt(self) -> str:
        """Convert to a natural language prompt that AutoPlanner can consume."""
        parts = [f"[{self.severity.name}] {self.title}: {self.description}"]
        if self.fix_hint:
            parts.append(f"Suggested fix: {self.fix_hint}")
        if self.file_path:
            loc = self.file_path
            if self.line_number:
                loc += f":{self.line_number}"
            parts.append(f"Location: {loc}")
        return "\n".join(parts)


@dataclass
class Diagnosis:
    """Complete diagnostic result."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    findings: list[Finding] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def fixable_count(self) -> int:
        return sum(1 for f in self.findings if f.auto_fixable and not f.fixed)

    @property
    def unfixed(self) -> list[Finding]:
        return [f for f in self.findings if not f.fixed]

    @property
    def is_demo_ready(self) -> bool:
        """True if no CRITICAL or HIGH findings remain unfixed."""
        return all(
            f.fixed or f.severity not in (Severity.CRITICAL, Severity.HIGH)
            for f in self.findings
        )

    def summary(self) -> str:
        sev_counts = {}
        for f in self.findings:
            sev_counts[f.severity.name] = sev_counts.get(f.severity.name, 0) + 1
        lines = [
            f"MVP DOCTOR DIAGNOSIS — {self.timestamp}",
            f"Checks: {self.checks_run} run, {self.checks_passed} passed",
            f"Findings: {len(self.findings)} total ({', '.join(f'{v} {k}' for k, v in sev_counts.items())})",
            f"Auto-fixable: {self.fixable_count}",
            f"Demo ready: {'YES' if self.is_demo_ready else 'NO'}",
        ]
        if not self.is_demo_ready:
            blockers = [f for f in self.unfixed if f.severity in (Severity.CRITICAL, Severity.HIGH)]
            lines.append(f"Blockers ({len(blockers)}):")
            for b in blockers:
                lines.append(f"  [{b.severity.name}] {b.id}: {b.title}")
        return "\n".join(lines)

    def to_planner_prompt(self) -> str:
        """Convert entire diagnosis to a prompt for AutoPlanner."""
        if not self.unfixed:
            return "All checks passed. No fixes needed."

        parts = [
            "MVP DOCTOR DIAGNOSIS — Fix the following issues to reach a playable demo:\n",
            f"Total findings: {len(self.unfixed)} unfixed\n",
        ]

        # Group by severity, highest first
        for sev in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW):
            group = [f for f in self.unfixed if f.severity == sev]
            if group:
                parts.append(f"\n--- {sev.name} ({len(group)}) ---")
                for finding in group:
                    parts.append(finding.to_planner_prompt())
                    parts.append("")

        parts.append(
            "\nGenerate a Bionics execution plan that fixes these issues. "
            "Prioritize CRITICAL first, then HIGH. "
            "Use ue5_python for editor fixes, existing_script for known tools, "
            "and cpp_edit hints for source changes that need recompilation."
        )
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "demo_ready": self.is_demo_ready,
            "findings": [f.to_dict() for f in self.findings],
        }

    def save(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        logger.info(f"Diagnosis saved to {path}")


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

CheckFunc = Callable[["MVPDoctor"], list[Finding]]

_CHECK_REGISTRY: list[tuple[str, Category, CheckFunc]] = []


def check(name: str, category: Category):
    """Decorator to register a diagnostic check."""
    def decorator(func: CheckFunc) -> CheckFunc:
        _CHECK_REGISTRY.append((name, category, func))
        return func
    return decorator


# ---------------------------------------------------------------------------
# MVP Doctor
# ---------------------------------------------------------------------------

class MVPDoctor:
    """Diagnoses what blocks a playable Sworder:721 demo.

    Works in two modes:
    1. Static analysis — scans C++ source for wiring gaps, missing includes, etc.
    2. Live analysis — queries UE5 via bridge for runtime/editor state

    Args:
        ue5_project_path: Path to MyProject/ (the UE5 project root)
        ue5_bridge: Optional UE5Bridge instance for live checks
    """

    def __init__(
        self,
        ue5_project_path: str = "",
        ue5_bridge=None,
    ):
        if not ue5_project_path:
            from core.paths import get_ue5_project
            _p = get_ue5_project()
            ue5_project_path = str(_p) if _p else ""
        self._project = Path(ue5_project_path) if ue5_project_path else Path(".")
        self._source = self._project / "Source" / "MyProject"
        self._content = self._project / "Content"
        self._bridge = ue5_bridge
        self._on_log: Callable[[str], None] | None = None

        # Cache for file reads (avoid re-reading same file)
        self._file_cache: dict[str, str] = {}

    def set_log_callback(self, callback: Callable[[str], None]):
        self._on_log = callback

    def _log(self, msg: str):
        logger.info(msg)
        if self._on_log:
            self._on_log(msg)

    def _read_file(self, path: Path) -> str:
        """Read a source file with caching."""
        key = str(path)
        if key not in self._file_cache:
            try:
                self._file_cache[key] = path.read_text(encoding="utf-8", errors="ignore")
            except FileNotFoundError:
                self._file_cache[key] = ""
        return self._file_cache[key]

    def _grep_source(self, pattern: str, extensions: tuple = (".h", ".cpp")) -> list[tuple[Path, int, str]]:
        """Search source files for a regex pattern. Returns [(path, line_num, line_text)]."""
        results = []
        regex = re.compile(pattern, re.IGNORECASE)
        for ext in extensions:
            for f in self._source.rglob(f"*{ext}"):
                # Skip backup files
                if ".pre_demo_backup" in f.name:
                    continue
                content = self._read_file(f)
                for i, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        results.append((f, i, line.strip()))
        return results

    def _file_contains(self, path: Path, text: str) -> bool:
        """Check if a file contains a substring."""
        return text in self._read_file(path)

    def _find_line(self, path: Path, text: str) -> int:
        """Find the line number of first occurrence of text in a file. Returns 0 if not found."""
        content = self._read_file(path)
        for i, line in enumerate(content.split("\n"), 1):
            if text in line:
                return i
        return 0

    # ------------------------------------------------------------------
    # Diagnostic checks
    # ------------------------------------------------------------------

    @check("DirectionalLight Mobility", Category.EDITOR)
    def check_directional_light(self) -> list[Finding]:
        """Check if DirectionalLight is spawned as Movable (not Static)."""
        findings = []
        exo_gm = self._source / "EXO" / "EXOGameMode.cpp"
        content = self._read_file(exo_gm)

        # Check if MoonLight is spawned but mobility isn't set to Movable
        if "SpawnActor<ADirectionalLight>" in content:
            if "SetMobility(EComponentMobility::Movable)" not in content:
                findings.append(Finding(
                    id="DIRLIGHT_STATIC",
                    title="DirectionalLight spawned without Movable mobility",
                    description=(
                        "MoonLight in EXOGameMode spawns a DirectionalLight but doesn't "
                        "set mobility to Movable. The editor-placed light may also be Static. "
                        "This spams 'Static light updated every frame' in PIE logs."
                    ),
                    severity=Severity.HIGH,
                    category=Category.EDITOR,
                    fix_method=FixMethod.UE5_PYTHON,
                    fix_hint=(
                        "Run Python in UE5: find all DirectionalLight actors in the level, "
                        "set their root component mobility to EComponentMobility::Movable, "
                        "then save the level. Also add SetMobility(Movable) after spawn in C++."
                    ),
                    file_path=str(exo_gm),
                    auto_fixable=True,
                ))

        # Also check if there's a runtime fix but the level actor is still static
        # (This is the known issue from project_status.md)
        if "SetMobility" in content and "Movable" in content:
            findings.append(Finding(
                id="DIRLIGHT_LEVEL_STATIC",
                title="DirectionalLight: runtime fix exists but level actor may still be Static",
                description=(
                    "C++ sets mobility at runtime, but the EDITOR-placed actor in EXO_001 "
                    "level is still Static. This fires a warning every tick in PIE."
                ),
                severity=Severity.MEDIUM,
                category=Category.EDITOR,
                fix_method=FixMethod.UE5_PYTHON,
                fix_hint=(
                    "Run Python in UE5: iterate all ADirectionalLight actors via "
                    "unreal.EditorLevelLibrary.get_all_level_actors(), set mobility to Movable, "
                    "then unreal.EditorLevelLibrary.save_current_level()."
                ),
                file_path=str(exo_gm),
                auto_fixable=True,
            ))

        return findings

    @check("Enemy AI StateTree", Category.AI)
    def check_enemy_statetree(self) -> list[Finding]:
        """Check if enemies have a StateTree asset assigned."""
        findings = []
        ai_ctrl = self._source / "Variant_Combat" / "AI" / "SWAIController.cpp"
        content = self._read_file(ai_ctrl)

        # The controller has StateTreeAI component — check if it loads a StateTree asset
        has_statetree_component = "UStateTreeAIComponent" in content or "StateTreeAI" in content
        loads_statetree_asset = "SetStateTreeAsset" in content or "StateTreeAsset" in content

        if has_statetree_component and not loads_statetree_asset:
            findings.append(Finding(
                id="ENEMY_NO_STATETREE",
                title="SWAIController has StateTree component but no asset assigned",
                description=(
                    "SWAIController creates a UStateTreeAIComponent but never sets a "
                    "StateTree asset on it. Enemies spawn but have no AI behavior — "
                    "they just stand idle."
                ),
                severity=Severity.CRITICAL,
                category=Category.AI,
                fix_method=FixMethod.UE5_PYTHON,
                fix_hint=(
                    "Check if a StateTree asset exists in Content/AI/. If not, create a basic "
                    "patrol+chase+attack StateTree via Python. Then either: "
                    "(a) set it in SWAIController::OnPossess via EnemyData->StateTreeAsset, or "
                    "(b) set it as the default on the StateTreeAI component in the constructor."
                ),
                file_path=str(ai_ctrl),
                auto_fixable=False,  # Needs a StateTree asset created
            ))

        # Check if EnemyDataAsset has a StateTree reference
        data_asset_h = self._source / "Variant_Combat" / "AI" / "SWEnemyDataAsset.h"
        da_content = self._read_file(data_asset_h)
        if da_content and "StateTree" not in da_content:
            findings.append(Finding(
                id="ENEMY_DATA_NO_STATETREE",
                title="SWEnemyDataAsset has no StateTree reference",
                description=(
                    "The enemy data asset doesn't have a UStateTree* property. "
                    "Enemies can't be data-driven for AI behavior without this."
                ),
                severity=Severity.MEDIUM,
                category=Category.AI,
                fix_method=FixMethod.CPP_EDIT,
                fix_hint=(
                    "Add to SWEnemyDataAsset.h: "
                    "UPROPERTY(EditAnywhere, Category=\"AI\") "
                    "TObjectPtr<UStateTree> StateTreeAsset;"
                ),
                file_path=str(data_asset_h),
                auto_fixable=False,
            ))

        return findings

    @check("Death-to-Extraction Wiring", Category.WIRING)
    def check_death_extraction_wiring(self) -> list[Finding]:
        """Check if OnCharacterDied is wired to ExtractionManager::HandlePlayerDeath."""
        findings = []
        exo_gm = self._source / "EXO" / "EXOGameMode.cpp"
        content = self._read_file(exo_gm)

        # Check if HandlePlayerDeath is called/bound anywhere in the game mode
        has_death_binding = (
            "HandlePlayerDeath" in content
            or "OnCharacterDied" in content
            or "OnDied" in content
        )

        if not has_death_binding:
            findings.append(Finding(
                id="DEATH_NOT_WIRED",
                title="Player death not wired to ExtractionManager",
                description=(
                    "EXOGameMode doesn't bind OnCharacterDied/OnDied to "
                    "ExtractionManager::HandlePlayerDeath. When the player dies, "
                    "nothing triggers loot drop or session failure."
                ),
                severity=Severity.HIGH,
                category=Category.WIRING,
                fix_method=FixMethod.CPP_EDIT,
                fix_hint=(
                    "In EXOGameMode::BeginPlay or HandleMatchHasStarted, bind the player's "
                    "death delegate to ExtractionManager::HandlePlayerDeath. Example: "
                    "PlayerChar->OnDied.AddDynamic(ExtractionMgr, &ASWExtractionManager::HandlePlayerDeath);"
                ),
                file_path=str(exo_gm),
                auto_fixable=False,
            ))

        return findings

    @check("Respawn System", Category.WIRING)
    def check_respawn(self) -> list[Finding]:
        """Check if there's a respawn mechanism after death."""
        findings = []
        exo_gm = self._source / "EXO" / "EXOGameMode.cpp"
        content = self._read_file(exo_gm)

        has_respawn = any(term in content for term in [
            "Respawn", "RestartPlayer", "RespawnPlayer",
            "SpawnDefaultPawnFor", "HandleStartingNewPlayer",
        ])

        if not has_respawn:
            findings.append(Finding(
                id="NO_RESPAWN",
                title="No respawn mechanism in EXO mode",
                description=(
                    "EXOGameMode has no respawn logic. After death, the character "
                    "ragdolls permanently. Need at minimum a timer-based respawn."
                ),
                severity=Severity.MEDIUM,
                category=Category.WIRING,
                fix_method=FixMethod.CPP_EDIT,
                fix_hint=(
                    "Add to EXOGameMode: a HandlePlayerDeath that starts a timer, "
                    "then calls RestartPlayer(PlayerController) after the delay. "
                    "For EXO extraction mode, death should end the session instead."
                ),
                file_path=str(exo_gm),
                auto_fixable=False,
            ))

        return findings

    @check("Extraction Zone Heights", Category.WIRING)
    def check_extraction_zone_heights(self) -> list[Finding]:
        """Check if extraction zones account for terrain height."""
        findings = []
        ext_mgr = self._source / "EXO" / "Extraction" / "SWExtractionManager.cpp"
        content = self._read_file(ext_mgr)

        # Check if zones are spawned at Z=0 without terrain height query
        if "ZeroVector" in content or "FVector(0" in content:
            # Check if there's a line trace or height query
            has_height_fix = any(term in content for term in [
                "LineTrace", "GetGroundHeight", "ProjectPointToNavigation",
                "GetNavigationSystem", "TraceStart", "ECC_WorldStatic",
            ])
            if not has_height_fix:
                findings.append(Finding(
                    id="EXTRACTION_Z_ZERO",
                    title="Extraction zones may spawn at Z=0 (underground on proc terrain)",
                    description=(
                        "ExtractionManager spawns zones without querying terrain height. "
                        "On procedural terrain, Z=0 is underground."
                    ),
                    severity=Severity.MEDIUM,
                    category=Category.EXTRACTION,
                    fix_method=FixMethod.CPP_EDIT,
                    fix_hint=(
                        "After spawning each extraction zone, do a line trace downward "
                        "from (X, Y, 50000) to (X, Y, -50000) against ECC_WorldStatic "
                        "to find terrain surface height. Set the zone's Z to the hit location."
                    ),
                    file_path=str(ext_mgr),
                    auto_fixable=False,
                ))

        return findings

    @check("Extraction Session Timing", Category.WIRING)
    def check_extraction_timing(self) -> list[Finding]:
        """Check if ExtractionManager waits for zones before starting session."""
        findings = []
        ext_mgr = self._source / "EXO" / "Extraction" / "SWExtractionManager.cpp"
        content = self._read_file(ext_mgr)

        # Check if BeginPlay calls StartSession immediately
        begin_play_section = ""
        in_begin = False
        brace_count = 0
        for line in content.split("\n"):
            if "::BeginPlay()" in line:
                in_begin = True
            if in_begin:
                begin_play_section += line + "\n"
                brace_count += line.count("{") - line.count("}")
                if brace_count <= 0 and in_begin and "{" in begin_play_section:
                    break

        if "StartSession" in begin_play_section:
            findings.append(Finding(
                id="EXTRACTION_EARLY_START",
                title="ExtractionManager starts session in BeginPlay (before zones register)",
                description=(
                    "StartSession() is called in BeginPlay, but extraction zones may not "
                    "have registered yet. This causes '0 zones active' in the log."
                ),
                severity=Severity.MEDIUM,
                category=Category.EXTRACTION,
                fix_method=FixMethod.CPP_EDIT,
                fix_hint=(
                    "Delay StartSession with a timer (e.g. 2 seconds) to allow zones "
                    "to register in their own BeginPlay, or start session on first Tick "
                    "when ExtractionZones.Num() > 0."
                ),
                file_path=str(ext_mgr),
                auto_fixable=False,
            ))

        return findings

    @check("Inventory Component", Category.WIRING)
    def check_inventory_component(self) -> list[Finding]:
        """Check if USWInventoryComponent is added to SWCharacterBase."""
        findings = []
        char_h = self._source / "Variant_Combat" / "SWCharacterBase.h"
        content = self._read_file(char_h)

        if "USWInventoryComponent" not in content and "InventoryComponent" not in content:
            findings.append(Finding(
                id="NO_INVENTORY_COMPONENT",
                title="SWCharacterBase missing USWInventoryComponent",
                description=(
                    "The player character doesn't have an inventory component. "
                    "Loot pickup and extraction rewards won't work."
                ),
                severity=Severity.HIGH,
                category=Category.WIRING,
                fix_method=FixMethod.CPP_EDIT,
                fix_hint=(
                    "Add to SWCharacterBase.h: "
                    "UPROPERTY(VisibleAnywhere, Category=\"Inventory\") "
                    "TObjectPtr<USWInventoryComponent> InventoryComponent; "
                    "and create it in the constructor."
                ),
                file_path=str(char_h),
                auto_fixable=False,
            ))

        return findings

    @check("Combat Damage Pipeline", Category.COMBAT)
    def check_combat_pipeline(self) -> list[Finding]:
        """Check if the combat damage pipeline is complete: hit -> damage -> death -> loot."""
        findings = []
        enemy_cpp = self._source / "Variant_Combat" / "AI" / "SWEnemyBase.cpp"
        content = self._read_file(enemy_cpp)

        # Check TakeDamage is implemented
        if "ASWEnemyBase::TakeDamage" not in content and "ASWEnemyBase::ApplyDamage" not in content:
            findings.append(Finding(
                id="ENEMY_NO_DAMAGE",
                title="SWEnemyBase doesn't implement TakeDamage or ApplyDamage",
                description="Enemies can't take damage — combat won't work.",
                severity=Severity.CRITICAL,
                category=Category.COMBAT,
                fix_method=FixMethod.CPP_EDIT,
                fix_hint="Implement ASWEnemyBase::TakeDamage to subtract from CurrentHP and call HandleDeath at 0.",
                file_path=str(enemy_cpp),
                auto_fixable=False,
            ))

        # Check HandleDeath exists
        if "HandleDeath" in content:
            # Check it broadcasts OnDied
            if "OnDied.Broadcast" not in content:
                findings.append(Finding(
                    id="ENEMY_DEATH_NO_BROADCAST",
                    title="SWEnemyBase::HandleDeath doesn't broadcast OnDied",
                    description="Death delegate never fires — loot drops and quest tracking won't trigger.",
                    severity=Severity.HIGH,
                    category=Category.COMBAT,
                    fix_method=FixMethod.CPP_EDIT,
                    fix_hint="Add OnDied.Broadcast(this) in HandleDeath.",
                    file_path=str(enemy_cpp),
                    auto_fixable=False,
                ))

            # Check it spawns loot
            if "SpawnLootDrop" not in content:
                findings.append(Finding(
                    id="ENEMY_DEATH_NO_LOOT",
                    title="SWEnemyBase::HandleDeath doesn't call SpawnLootDrop",
                    description="Enemies don't drop loot on death.",
                    severity=Severity.MEDIUM,
                    category=Category.COMBAT,
                    fix_method=FixMethod.CPP_EDIT,
                    fix_hint="Call SpawnLootDrop() in HandleDeath.",
                    file_path=str(enemy_cpp),
                    auto_fixable=False,
                ))

        return findings

    @check("Terrain Seam Fix", Category.PERFORMANCE)
    def check_terrain_seams(self) -> list[Finding]:
        """Check if SeamOverlap=0 fix is applied to terrain generation."""
        findings = []
        # Search for terrain tile generation code
        hits = self._grep_source(r"SeamOverlap|seam_overlap|SEAM_OVERLAP")

        if not hits:
            # Look for terrain generation files
            terrain_hits = self._grep_source(r"class.*Terrain.*Tile|ProceduralTerrain|EXOTerrain")
            if terrain_hits:
                findings.append(Finding(
                    id="TERRAIN_SEAM_MISSING",
                    title="Terrain system exists but SeamOverlap fix not found",
                    description=(
                        "Procedural terrain code exists but SeamOverlap=0 is not set. "
                        "This causes visible holes between terrain tiles."
                    ),
                    severity=Severity.MEDIUM,
                    category=Category.PERFORMANCE,
                    fix_method=FixMethod.CPP_EDIT,
                    fix_hint="Set SeamOverlap=0 on terrain mesh sections for shared-edge vertices.",
                    auto_fixable=False,
                ))

        return findings

    @check("Compile State", Category.COMPILE)
    def check_compile_state(self) -> list[Finding]:
        """Check if the project compiles cleanly."""
        findings = []

        # Check for .pre_demo_backup files (indicates reverted edits that need recompile)
        backup_files = list(self._source.rglob("*.pre_demo_backup"))
        if backup_files:
            names = [f.stem for f in backup_files[:5]]
            findings.append(Finding(
                id="BACKUP_FILES_EXIST",
                title=f"{len(backup_files)} .pre_demo_backup files exist (may indicate reverted edits)",
                description=(
                    f"Found backup files: {', '.join(names)}. These suggest edits were "
                    "reverted. The current source may need recompilation to match."
                ),
                severity=Severity.LOW,
                category=Category.COMPILE,
                fix_method=FixMethod.NONE,
                fix_hint="Verify current source compiles. Backup files can be cleaned up if source is stable.",
                auto_fixable=False,
            ))

        return findings

    @check("UE5 Health Checks (Live)", Category.HEALTH_CHECK)
    def check_ue5_health(self) -> list[Finding]:
        """Run SWHealthChecks via UE5 bridge (requires live connection)."""
        findings = []
        if self._bridge is None or not self._bridge.is_connected:
            findings.append(Finding(
                id="UE5_NOT_CONNECTED",
                title="UE5 not connected — skipping live health checks",
                description="Connect UE5 with Remote Control to enable live diagnostics.",
                severity=Severity.INFO,
                category=Category.HEALTH_CHECK,
                fix_method=FixMethod.NONE,
            ))
            return findings

        # Run SWHealthChecks via Python in UE5
        script = """
import unreal
import json

# Try to run health checks if the console command is registered
try:
    # Health checks are C++ only — we can check some basics via Python
    results = []

    # Check: Player character exists in world
    pc = unreal.GameplayStatics.get_player_character(unreal.EditorLevelLibrary.get_editor_world(), 0)
    results.append({"check": "PlayerCharacter", "pass": pc is not None})

    # Check: NavMesh exists
    nav = unreal.AINavigationLibrary.get_navigation_system(unreal.EditorLevelLibrary.get_editor_world())
    results.append({"check": "NavigationSystem", "pass": nav is not None})

    # Check: DirectionalLight actors and their mobility
    actors = unreal.GameplayStatics.get_all_actors_of_class(
        unreal.EditorLevelLibrary.get_editor_world(),
        unreal.DirectionalLight
    )
    for a in actors:
        comp = a.get_component_by_class(unreal.DirectionalLightComponent)
        if comp:
            mobile = comp.mobility == unreal.ComponentMobility.MOVABLE
            results.append({
                "check": f"DirectionalLight_{a.get_name()}_Movable",
                "pass": mobile,
                "detail": f"Mobility: {comp.mobility}"
            })

    print(json.dumps(results))
except Exception as e:
    print(json.dumps([{"check": "HealthCheckRunner", "pass": False, "detail": str(e)}]))
"""
        result = self._bridge.execute_python(script)
        if result.success:
            try:
                output_lines = result.data.get("output", [])
                output_text = "\n".join(
                    l.get("output", "") for l in output_lines
                ).strip()
                checks = json.loads(output_text) if output_text else []
                for chk in checks:
                    if not chk.get("pass", True):
                        findings.append(Finding(
                            id=f"UE5_HEALTH_{chk['check'].upper()}",
                            title=f"UE5 Health Check failed: {chk['check']}",
                            description=chk.get("detail", "Check did not pass"),
                            severity=Severity.HIGH,
                            category=Category.HEALTH_CHECK,
                            fix_method=FixMethod.UE5_PYTHON,
                            fix_hint=f"Fix the {chk['check']} issue in the UE5 editor.",
                            auto_fixable=True,
                        ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse UE5 health check output: {e}")

        return findings

    @check("AnimBP Doctor (Live)", Category.ANIMATION)
    def check_animbp(self) -> list[Finding]:
        """Run the AnimBP Doctor script inside UE5 and parse its output into Findings.

        This delegates to animbp_doctor.py (8-phase diagnostic) which runs inside
        the UE5 editor via Remote Execution. The output is parsed for [OK], [WARN],
        [FAIL], [FIXED] tags and converted to structured Finding objects.
        """
        findings = []
        if self._bridge is None or not self._bridge.is_connected:
            findings.append(Finding(
                id="ANIMBP_NO_BRIDGE",
                title="UE5 not connected — skipping AnimBP live diagnostic",
                description=(
                    "The AnimBP Doctor requires a live UE5 connection to inspect "
                    "skeleton, mesh, AnimBP, BlendSpace, and montage assets."
                ),
                severity=Severity.INFO,
                category=Category.ANIMATION,
                fix_method=FixMethod.NONE,
            ))
            return findings

        doctor_path = self._content / "Python" / "animbp_doctor.py"
        if not doctor_path.exists():
            findings.append(Finding(
                id="ANIMBP_DOCTOR_MISSING",
                title="animbp_doctor.py not found in Content/Python/",
                description=f"Expected at: {doctor_path}",
                severity=Severity.MEDIUM,
                category=Category.ANIMATION,
                fix_method=FixMethod.NONE,
            ))
            return findings

        # Run the AnimBP doctor with output capture
        self._log("Running AnimBP Doctor inside UE5...")
        capture_script = f'''
import json
_bionics_lines = []
_bionics_orig_log = unreal.log
_bionics_orig_warn = unreal.log_warning
_bionics_orig_err = unreal.log_error
def _b_log(msg):
    _bionics_lines.append(("OK", str(msg)))
    _bionics_orig_log(msg)
def _b_warn(msg):
    _bionics_lines.append(("WARN", str(msg)))
    _bionics_orig_warn(msg)
def _b_err(msg):
    _bionics_lines.append(("FAIL", str(msg)))
    _bionics_orig_err(msg)
unreal.log = _b_log
unreal.log_warning = _b_warn
unreal.log_error = _b_err
try:
    exec(open(r"{doctor_path}").read())
except Exception as e:
    _bionics_orig_err(f"AnimBP Doctor crashed: {{e}}")
    _bionics_lines.append(("FAIL", f"AnimBP Doctor crashed: {{e}}"))
finally:
    unreal.log = _bionics_orig_log
    unreal.log_warning = _bionics_orig_warn
    unreal.log_error = _bionics_orig_err
# Emit structured JSON summary
_summary = {{
    "ok": [msg for lvl, msg in _bionics_lines if "[OK]" in msg],
    "warn": [msg for lvl, msg in _bionics_lines if "[WARN]" in msg],
    "fail": [msg for lvl, msg in _bionics_lines if "[FAIL]" in msg],
    "fixed": [msg for lvl, msg in _bionics_lines if "[FIXED]" in msg],
    "manual": [msg for lvl, msg in _bionics_lines if "MANUAL" in msg.upper() or "manual" in msg],
    "verdict": "READY" if not any("[FAIL]" in msg for _, msg in _bionics_lines) else "BLOCKED",
    "total_lines": len(_bionics_lines),
}}
print(json.dumps(_summary))
'''
        result = self._bridge.execute_python(capture_script)

        if not result.success:
            findings.append(Finding(
                id="ANIMBP_DOCTOR_EXEC_FAIL",
                title=f"AnimBP Doctor execution failed: {result.error}",
                description="Could not run animbp_doctor.py inside UE5.",
                severity=Severity.HIGH,
                category=Category.ANIMATION,
                fix_method=FixMethod.NONE,
            ))
            return findings

        # Parse the JSON summary from output
        try:
            output_lines = result.data.get("output", [])
            output_text = "\n".join(
                l.get("output", "") for l in output_lines
            ).strip()

            # Find the JSON blob (last line should be the print(json.dumps(...)))
            summary = None
            for line in reversed(output_text.split("\n")):
                line = line.strip()
                if line.startswith("{"):
                    summary = json.loads(line)
                    break

            if summary is None:
                # Fallback: parse raw output for tags
                summary = self._parse_animbp_raw_output(output_text)

        except (json.JSONDecodeError, KeyError) as e:
            self._log(f"AnimBP Doctor output parse error: {e}")
            summary = self._parse_animbp_raw_output(
                "\n".join(l.get("output", "") for l in result.data.get("output", []))
            )

        # Convert summary into Findings
        for fail_msg in summary.get("fail", []):
            clean = fail_msg.replace("[FAIL]", "").strip()
            findings.append(Finding(
                id=f"ANIMBP_{self._slugify(clean)}",
                title=clean,
                description=f"AnimBP Doctor Phase failure: {clean}",
                severity=Severity.HIGH,
                category=Category.ANIMATION,
                fix_method=FixMethod.UE5_PYTHON,
                fix_hint=f"Fix via AnimBP editor or UE5 Python script: {clean}",
                auto_fixable=True,
            ))

        for warn_msg in summary.get("warn", []):
            clean = warn_msg.replace("[WARN]", "").strip()
            findings.append(Finding(
                id=f"ANIMBP_{self._slugify(clean)}",
                title=clean,
                description=f"AnimBP Doctor warning: {clean}",
                severity=Severity.MEDIUM,
                category=Category.ANIMATION,
                fix_method=FixMethod.UE5_PYTHON,
                fix_hint=f"Address AnimBP warning: {clean}",
                auto_fixable=True,
            ))

        for manual_msg in summary.get("manual", []):
            clean = manual_msg.strip()
            if not any(clean in f.description for f in findings):
                findings.append(Finding(
                    id=f"ANIMBP_MANUAL_{self._slugify(clean)[:30]}",
                    title="AnimBP: manual step required",
                    description=clean,
                    severity=Severity.MEDIUM,
                    category=Category.ANIMATION,
                    fix_method=FixMethod.EDITOR_MANUAL,
                    fix_hint=clean,
                    auto_fixable=False,
                ))

        for fixed_msg in summary.get("fixed", []):
            clean = fixed_msg.replace("[FIXED]", "").strip()
            findings.append(Finding(
                id=f"ANIMBP_{self._slugify(clean)}",
                title=f"Auto-fixed: {clean}",
                description=f"AnimBP Doctor auto-fixed: {clean}",
                severity=Severity.INFO,
                category=Category.ANIMATION,
                fix_method=FixMethod.NONE,
                fixed=True,
            ))

        # Overall verdict
        verdict = summary.get("verdict", "UNKNOWN")
        ok_count = len(summary.get("ok", []))
        fail_count = len(summary.get("fail", []))
        fixed_count = len(summary.get("fixed", []))
        self._log(
            f"AnimBP Doctor: {ok_count} OK, {fail_count} FAIL, "
            f"{fixed_count} auto-fixed, verdict={verdict}"
        )

        return findings

    def _parse_animbp_raw_output(self, text: str) -> dict:
        """Fallback parser for AnimBP Doctor output when JSON isn't available."""
        result = {"ok": [], "warn": [], "fail": [], "fixed": [], "manual": [], "verdict": "UNKNOWN"}
        for line in text.split("\n"):
            line = line.strip()
            if "[OK]" in line:
                result["ok"].append(line)
            elif "[WARN]" in line:
                result["warn"].append(line)
            elif "[FAIL]" in line:
                result["fail"].append(line)
            elif "[FIXED]" in line:
                result["fixed"].append(line)
            if "MANUAL" in line.upper():
                result["manual"].append(line)
            if "BLOCKED" in line:
                result["verdict"] = "BLOCKED"
            elif "READY" in line and result["verdict"] != "BLOCKED":
                result["verdict"] = "READY"
        return result

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to a safe ID slug."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").upper()
        return slug[:50]

    # ------------------------------------------------------------------
    # Topic detection (routes prompts to the right checks)
    # ------------------------------------------------------------------

    @staticmethod
    def detect_topics(prompt: str) -> list[Category]:
        """Detect which diagnostic categories a prompt relates to.

        Used by divine powers to route prompts through the right Doctor checks
        before planning. Returns a list of relevant Categories.
        """
        prompt_lower = prompt.lower()
        topics = []

        topic_keywords = {
            Category.ANIMATION: [
                "animbp", "anim bp", "animgraph", "animation", "blend space",
                "blendspace", "montage", "locomotion", "t-pose", "tpose",
                "skeleton", "skeletal", "state machine", "inertialization",
                "default slot", "layered blend",
            ],
            Category.COMBAT: [
                "combat", "damage", "weapon", "melee", "attack", "punch",
                "hit", "death", "kill", "loot",
            ],
            Category.MOVEMENT: [
                "movement", "walk", "run", "sprint", "jump", "dodge",
                "slide", "crouch", "vault", "capsule", "cmc",
                "character movement",
            ],
            Category.AI: [
                "ai", "enemy", "patrol", "behavior", "statetree",
                "state tree", "perception", "navmesh", "pathfinding",
            ],
            Category.EDITOR: [
                "directional light", "mobility", "level", "editor",
                "viewport", "pie", "play in editor",
            ],
            Category.EXTRACTION: [
                "extraction", "extract", "zone", "session", "raid",
            ],
            Category.ASSET: [
                "mesh", "material", "texture", "asset", "content browser",
                "skeletal mesh", "static mesh",
            ],
            Category.PERFORMANCE: [
                "performance", "fps", "optimization", "terrain", "seam",
                "lod", "nanite", "lumen",
            ],
        }

        for category, keywords in topic_keywords.items():
            if any(kw in prompt_lower for kw in keywords):
                topics.append(category)

        return topics if topics else list(Category)  # If no match, run all checks

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def diagnose(self, categories: list[Category] | None = None) -> Diagnosis:
        """Run all diagnostic checks and return structured results.

        Args:
            categories: If provided, only run checks in these categories.
                        If None, run all checks.
        """
        self._log("MVP Doctor: Starting diagnosis...")
        self._file_cache.clear()
        diagnosis = Diagnosis()

        for name, cat, func in _CHECK_REGISTRY:
            if categories and cat not in categories:
                continue

            self._log(f"  Check: {name}")
            diagnosis.checks_run += 1
            try:
                findings = func(self)
                if findings:
                    diagnosis.findings.extend(findings)
                else:
                    diagnosis.checks_passed += 1
            except Exception as e:
                logger.error(f"Check '{name}' crashed: {e}")
                diagnosis.findings.append(Finding(
                    id=f"CHECK_CRASH_{name.upper().replace(' ', '_')}",
                    title=f"Check '{name}' crashed: {e}",
                    description=str(e),
                    severity=Severity.LOW,
                    category=cat,
                ))

        # Sort: CRITICAL first, then HIGH, etc.
        severity_order = {s: i for i, s in enumerate(Severity)}
        diagnosis.findings.sort(key=lambda f: severity_order[f.severity])

        self._log(diagnosis.summary())
        return diagnosis

    def diagnose_and_fix(
        self,
        auto_planner=None,
        bridge=None,
        max_cycles: int = 3,
        auto_only: bool = True,
    ) -> dict:
        """Full pipeline: diagnose -> plan -> execute -> re-diagnose -> report.

        Args:
            auto_planner: AutoPlanner instance for generating fix plans
            bridge: UE5Bridge for executing fixes
            max_cycles: Max diagnose-fix-verify cycles
            auto_only: If True, only fix auto_fixable findings

        Returns:
            Dict with diagnosis, plans executed, and final state.
        """
        self._log("MVP Doctor: Starting diagnose-and-fix pipeline...")
        results = {
            "cycles": [],
            "initial_diagnosis": None,
            "final_diagnosis": None,
            "demo_ready": False,
        }

        for cycle in range(max_cycles):
            self._log(f"\n{'='*40} CYCLE {cycle+1}/{max_cycles} {'='*40}")

            # Step 1: Diagnose
            diagnosis = self.diagnose()
            if cycle == 0:
                results["initial_diagnosis"] = diagnosis.to_dict()

            # Step 2: Check if we're done
            fixable = [f for f in diagnosis.unfixed if (not auto_only or f.auto_fixable)]
            if not fixable:
                self._log("No more fixable findings. Pipeline complete.")
                break

            # Step 3: Generate fix plan
            if auto_planner is None:
                self._log("No AutoPlanner provided — diagnosis only (no fixes applied)")
                break

            self._log(f"Generating fix plan for {len(fixable)} findings...")
            prompt = diagnosis.to_planner_prompt()
            plan_result = auto_planner.generate_plan(prompt, divine=True)

            cycle_result = {
                "cycle": cycle + 1,
                "findings_count": len(diagnosis.findings),
                "fixable_count": len(fixable),
                "plan": plan_result.get("plan", {}),
            }

            # Step 4: Execute if bridge available
            if bridge is not None:
                plan = plan_result.get("plan", {})
                steps = plan.get("steps", [])
                executed = 0
                passed = 0

                for step in steps:
                    method = step.get("execution_method", "")
                    if method == "ue5_python" and step.get("script_content"):
                        self._log(f"Executing fix: {step.get('description', '?')}")
                        exec_result = bridge.execute_python(step["script_content"])
                        executed += 1
                        if exec_result.success:
                            passed += 1
                    elif method == "existing_script" and step.get("existing_script"):
                        script_path = (
                            self._content / "Python" / step["existing_script"]
                        )
                        if script_path.exists():
                            self._log(f"Running tool: {step['existing_script']}")
                            exec_result = bridge.execute_python(
                                f"exec(open(r'{script_path}').read())"
                            )
                            executed += 1
                            if exec_result.success:
                                passed += 1

                cycle_result["executed"] = executed
                cycle_result["passed"] = passed
                self._log(f"Cycle {cycle+1}: {passed}/{executed} fixes applied")

            results["cycles"].append(cycle_result)

        # Final diagnosis
        final = self.diagnose()
        results["final_diagnosis"] = final.to_dict()
        results["demo_ready"] = final.is_demo_ready

        status = "DEMO READY" if final.is_demo_ready else "NOT READY"
        self._log(f"\nMVP Doctor Pipeline Complete — {status}")
        self._log(final.summary())

        # Save report
        report_path = Path("plans") / f"mvp_doctor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        self._log(f"Report saved: {report_path}")

        return results
