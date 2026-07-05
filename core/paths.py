"""Centralized path resolution for Bionics.

Reads from config.yaml and environment variables. All paths that were previously
hardcoded now resolve through this module.

Priority order: environment variable → config.yaml → None (disabled).
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("bionics.paths")


def _resolve_project_root() -> Path:
    """Return the writable project root.

    Under PyInstaller --onedir/--onefile, `__file__` resolves to the
    `_internal/` (or temp) directory which is read-only at runtime. Writes
    to plans/, audit/, sessions/ would either land in the wrong place or
    fail with a permission error. When frozen, anchor to the executable's
    directory instead so user-data dirs sit beside the .exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


PROJECT_ROOT = _resolve_project_root()


def _load_config_paths() -> dict:
    """Read the paths section from config.yaml."""
    try:
        import yaml
        config_path = PROJECT_ROOT / "config.yaml"
        if config_path.exists():
            # Explicit utf-8: matches every other file read in core/. Without it,
            # Windows defaults to cp1252 and silently misreads non-ASCII content.
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("paths", {})
    except Exception as e:
        logger.warning(f"paths: failed to load config.yaml ({e}) — using env vars / defaults only")
    return {}


def get_ue5_project() -> Path | None:
    """Get the UE5 project root (e.g., .../Sworder721/MyProject)."""
    env = os.environ.get("BIONICS_UE5_PROJECT", "").strip()
    if env:
        return Path(env)
    p = _load_config_paths().get("ue5_project", "")
    return Path(p) if p else None


def get_ue5_python_dir() -> Path | None:
    """Get the Content/Python directory inside the UE5 project."""
    proj = get_ue5_project()
    if proj:
        return proj / "Content" / "Python"
    return None


def get_bible_path() -> Path | None:
    """Get the Sworder Bible docs directory."""
    env = os.environ.get("BIONICS_BIBLE_PATH", "").strip()
    if env:
        return Path(env)
    p = _load_config_paths().get("bible", "")
    return Path(p) if p else None


def get_design_docs_path() -> Path | None:
    """Get the Design System docs directory."""
    env = os.environ.get("BIONICS_DOCS_PATH", "").strip()
    if env:
        return Path(env)
    p = _load_config_paths().get("design_docs", "")
    return Path(p) if p else None


def get_ue_knowledge_path() -> Path | None:
    """Get the UE Knowledge zone directory (T1/sworder/UE Knowledge)."""
    env = os.environ.get("BIONICS_UE_KNOWLEDGE_PATH", "").strip()
    if env:
        return Path(env)
    p = _load_config_paths().get("ue_knowledge", "")
    return Path(p) if p else None


def get_market_kb_paths() -> list[Path]:
    """Get Market Bot knowledge base file paths."""
    env = os.environ.get("BIONICS_MARKET_KB_DIR", "").strip()
    if env:
        kb_dir = Path(env)
        if kb_dir.exists():
            return sorted(kb_dir.iterdir())
        return []
    p = _load_config_paths().get("market_kb", "")
    if p:
        kb_dir = Path(p)
        if kb_dir.exists():
            return sorted(kb_dir.iterdir())
    return []
