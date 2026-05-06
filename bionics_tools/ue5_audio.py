"""UE5 Audio Tools — SoundWave import + SoundAttenuation configuration.

Closes the audio-sprint gap flagged 2026-04-17 audit: 30-40 SFX + 4 audio
subsystems all hand-edited. These tools let Bionics import .wav files to
USoundWave assets and configure attenuation falloffs without opening the
editor panel for each one.

Tool surface:
    ue5_sound_import            — import WAV file → USoundWave asset
    ue5_sound_set_attenuation   — set/create SoundAttenuation with falloff params

Bible reference: Adaptive Music + ambient biome loops + combat audio feel.
UE5 5.1+ required (AssetImportTask Python API).
"""

from __future__ import annotations

import os
from typing import Annotated, Literal

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool


@bionics_tool(
    name="ue5_sound_import",
    category="ue5_audio",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["import-sound", "sound-import"],
    title="Import Sound Wave",
)
def ue5_sound_import(
    source_wav_path: Annotated[str, "Absolute path to source .wav file on disk"],
    destination_path: Annotated[str, "UE5 asset path for output (/Game/Audio/SFX/SW_ExplosionLarge)"],
    replace_existing: Annotated[bool, "Overwrite if an asset already exists at destination_path"] = False,
) -> ToolResult:
    """Import a .wav file from disk as a USoundWave asset.

    Uses UE5's AssetImportTask pipeline — the same mechanism the Content
    Browser drag-drop uses. Result is a USoundWave that can be wired into
    SoundCues, MetaSounds, or referenced directly from C++ gameplay code.
    """
    if not source_wav_path or not isinstance(source_wav_path, str):
        return ToolResult.failure("source_wav_path must be a non-empty string")
    if not os.path.isfile(source_wav_path):
        return ToolResult.failure(f"source file not found: {source_wav_path}")
    if not source_wav_path.lower().endswith((".wav", ".ogg", ".flac")):
        return ToolResult.failure("source must be .wav, .ogg, or .flac")
    if not destination_path.startswith("/Game/"):
        return ToolResult.failure("destination_path must start with /Game/")

    src = escape_path(source_wav_path)
    dst = escape_path(destination_path)
    replace = "True" if replace_existing else "False"
    body = f"""
src_file = '{src}'
dst_path = '{dst}'
replace = {replace}

# Split destination into folder + asset name
pkg_folder, asset_name = dst_path.rsplit('/', 1)

# Check existing
exists = unreal.EditorAssetLibrary.does_asset_exist(dst_path)
if exists and not replace:
    print(_dump({{"ok": False, "error": "asset already exists — set replace_existing=True to overwrite", "path": dst_path}}))
else:
    if exists and replace:
        unreal.EditorAssetLibrary.delete_asset(dst_path)

    task = unreal.AssetImportTask()
    task.filename = src_file
    task.destination_path = pkg_folder
    task.destination_name = asset_name
    task.replace_existing = replace
    task.automated = True
    task.save = True

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    tools.import_asset_tasks([task])

    imported = task.get_editor_property('imported_object_paths') or []
    if imported:
        # Verify the USoundWave loaded
        wave = unreal.load_asset(dst_path)
        if wave and isinstance(wave, unreal.SoundWave):
            duration = wave.get_editor_property('duration') if hasattr(wave, 'get_editor_property') else None
            print(_dump({{
                "ok": True,
                "path": dst_path,
                "class": wave.get_class().get_name(),
                "duration_s": float(duration) if duration else None,
            }}))
        else:
            print(_dump({{"ok": True, "path": dst_path, "imported": [str(p) for p in imported]}}))
    else:
        print(_dump({{"ok": False, "error": "import returned no assets — check UE5 output log for details"}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_sound_set_attenuation",
    category="ue5_audio",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["sound-attenuation"],
    title="Set Sound Attenuation",
)
def ue5_sound_set_attenuation(
    attenuation_path: Annotated[str, "SoundAttenuation asset path (/Game/Audio/Atten_Explosion)"],
    inner_radius: Annotated[float, "Inner radius (cm) — full volume inside this sphere"] = 400.0,
    falloff_distance: Annotated[float, "Falloff distance (cm) — linear/log fade past inner_radius"] = 4000.0,
    falloff_mode: Annotated[Literal["linear", "logarithmic", "inverse", "log_reverse", "natural_sound"], "Distance falloff model"] = "natural_sound",
    spatialize: Annotated[bool, "Enable 3D spatialization (stereo positioning)"] = True,
    create_if_missing: Annotated[bool, "Create the asset if it doesn't exist"] = True,
) -> ToolResult:
    """Configure attenuation settings on a SoundAttenuation asset.

    Attenuation controls how audio volume falls off with distance from the
    listener. Used for biome ambience (long falloff), weapon fire (medium),
    footsteps (short). Creates the asset if missing when create_if_missing=True.
    """
    if not attenuation_path.startswith("/Game/"):
        return ToolResult.failure("attenuation_path must start with /Game/")
    if inner_radius < 0 or falloff_distance < 0:
        return ToolResult.failure("radius and falloff_distance must be non-negative")

    ap = escape_path(attenuation_path)
    mode_map = {
        "linear": "LINEAR",
        "logarithmic": "LOGARITHMIC",
        "inverse": "INVERSE",
        "log_reverse": "LOG_REVERSE",
        "natural_sound": "NATURAL_SOUND",
    }
    enum_suffix = mode_map.get(falloff_mode, "NATURAL_SOUND")
    create = "True" if create_if_missing else "False"
    spatial = "True" if spatialize else "False"

    body = f"""
ap = '{ap}'
create_missing = {create}

atten = unreal.load_asset(ap)
if not atten and create_missing:
    # Create new SoundAttenuation asset
    pkg_folder, asset_name = ap.rsplit('/', 1)
    factory = unreal.SoundAttenuationFactory()
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    atten = tools.create_asset(asset_name, pkg_folder, unreal.SoundAttenuation, factory)

if not atten:
    print(_dump({{"ok": False, "error": f"SoundAttenuation not found and create_if_missing=False: {{ap}}"}}))
else:
    # Get the inner 'attenuation' property (FSoundAttenuationSettings)
    settings = atten.get_editor_property('attenuation')
    if not settings:
        print(_dump({{"ok": False, "error": "attenuation settings struct missing"}}))
    else:
        try:
            settings.set_editor_property('falloff_distance', float({falloff_distance}))
            # Inner radius varies by attenuation shape (Sphere uses attenuation_shape_extents.x)
            extents = unreal.Vector({inner_radius}, {inner_radius}, {inner_radius})
            settings.set_editor_property('attenuation_shape_extents', extents)
            settings.set_editor_property('distance_algorithm', unreal.AttenuationDistanceModel.{enum_suffix})
            settings.set_editor_property('b_spatialize', {spatial})
            settings.set_editor_property('b_attenuate', True)

            atten.set_editor_property('attenuation', settings)
            unreal.EditorAssetLibrary.save_asset(ap)
            print(_dump({{
                "ok": True,
                "path": ap,
                "inner_radius": {inner_radius},
                "falloff_distance": {falloff_distance},
                "mode": '{falloff_mode}',
                "spatialize": {spatial},
            }}))
        except Exception as _ae:
            print(_dump({{"ok": False, "error": str(_ae)}}))
"""
    return run_python(wrap_script(body))
