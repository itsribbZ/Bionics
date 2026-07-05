"""BP Doctor — Bionics MCP wrapper for AnimBPDoctor.

Wraps the standalone AnimBPDoctor CLI (`AnimBPDoctor.pyw --cli`) so an agent
can scan a UE5 project for AnimBP / Blueprint issues from inside a Bionics
session, get structured JSON back, and chain the result into other tools
(plan-step generation, fix automation, etc.).

AnimBPDoctor itself lives in a sibling project; this wrapper just shells out
to its CLI and parses the JSON output. No logic is duplicated — the scanner
remains the single source of truth for the 34 check categories.

Tools registered:
    bp_doctor_scan(project_path, severity=None, checks=None, format='json')
        → {ok, issues, count_by_severity, scanned_assets, elapsed_ms}

    bp_doctor_locate()
        → {ok, path, source}     # diagnostic — where did we find the CLI?
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Literal

from core.bridge import SafetyTier, ToolResult, bionics_tool

logger = logging.getLogger("bionics.tools.bp_doctor")

# ─── locate AnimBPDoctor ──────────────────────────────────────────────

def _candidate_paths() -> list[Path]:
    """Ordered list of places to look for AnimBPDoctor.pyw. First hit wins."""
    paths: list[Path] = []

    env = os.environ.get("BP_DOCTOR_PATH", "").strip()
    if env:
        paths.append(Path(env))

    # Sibling-project layout: <T1>/AnimBPDoctor/dev/AnimBPDoctor.pyw
    here = Path(__file__).resolve()
    bionics_root = here.parent.parent
    paths.append(bionics_root.parent / "AnimBPDoctor" / "dev" / "AnimBPDoctor.pyw")

    # User's standard layout (per ROSETTA_STONE)
    paths.append(Path.home() / "Desktop" / "T1" / "AnimBPDoctor" / "dev" / "AnimBPDoctor.pyw")

    return paths


def _resolve_bp_doctor() -> tuple[Path, str] | None:
    """Find AnimBPDoctor.pyw on disk. Returns (path, source) or None."""
    env = os.environ.get("BP_DOCTOR_PATH", "").strip()
    candidates = _candidate_paths()
    for i, p in enumerate(candidates):
        if p.is_file():
            source = "env_var" if (env and i == 0) else "auto_detect"
            return p, source
    return None


# ─── tool: locate ─────────────────────────────────────────────────────

@bionics_tool(
    name="bp_doctor_locate",
    category="bp_doctor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Locate BP Doctor",
)
def bp_doctor_locate() -> ToolResult:
    """Return the resolved path to AnimBPDoctor.pyw (or report failure).

    Used to verify the wrapper can find the scanner before calling
    `bp_doctor_scan`. Set `BP_DOCTOR_PATH` to override auto-detection.
    """
    found = _resolve_bp_doctor()
    if found is None:
        return ToolResult.failure(
            "AnimBPDoctor.pyw not found. Set BP_DOCTOR_PATH or place it "
            "at <T1>/AnimBPDoctor/dev/AnimBPDoctor.pyw.",
            checked=[str(p) for p in _candidate_paths()],
        )
    path, source = found
    return ToolResult.success(data={
        "path": str(path),
        "source": source,
    })


# ─── tool: scan ───────────────────────────────────────────────────────

@bionics_tool(
    name="bp_doctor_scan",
    category="bp_doctor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Scan UE5 Project (BP Doctor)",
)
def bp_doctor_scan(
    project_path: Annotated[str, "Path to UE5 .uproject directory"],
    severity: Annotated[
        str | None,
        "Filter by severity (comma-separated). E.g. 'ERROR' or 'ERROR,WARNING'."
    ] = None,
    checks: Annotated[
        str | None,
        "Only run specific check codes (comma-separated)."
    ] = None,
    output_format: Annotated[
        Literal["json", "sarif"],
        "Output format. JSON parsed in-place; SARIF returned as raw string."
    ] = "json",
    timeout_seconds: Annotated[int, "Subprocess timeout in seconds"] = 300,
) -> ToolResult:
    """Scan a UE5 project for AnimBP/Blueprint issues via AnimBPDoctor.

    Returns a structured summary of findings:
        {
          "issues": [...],                 # full list (json mode only)
          "count": <int>,
          "count_by_severity": {"ERROR": N, "WARNING": M, "INFO": K},
          "scanned_assets": <int>,         # if reported by scanner
          "elapsed_ms": <float>,
          "raw_format": "json" | "sarif",
          "raw": <string>,                 # only when format=sarif
        }

    The wrapper itself is read-only: AnimBPDoctor's --cli mode only reads
    .uasset files, never writes. Auto-fix would require a separate, gated
    DESTRUCTIVE tool (not provided here).
    """
    found = _resolve_bp_doctor()
    if found is None:
        return ToolResult.failure(
            "AnimBPDoctor.pyw not found. Run bp_doctor_locate for diagnostics."
        )
    bp_doctor_path, _ = found

    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        return ToolResult.failure(f"Project path not a directory: {project}")

    # AnimBPDoctor accepts the project root (containing .uproject or Content/)
    cmd = [
        sys.executable,
        str(bp_doctor_path),
        "--cli",
        "--project", str(project),
        "--format", output_format,
        "--quiet",
    ]
    if severity:
        cmd.extend(["--severity", severity])
    if checks:
        cmd.extend(["--checks", checks])

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(
            f"BP Doctor scan timed out after {timeout_seconds}s",
            cmd=cmd,
        )
    except FileNotFoundError as e:
        return ToolResult.failure(f"Failed to launch Python: {e}")
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    # AnimBPDoctor uses --exit-code only when explicitly requested. Without it,
    # exit 0 = clean run, non-zero = scanner error. Treat the parsed output as
    # authoritative for issue presence.
    if completed.returncode not in (0, 1):
        return ToolResult.failure(
            f"BP Doctor exited with code {completed.returncode}",
            stdout=(completed.stdout or "")[-2000:],
            stderr=(completed.stderr or "")[-2000:],
            elapsed_ms=elapsed_ms,
        )

    raw = completed.stdout or ""
    if output_format == "sarif":
        return ToolResult.success(data={
            "raw_format": "sarif",
            "raw": raw,
            "elapsed_ms": elapsed_ms,
        })

    # output_format == "json"
    try:
        parsed = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as e:
        return ToolResult.failure(
            f"BP Doctor returned non-JSON output: {e}",
            stdout_excerpt=raw[:1000],
            elapsed_ms=elapsed_ms,
        )

    # AnimBPDoctor's JSON schema: a list of issue dicts (per dev/AnimBPDoctor.pyw
    # serialization). Each has at minimum: severity, code, asset, message.
    issues = parsed if isinstance(parsed, list) else parsed.get("issues", [])
    by_sev: dict[str, int] = {}
    for it in issues:
        s = (it.get("severity") or "UNKNOWN").upper()
        by_sev[s] = by_sev.get(s, 0) + 1

    summary = {
        "issues": issues,
        "count": len(issues),
        "count_by_severity": by_sev,
        "elapsed_ms": elapsed_ms,
        "raw_format": "json",
    }
    if isinstance(parsed, dict):
        # If scanner ever emits a wrapped dict with metadata, surface it
        for key in ("scanned_assets", "version", "scan_id"):
            if key in parsed:
                summary[key] = parsed[key]

    return ToolResult.success(data=summary)
