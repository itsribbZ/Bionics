"""UE5 Asset Export Tools — the reverse bridge for blend-master ↔ bionics round-trip.

The forward direction (Blender → UE5 import) is already covered by:
    blend-master/bridge/ue5_importer.py + _ue5_import_via_bionics.py

This module is the missing reverse primitive: pull a .uasset out of UE5 to an
FBX/GLB file on disk so blend-master can re-open it in Blender. With this in
place, `sync_asset.py` (blend-master side) can orchestrate a true round trip.

Tools:
    ue5_export_to_fbx — export StaticMesh / SkeletalMesh / AnimSequence to FBX
    ue5_export_to_glb — export StaticMesh / SkeletalMesh to glTF/GLB
    ue5_export_asset_info — diagnose what an asset is before exporting

Asset class autodetection: tool inspects the loaded asset's class and picks the
right exporter + options. Caller can override with explicit asset_class arg.
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool


# ============================================================
# ue5_export_asset_info — read-only diagnostic
# ============================================================

@bionics_tool(
    name="ue5_export_asset_info",
    category="ue5_export",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["export-info"],
    title="Export Asset Info (round-trip diag)",
)
def ue5_export_asset_info(
    asset_path: Annotated[str, "UE5 asset path, e.g. /Game/Test/BlendMaster/SM_SW_SciFiCrate"],
) -> ToolResult:
    """Inspect a UE5 asset to decide which exporter applies. Read-only."""
    ap = escape_path(asset_path)
    body = f"""
import os
ap = "{ap}"
asset = unreal.EditorAssetLibrary.load_asset(ap)
info = {{"asset_path": ap, "loaded": bool(asset)}}
if asset:
    cls = asset.get_class()
    info["class"] = str(cls.get_name()) if cls else None
    info["full_class"] = str(asset.__class__.__name__)
    # Per-class stats
    if isinstance(asset, unreal.StaticMesh):
        info["kind"] = "static_mesh"
        info["lod_count"] = asset.get_num_lods() if hasattr(asset, "get_num_lods") else None
        info["material_count"] = len(asset.static_materials) if hasattr(asset, "static_materials") else None
    elif isinstance(asset, unreal.SkeletalMesh):
        info["kind"] = "skeletal_mesh"
        info["lod_count"] = asset.get_num_lods() if hasattr(asset, "get_num_lods") else None
        skel = asset.skeleton if hasattr(asset, "skeleton") else None
        info["skeleton"] = str(skel.get_path_name()) if skel else None
    elif isinstance(asset, unreal.AnimSequence):
        info["kind"] = "anim_sequence"
        info["frame_count"] = asset.get_editor_property("number_of_sampled_frames") if hasattr(asset, "get_editor_property") else None
    else:
        info["kind"] = "other"
    info["recommended_exporter"] = {{
        "static_mesh":   "fbx_or_glb",
        "skeletal_mesh": "fbx_or_glb",
        "anim_sequence": "fbx",
        "other":         "unsupported",
    }}.get(info["kind"], "unsupported")
print(_dump(info))
"""
    return run_python(wrap_script(body))


# ============================================================
# ue5_export_to_fbx — primary reverse-bridge primitive
# ============================================================

@bionics_tool(
    name="ue5_export_to_fbx",
    category="ue5_export",
    safety_tier=SafetyTier.MODERATE,
    aliases=["export-fbx"],
    title="Export Asset to FBX (UE5 → disk)",
)
def ue5_export_to_fbx(
    asset_path: Annotated[str, "UE5 asset path, e.g. /Game/Test/BlendMaster/SM_SW_SciFiCrate"],
    output_path: Annotated[str, "Absolute filesystem path for output .fbx"],
    asset_class: Annotated[str, "Optional override: static_mesh | skeletal_mesh | anim_sequence | auto"] = "auto",
    ascii_format: Annotated[bool, "ASCII FBX (default: binary)"] = False,
    embed_textures: Annotated[bool, "Embed textures in FBX (default: false)"] = False,
    level_of_detail: Annotated[bool, "Export all LODs (default: false — LOD0 only)"] = False,
    collision: Annotated[bool, "Include collision (static meshes; default: false)"] = False,
) -> ToolResult:
    """Export a UE5 asset to an FBX file. Round-trip reverse bridge.

    Composability: pair with blend-master/bridge/sync_asset.py:sync_from_ue5()
    for full Blender-side re-ingest. The output_path is what gets re-opened in
    Blender via bpy.ops.import_scene.fbx.

    Per-class behavior:
      - StaticMesh: standard FBX mesh export (geometry + UVs + materials)
      - SkeletalMesh: mesh + skeleton (bone hierarchy + weights)
      - AnimSequence: skeleton + animation tracks (FBX is the only path; GLB not viable)
    """
    ap = escape_path(asset_path)
    op = escape_path(output_path).replace("\\\\", "/").replace("\\", "/")
    ac = escape_path(asset_class)
    body = f"""
