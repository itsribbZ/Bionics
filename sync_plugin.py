#!/usr/bin/env python3
"""Sync BionicsBridge plugin from canonical Bionics source to a UE5 project.

**Path resolution (in priority order)**:
  SOURCE_ROOT: always `<repo>/plugins/BionicsBridge/` (auto-resolved from this script).
  TARGET_ROOT: env var `BIONICS_PLUGIN_TARGET` → config.yaml `paths.ue5_project`/Plugins/BionicsBridge
               → error (sync cannot run blind).

**Why this exists**: the canonical Bionics plugin source is edited in the Bionics repo;
a downstream UE5 project (e.g. Sworder:721) keeps a deployed copy at
`<uproject>/Plugins/BionicsBridge/`. This script keeps them in sync.

**Behavior**:
- Compares file SHA256 hashes between source and target trees
- Copies changed/missing source files (Source/, *.uplugin, Resources/)
- **Preserves Binaries/** and **Intermediate/** in target (never clobbered — those are
  UE5 build outputs, not plugin source)
- Dry-run by default. Pass `--write` to actually copy.
- Reports any files in target that AREN'T in source (orphans — likely from old versions)

**Usage**:
    # One-off:
    BIONICS_PLUGIN_TARGET=D:/UE5Projects/Foo/Plugins/BionicsBridge python sync_plugin.py
    # Or set `paths.ue5_project` in config.yaml and run:
    python sync_plugin.py --write
    python sync_plugin.py --write --verbose  # show every file compared

Run this after editing the C++ plugin source. Do NOT run with `--write` while UE5
has the project loaded — the DLLs will be locked. Close UE5, sync, reopen, let it
hot-compile.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent / "plugins" / "BionicsBridge"


def _resolve_target_root() -> Path:
    """Resolve the target plugin dir from env var, config, or raise."""
    env = os.environ.get("BIONICS_PLUGIN_TARGET", "").strip()
    if env:
        return Path(env)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from core.paths import get_ue5_project
        proj = get_ue5_project()
        if proj:
            return Path(proj) / "Plugins" / "BionicsBridge"
    except Exception:
        pass
    raise SystemExit(
        "sync_plugin: cannot resolve target plugin directory.\n"
        "  Set env var BIONICS_PLUGIN_TARGET=<uproject>/Plugins/BionicsBridge\n"
        "  OR set paths.ue5_project in config.yaml"
    )


TARGET_ROOT = _resolve_target_root()

# Files/dirs to sync (relative to plugin root)
SYNC_PATHS = ["Source", "Resources", "BionicsBridge.uplugin"]

# Dirs to NEVER touch in target (build outputs owned by UE5)
PRESERVE_IN_TARGET = {"Binaries", "Intermediate", "DerivedDataCache"}


def sha256_file(path: Path) -> str:
    """Return hex SHA256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def enumerate_source_files() -> list[Path]:
    """Recursively list all files under SYNC_PATHS in the canonical source."""
    files = []
    for rel in SYNC_PATHS:
        src = SOURCE_ROOT / rel
        if src.is_file():
            files.append(src)
        elif src.is_dir():
            for p in src.rglob("*"):
                if p.is_file():
                    files.append(p)
    return files


