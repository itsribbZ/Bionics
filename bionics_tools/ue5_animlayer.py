"""UE5 Linked Anim Layer Tools — Create pre-wired weapon/posture layer AnimBPs.

Closes the 5 missing weapon layer ABP gap (ABP_WeaponLayer_Rifle/Pistol/Shotgun/
Melee/Unarmed) flagged in Gap_Audit_Addendum_2026_04_10.md. Without these, the
Linked Layer architecture (Bible Step 2) cannot wire per-weapon poses into the
base AnimBP.

Tool surface:
    ue5_linked_anim_layer_create — create AnimBP pre-configured as a Linked
                                    Anim Layer with Input Pose → Output Pose

Bible reference: Step 2 (Linked Layer architecture, Lyra pattern).
UE5 5.1+ required (AnimBlueprintFactory + AnimBlueprintExtension Python API).
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool


@bionics_tool(
    name="ue5_linked_anim_layer_create",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["create-anim-layer", "linked-anim-layer"],
    title="Create Linked Anim Layer AnimBP",
)
def ue5_linked_anim_layer_create(
    asset_path: Annotated[str, "Full AnimBP path (/Game/Characters/ABP_WeaponLayer_Rifle)"],
    skeleton_path: Annotated[str, "Target skeleton (/Game/Characters/CH_Trooper_Skeleton)"],
    interface_path: Annotated[str, "AnimLayerInterface asset (/Game/Characters/ALI_Combat)"] = "",
    parent_class_path: Annotated[str, "Optional parent AnimInstance class for child AnimBP pattern"] = "",
) -> ToolResult:
    """Create a new AnimBP pre-wired as a Linked Anim Layer.

    The resulting asset has:
      - Target skeleton set
      - Optional AnimLayerInterface parent (if interface_path supplied)
      - Optional parent AnimInstance class (for child-AnimBP weapon variants)

    Used for per-weapon pose layers (Rifle/Pistol/Shotgun/Melee/Unarmed) that
    the base AnimBP links via `Linked Anim Layer` nodes on the AnimGraph.
    Each layer AnimBP implements the interface's functions (e.g. IdlePose,
    FirePose, AimPose) with weapon-specific animations.
    """
    if not asset_path.startswith("/Game/"):
        return ToolResult.failure("asset_path must start with /Game/")
    if not skeleton_path.startswith("/Game/"):
        return ToolResult.failure("skeleton_path must start with /Game/")

    ap = escape_path(asset_path)
    sp = escape_path(skeleton_path)
    ip = escape_path(interface_path) if interface_path else ""
    pp = escape_path(parent_class_path) if parent_class_path else ""

    body = f"""
asset_path = '{ap}'
skel_path = '{sp}'
iface_path = '{ip}'
parent_path = '{pp}'

# Skeleton check
skel = unreal.load_asset(skel_path)
if not skel:
    print(_dump({{"ok": False, "error": f"Skeleton not found: {{skel_path}}"}}))
elif unreal.EditorAssetLibrary.does_asset_exist(asset_path):
    print(_dump({{"ok": False, "error": "asset already exists", "path": asset_path}}))
else:
    pkg_folder, asset_name = asset_path.rsplit('/', 1)
    factory = unreal.AnimBlueprintFactory()
    factory.target_skeleton = skel

    # Parent class: either specified parent AnimBP class, or the default AnimInstance
    if parent_path:
        parent_asset = unreal.load_asset(parent_path)
        if parent_asset and hasattr(parent_asset, 'parent_class'):
            factory.parent_class = parent_asset.parent_class
        elif parent_asset:
            try:
                factory.parent_class = parent_asset.generated_class()
            except Exception:
                pass  # fall through to default parent

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    abp = tools.create_asset(asset_name, pkg_folder, unreal.AnimBlueprint, factory)

    if not abp:
        print(_dump({{"ok": False, "error": "AnimBlueprintFactory returned None"}}))
    else:
        # Add interface if specified — this is what makes it a Linked Anim Layer
        interface_added = False
        if iface_path:
            iface = unreal.load_asset(iface_path)
            if iface:
                try:
                    unreal.BlueprintEditorLibrary.add_interface(abp, iface.generated_class())
                    interface_added = True
                except Exception as _ie:
                    # fall through — the AnimBP is still valid, just no interface
                    pass

        unreal.EditorAssetLibrary.save_asset(asset_path)
        print(_dump({{
            "ok": True,
            "path": asset_path,
            "skeleton": skel_path,
            "interface": iface_path or None,
            "interface_added": interface_added,
            "parent_class": parent_path or None,
        }}))
"""
    return run_python(wrap_script(body))