import os
import time
ap = "{ap}"
op = "{op}"
ac = "{ac}"

# Ensure output dir exists
out_dir = os.path.dirname(op)
if out_dir:
    os.makedirs(out_dir, exist_ok=True)

asset = unreal.EditorAssetLibrary.load_asset(ap)
if not asset:
    print(_dump({{"ok": False, "error": f"Asset not loadable: {{ap}}"}}))
else:
    # Detect class if auto
    if ac == "auto":
        if isinstance(asset, unreal.StaticMesh):
            ac = "static_mesh"
        elif isinstance(asset, unreal.SkeletalMesh):
            ac = "skeletal_mesh"
        elif isinstance(asset, unreal.AnimSequence):
            ac = "anim_sequence"
        else:
            ac = "unsupported"

    if ac == "unsupported":
        print(_dump({{"ok": False, "error": f"Unsupported asset class for FBX export: {{type(asset).__name__}}"}}))
    else:
        # Build per-class FBX options
        opts = unreal.FbxExportOption()
        opts.ascii = bool({ascii_format!r} == "True" or {ascii_format} is True)
        opts.fbx_export_compatibility = unreal.FbxExportCompatibility.FBX_2013
        opts.embed_textures = bool({embed_textures} is True)
        opts.level_of_detail = bool({level_of_detail} is True)
        opts.collision = bool({collision} is True)
        # Class-specific
        if ac == "skeletal_mesh":
            opts.export_morph_targets = True
            opts.vertex_color = True
        elif ac == "anim_sequence":
            opts.export_local_time = True
        # Build task
        task = unreal.AssetExportTask()
        task.object = asset
        task.filename = op
        task.replace_identical = True
        task.prompt = False
        task.automated = True
        task.options = opts
        # Execute
        t0 = time.time()
        ok = unreal.Exporter.run_asset_export_task(task)
        elapsed_ms = int((time.time() - t0) * 1000)
        size_bytes = os.path.getsize(op) if os.path.exists(op) else 0
        print(_dump({{
            "ok": bool(ok) and size_bytes > 0,
            "asset_path": ap,
            "asset_class": ac,
            "output_path": op,
            "size_bytes": size_bytes,
            "elapsed_ms": elapsed_ms,
            "ascii": opts.ascii,
            "lods_exported": opts.level_of_detail,
        }}))
"""
    return run_python(wrap_script(body))


# ============================================================
# ue5_export_to_glb — glTF/GLB export (lighter than FBX, web-friendly)
# ============================================================

@bionics_tool(
    name="ue5_export_to_glb",
    category="ue5_export",
    safety_tier=SafetyTier.MODERATE,
    aliases=["export-glb"],
    title="Export Asset to GLB (UE5 → disk)",
)
def ue5_export_to_glb(
    asset_path: Annotated[str, "UE5 asset path"],
    output_path: Annotated[str, "Absolute filesystem path for output .glb"],
) -> ToolResult:
    """Export a UE5 mesh asset to GLB (glTF 2.0 binary).

    GLB is preferred over FBX for: static meshes with PBR materials, web preview,
    smaller file sizes. Not suitable for animation export (use FBX for that).

    Requires the GLTFExporter plugin enabled in the .uproject — bionics returns
    a clear error if it isn't.
    """
    ap = escape_path(asset_path)
    op = escape_path(output_path).replace("\\\\", "/").replace("\\", "/")
    body = f"""
import os
import time
ap = "{ap}"
op = "{op}"

out_dir = os.path.dirname(op)
if out_dir:
    os.makedirs(out_dir, exist_ok=True)

asset = unreal.EditorAssetLibrary.load_asset(ap)
if not asset:
    print(_dump({{"ok": False, "error": f"Asset not loadable: {{ap}}"}}))
else:
    # GLTFExportOptions lives in the GLTFExporter plugin
    try:
        opts = unreal.GLTFExportOptions()
    except AttributeError:
        print(_dump({{
            "ok": False,
            "error": "GLTFExporter plugin not enabled. Enable in Edit > Plugins > glTF Exporter.",
        }}))
    else:
        # Sensible defaults: PBR-friendly, binary
        opts.export_uniform_scale = 1.0
        if hasattr(opts, "export_settings_compression"):
            opts.export_settings_compression = unreal.GLTFTextureImageFormat.PNG
        task = unreal.AssetExportTask()
        task.object = asset
        task.filename = op
        task.replace_identical = True
        task.prompt = False
        task.automated = True
        task.options = opts
        t0 = time.time()
        ok = unreal.Exporter.run_asset_export_task(task)
        elapsed_ms = int((time.time() - t0) * 1000)
        size_bytes = os.path.getsize(op) if os.path.exists(op) else 0
        print(_dump({{
            "ok": bool(ok) and size_bytes > 0,
            "asset_path": ap,
            "output_path": op,
            "size_bytes": size_bytes,
            "elapsed_ms": elapsed_ms,
        }}))
"""
    return run_python(wrap_script(body))