def compare_tree(verbose: bool = False) -> dict:
    """Compare source vs target. Returns {missing, changed, same, orphans}."""
    result = {"missing": [], "changed": [], "same": [], "orphans": []}

    if not SOURCE_ROOT.exists():
        print(f"ERROR: Source does not exist: {SOURCE_ROOT}")
        sys.exit(2)
    if not TARGET_ROOT.exists():
        print(f"ERROR: Target does not exist: {TARGET_ROOT}")
        print("       (Create the Plugins/ dir in Sworder first, then re-run.)")
        sys.exit(2)

    source_files = enumerate_source_files()

    # Check each source file against target
    for src_path in source_files:
        rel = src_path.relative_to(SOURCE_ROOT)
        tgt_path = TARGET_ROOT / rel

        if not tgt_path.exists():
            result["missing"].append(rel)
            if verbose:
                print(f"  MISSING: {rel}")
            continue

        try:
            if sha256_file(src_path) != sha256_file(tgt_path):
                result["changed"].append(rel)
                if verbose:
                    print(f"  CHANGED: {rel}")
            else:
                result["same"].append(rel)
                if verbose:
                    print(f"  SAME: {rel}")
        except OSError as e:
            print(f"  SKIP (read error): {rel} -> {e}")

    # Find orphans — files in target that aren't in source (ignoring PRESERVE_IN_TARGET)
    for rel_name in SYNC_PATHS:
        tgt_root = TARGET_ROOT / rel_name
        if tgt_root.is_dir():
            for tgt_file in tgt_root.rglob("*"):
                if not tgt_file.is_file():
                    continue
                rel = tgt_file.relative_to(TARGET_ROOT)
                src = SOURCE_ROOT / rel
                if not src.exists():
                    # Is it inside a preserve dir? Skip.
                    if any(part in PRESERVE_IN_TARGET for part in rel.parts):
                        continue
                    result["orphans"].append(rel)

    return result


def apply_sync(changes: dict, verbose: bool = False) -> int:
    """Copy missing/changed files from source to target. Returns count copied.

    Note: uses shutil.copy (not copy2) and explicitly bumps mtime to now after
    each write. This is deliberate — UBT's adaptive build skips recompile when
    .obj mtime > source mtime, and copy2 preserves repo mtimes which can be
    older than stale .obj files from prior phantom-success builds. Touching
    after copy guarantees the build sees newly-synced files as fresh.
    See feedback_sync_plugin_before_rebuild.md for the incident receipts.
    """
    import time as _time
    count = 0
    to_copy = changes["missing"] + changes["changed"]
    now = _time.time()
    for rel in to_copy:
        src = SOURCE_ROOT / rel
        tgt = TARGET_ROOT / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, tgt)
        os.utime(tgt, (now, now))  # bump mtime past any stale .obj timestamps
        count += 1
        if verbose:
            print(f"  COPIED: {rel}")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="Actually copy changed files (default is dry-run)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every file compared")
    args = parser.parse_args()

    print(f"Source: {SOURCE_ROOT}")
    print(f"Target: {TARGET_ROOT}")
    print(f"Mode:   {'WRITE' if args.write else 'DRY-RUN'}")
    print()

    changes = compare_tree(verbose=args.verbose)

    print("SUMMARY")
    print(f"  Same:    {len(changes['same'])}")
    print(f"  Changed: {len(changes['changed'])}")
    print(f"  Missing: {len(changes['missing'])}")
    print(f"  Orphans: {len(changes['orphans'])}")

    if changes["changed"] or changes["missing"]:
        print()
        print("FILES TO SYNC:")
        for rel in (changes["missing"] + changes["changed"])[:30]:
            marker = "NEW" if rel in changes["missing"] else "UPD"
            print(f"  [{marker}] {rel}")
        more = len(changes["changed"] + changes["missing"]) - 30
        if more > 0:
            print(f"  ... and {more} more")

    if changes["orphans"]:
        print()
        print("ORPHANS IN TARGET (exist in Sworder but not in canonical source):")
        for rel in changes["orphans"][:10]:
            print(f"  [ORPHAN] {rel}")
        if len(changes["orphans"]) > 10:
            print(f"  ... and {len(changes['orphans']) - 10} more")
        print("  (Orphans are NOT deleted — review manually if unexpected)")

    if args.write:
        if not (changes["changed"] or changes["missing"]):
            print("\nNothing to sync. Target is up to date.")
            return 0
        print()
        print("Copying files...")
        copied = apply_sync(changes, verbose=args.verbose)
        print(f"SYNC COMPLETE: {copied} files copied.")
        print()
        print("Next steps:")
        print("  1. Close UE5 if running (Binaries will be locked otherwise)")
        print("  2. Right-click MyProject.uproject -> Generate Visual Studio project files")
        print("  3. Rebuild MyProjectEditor target")
        print("  4. Reopen UE5 (BionicsBridge hot-reloads)")
    else:
        print()
        print("Dry-run — no files copied. Re-run with --write to apply.")

    return 0 if not (changes["missing"] or changes["changed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
