"""UE5 Rigging Tools — IK Rig + IK Retargeter automation.

Fills Bible Step 3 (retarget anims to Trooper skeleton). Unblocks the
retarget pipeline which was entirely absent from Bionics before 2026-04-16.

Tool surface:
    ue5_create_ik_rig           — create IKRigDefinition asset + bind skeletal mesh
    ue5_ik_rig_add_chain        — add a retarget chain (e.g. Spine, Arm_L)
    ue5_create_ik_retargeter    — create IKRetargeter asset bridging two IK Rigs
    ue5_batch_retarget          — duplicate-and-retarget a folder of anims

Bible reference: Model Animations Bible Ch.5 (Retargeting), Ch.6 (IK).
UE5 5.4+ required for full IK Rig + IKRetargeter Python API.
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# IK RIG CREATION
# ============================================================================


@bionics_tool(
    name="ue5_create_ik_rig",
    category="ue5_rigging",
    safety_tier=SafetyTier.MODERATE,
    aliases=["create-ik-rig"],
    title="Create IK Rig",
)
def ue5_create_ik_rig(
    rig_path: Annotated[str, "Full asset path (/Game/Characters/IK/IK_Trooper)"],
    skeleton_path: Annotated[str, "Skeleton to bind (/Game/...)"],
    preview_mesh_path: Annotated[str, "Optional preview skeletal mesh"] = "",
) -> ToolResult:
    """Create a new IKRigDefinition asset bound to a target skeleton.

    This is the foundation for retargeting — one IK Rig per skeleton family
    (source mannequin, target Trooper, etc.). Populate with retarget chains via
    `ue5_ik_rig_add_chain` after creation.
    """
    rp = escape_path(rig_path)
    sp = escape_path(skeleton_path)
    pp = escape_path(preview_mesh_path)
    body = f"""
rig_path = '{rp}'
skel_path = '{sp}'
preview_path = '{pp}'

# Check if already exists
existing = unreal.load_asset(rig_path)
if existing:
    print(_dump({{"ok": False, "error": "IK Rig already exists at path", "path": rig_path}}))
else:
    factory = unreal.IKRigDefinitionFactory()
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    pkg_path, asset_name = rig_path.rsplit('/', 1)
    rig = asset_tools.create_asset(asset_name, pkg_path, unreal.IKRigDefinition, factory)

    if not rig:
        print(_dump({{"ok": False, "error": "Factory returned None", "path": rig_path}}))
    else:
        # Get the controller to configure the rig
        controller = unreal.IKRigController.get_controller(rig)
        skel = unreal.load_asset(skel_path) if skel_path else None
        if skel:
            # Resolve skeleton's preview mesh
            preview = unreal.load_asset(preview_path) if preview_path else skel.get_preview_mesh()
            if preview:
                controller.set_skeletal_mesh(preview)

        unreal.EditorAssetLibrary.save_asset(rig_path)
        print(_dump({{"ok": True, "path": rig_path, "skeleton_bound": bool(skel)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_ik_rig_add_chain",
    category="ue5_rigging",
    safety_tier=SafetyTier.MODERATE,
    aliases=["ik-rig-chain"],
    title="Add IK Rig Retarget Chain",
)
def ue5_ik_rig_add_chain(
    rig_path: Annotated[str, "IK Rig asset path"],
    chain_name: Annotated[str, "Chain name (Spine, Arm_L, Leg_R, Head)"],
    start_bone: Annotated[str, "Root bone of chain (e.g. spine_01)"],
    end_bone: Annotated[str, "Tip bone of chain (e.g. spine_05)"],
    goal_bone: Annotated[str, "Optional IK goal bone"] = "",
) -> ToolResult:
    """Add a retarget chain to an IK Rig.

    Chains define which bones flow to the animation retarget. Standard chain
    set for humanoid rigs: Root, Spine, Neck, Head, Clavicle_L/R, Arm_L/R,
    Hand_L/R, Leg_L/R, Foot_L/R.
    """
    rp = escape_path(rig_path)
    cn = escape_path(chain_name)
    sb = escape_path(start_bone)
    eb = escape_path(end_bone)
    gb = escape_path(goal_bone)
    body = f"""
rig_path = '{rp}'
rig = unreal.load_asset(rig_path)
if not rig:
    print(_dump({{"ok": False, "error": "IK Rig not found", "path": rig_path}}))
else:
    controller = unreal.IKRigController.get_controller(rig)
    chain = unreal.BoneChain(
        chain_name='{cn}',
        start_bone=unreal.BoneReference(bone_name='{sb}'),
        end_bone=unreal.BoneReference(bone_name='{eb}'),
    )
    if '{gb}':
        chain.set_editor_property('ik_goal_name', '{gb}')
    ok = controller.add_retarget_chain(chain)
    unreal.EditorAssetLibrary.save_asset(rig_path)
    print(_dump({{"ok": bool(ok), "chain": '{cn}', "start": '{sb}', "end": '{eb}'}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# IK RETARGETER
# ============================================================================


@bionics_tool(
    name="ue5_create_ik_retargeter",
    category="ue5_rigging",
    safety_tier=SafetyTier.MODERATE,
    aliases=["create-retargeter"],
    title="Create IK Retargeter",
)
def ue5_create_ik_retargeter(
    retargeter_path: Annotated[str, "Full asset path (/Game/.../RTG_Mannequin_To_Trooper)"],
    source_rig_path: Annotated[str, "Source IK Rig (/Game/Mannequin/IK_Mannequin)"],
    target_rig_path: Annotated[str, "Target IK Rig (/Game/Characters/IK/IK_Trooper)"],
) -> ToolResult:
    """Create an IKRetargeter asset bridging two IK Rigs.

    Retargeters are the transform-space adapter between skeletons. Used by
    `ue5_batch_retarget` to adapt animation data from source to target skeleton.
    """
    rtp = escape_path(retargeter_path)
    sp = escape_path(source_rig_path)
    tp = escape_path(target_rig_path)
    body = f"""
rtg_path = '{rtp}'
src_rig_path = '{sp}'
tgt_rig_path = '{tp}'

src_rig = unreal.load_asset(src_rig_path)
tgt_rig = unreal.load_asset(tgt_rig_path)
if not src_rig:
    print(_dump({{"ok": False, "error": f"Source IK Rig not found: {{src_rig_path}}"}}))
elif not tgt_rig:
    print(_dump({{"ok": False, "error": f"Target IK Rig not found: {{tgt_rig_path}}"}}))
else:
    existing = unreal.load_asset(rtg_path)
    if existing:
        print(_dump({{"ok": False, "error": "Retargeter already exists", "path": rtg_path}}))
    else:
        factory = unreal.IKRetargetFactory()
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        pkg_path, asset_name = rtg_path.rsplit('/', 1)
        rtg = asset_tools.create_asset(asset_name, pkg_path, unreal.IKRetargeter, factory)

        if not rtg:
            print(_dump({{"ok": False, "error": "Factory returned None"}}))
        else:
            controller = unreal.IKRetargeterController.get_controller(rtg)
            controller.set_ik_rig(unreal.RetargetSourceOrTarget.SOURCE, src_rig)
            controller.set_ik_rig(unreal.RetargetSourceOrTarget.TARGET, tgt_rig)
            unreal.EditorAssetLibrary.save_asset(rtg_path)
            print(_dump({{"ok": True, "path": rtg_path, "source": src_rig_path, "target": tgt_rig_path}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_batch_retarget",
    category="ue5_rigging",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    strict=True,
    aliases=["batch-retarget"],
    title="Batch Retarget Animations",
)
def ue5_batch_retarget(
    source_folder: Annotated[str, "Source anim folder (/Game/Mannequin/Anims)"],
    target_folder: Annotated[str, "Output folder for retargeted anims"],
    retargeter_path: Annotated[str, "IKRetargeter asset"],
    search_str: Annotated[str, "Find string in asset name (replace with target)"] = "",
    replace_str: Annotated[str, "Replace string in asset name"] = "",
    prefix: Annotated[str, "Prefix for output asset names"] = "",
    suffix: Annotated[str, "Suffix for output asset names"] = "",
) -> ToolResult:
    """Duplicate-and-retarget all AnimSequences in a folder.

    Applies the retargeter to every AnimSequence found recursively under
    source_folder, writes results to target_folder with optional name mangling.
    Bible Step 3 for the Game Animation Sample → Trooper retarget sweep.
    """
    src = escape_path(source_folder)
    tgt = escape_path(target_folder)
    rtg = escape_path(retargeter_path)
    ss = escape_path(search_str)
    rs = escape_path(replace_str)
    pf = escape_path(prefix)
    sf = escape_path(suffix)
    body = f"""
source_folder = '{src}'
target_folder = '{tgt}'
rtg_path = '{rtg}'

rtg = unreal.load_asset(rtg_path)
if not rtg:
    print(_dump({{"ok": False, "error": f"Retargeter not found: {{rtg_path}}"}}))
else:
    # Discover all AnimSequences in source folder
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    f = unreal.ARFilter(
        package_paths=[source_folder],
        recursive_paths=True,
        class_names=['AnimSequence'],
    )
    asset_data_list = ar.get_assets(f)

    if not asset_data_list:
        print(_dump({{"ok": False, "error": "No AnimSequences found under source_folder"}}))
    else:
        # Run batch retarget — IKRetargetBatchOperation needs AssetData, not loaded objects
        batch_op = unreal.IKRetargetBatchOperation()
        name_rule = unreal.IKRetargetBatchOperationNameRule(
            folder_path=target_folder,
            prefix='{pf}',
            suffix='{sf}',
            rename_from='{ss}',
            rename_to='{rs}',
        )
        results = batch_op.duplicate_and_retarget(
            assets_to_retarget=list(asset_data_list),
            source_ik_rig_asset_data=None,
            target_ik_rig_asset_data=None,
            retarget_asset=rtg,
            name_rule=name_rule,
            include_referenced_assets=True,
        )
        print(_dump({{
            "ok": True,
            "source_folder": source_folder,
            "target_folder": target_folder,
            "retargeter": rtg_path,
            "count": len(asset_data_list),
            "results_count": len(results) if results else 0,
        }}))
"""
    return run_python(wrap_script(body))
