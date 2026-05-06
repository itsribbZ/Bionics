"""UE5 Asset Tools — Create, Save, Delete, Query, Preview assets.

Matches soft-ue-cli's asset management surface.
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool


@bionics_tool(
    name="ue5_query_asset",
    category="ue5_asset",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["query-asset"],
    title="Query Asset",
)
def ue5_query_asset(
    query: Annotated[str, "Name substring"] = "",
    class_filter: Annotated[str, "Asset class name filter"] = "",
    path: Annotated[str, "Path prefix (/Game/...)"] = "/Game",
    limit: int = 100,
) -> ToolResult:
    """Search Content Browser for assets by name/class/path."""
    q = escape_path(query)
    cf = escape_path(class_filter)
    p = escape_path(path)
    body = f"""
ar = unreal.AssetRegistryHelpers.get_asset_registry()
filter = unreal.ARFilter(package_paths=['{p}'], recursive_paths=True)
if '{cf}':
    filter.class_names = ['{cf}']
assets = ar.get_assets(filter)
results = []
q = '{q}'.lower()
for a in assets:
    name = str(a.asset_name)
    if q and q not in name.lower():
        continue
    results.append({{
        "name": name,
        "class": str(a.asset_class_path.asset_name) if hasattr(a, 'asset_class_path') else str(a.asset_class),
        "path": str(a.package_name),
    }})
    if len(results) >= {limit}:
        break
print(_dump({{"assets": results, "count": len(results)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_create_asset",
    category="ue5_asset",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["create-asset"],
    title="Create Asset",
)
def ue5_create_asset(
    asset_path: Annotated[str, "Full asset path (/Game/Blueprints/NewBP)"],
    asset_class: Annotated[str, "Blueprint|Material|DataTable|WidgetBlueprint|AnimBlueprint"],
    parent_class: Annotated[str, "Parent class (for Blueprint) e.g. Actor"] = "",
) -> ToolResult:
    """Create a new asset (Blueprint, Material, DataTable, etc.)."""
    ap = escape_path(asset_path)
    ac = asset_class
    pc = escape_path(parent_class)
    body = f"""
import os.path
pkg_path, asset_name = '{ap}'.rsplit('/', 1)
factory = None
cls_map = {{
    'Blueprint': (unreal.BlueprintFactory, unreal.Blueprint),
    'Material': (unreal.MaterialFactoryNew, unreal.Material),
    'DataTable': (unreal.DataTableFactory, unreal.DataTable),
    'WidgetBlueprint': (unreal.WidgetBlueprintFactory, unreal.WidgetBlueprint),
    'AnimBlueprint': (unreal.AnimBlueprintFactory, unreal.AnimBlueprint),
}}
if '{ac}' not in cls_map:
    print(_dump({{"error": "unsupported class: {ac}"}}))
else:
    factory_cls, base_cls = cls_map['{ac}']
    factory = factory_cls()
    if '{ac}' == 'Blueprint' and '{pc}':
        parent = getattr(unreal, '{pc}', None)
        if parent is None:
            print(_dump({{"error": "parent_class not found: {pc}"}}))
            raise RuntimeError("parent_class not found: {pc}")
        factory.set_editor_property('parent_class', parent)
    try:
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        new_asset = asset_tools.create_asset(asset_name, pkg_path, base_cls, factory)
        if new_asset:
            unreal.EditorAssetLibrary.save_asset('{ap}')
            print(_dump({{"ok": True, "path": '{ap}', "class": '{ac}'}}))
        else:
            print(_dump({{"error": "creation returned None"}}))
    except Exception as _ce:
        print(_dump({{"error": str(_ce)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_delete_asset",
    category="ue5_asset",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    strict=True,
    aliases=["delete-asset"],
    title="Delete Asset",
)
def ue5_delete_asset(asset_path: str) -> ToolResult:
    """Delete an asset from the Content Browser."""
    ap = escape_path(asset_path)
    body = f"""
if not unreal.EditorAssetLibrary.does_asset_exist('{ap}'):
    print(_dump({{"error": "asset not found"}}))
else:
    try:
        ok = unreal.EditorAssetLibrary.delete_asset('{ap}')
        print(_dump({{"ok": ok, "deleted": '{ap}'}}))
    except Exception as _de:
        print(_dump({{"error": str(_de)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_save_asset",
    category="ue5_asset",
    safety_tier=SafetyTier.MODERATE,
    aliases=["save-asset"],
    title="Save Asset",
)
def ue5_save_asset(asset_path: str) -> ToolResult:
    """Save a dirty asset to disk."""
    ap = escape_path(asset_path)
    body = f"""
if not unreal.EditorAssetLibrary.does_asset_exist('{ap}'):
    print(_dump({{"error": "asset not found"}}))
else:
    try:
        ok = unreal.EditorAssetLibrary.save_asset('{ap}')
        print(_dump({{"ok": ok, "saved": '{ap}'}}))
    except Exception as _se:
        print(_dump({{"error": str(_se)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_save_all_dirty",
    category="ue5_asset",
    safety_tier=SafetyTier.MODERATE,
    title="Save All Dirty Assets",
)
def ue5_save_all_dirty() -> ToolResult:
    """Save all modified (dirty) assets in the project."""
    body = """
try:
    ok = unreal.EditorAssetLibrary.save_loaded_assets_in_folder('/Game', only_if_is_dirty=True)
    print(_dump({"ok": bool(ok)}))
except Exception as _se:
    try:
        unreal.EditorAssetLibrary.save_directory('/Game', only_if_is_dirty=True)
        print(_dump({"ok": True}))
    except Exception as _e2:
        print(_dump({"error": str(_e2)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_open_asset",
    category="ue5_asset",
    safety_tier=SafetyTier.MODERATE,
    aliases=["open-asset"],
    title="Open Asset",
)
def ue5_open_asset(asset_path: str) -> ToolResult:
    """Open an asset in the editor."""
    ap = escape_path(asset_path)
    body = f"""
asset = unreal.load_asset('{ap}')
if not asset:
    print(_dump({{"error": "asset not found"}}))
else:
    try:
        editor = unreal.AssetToolsHelpers.get_asset_tools()
        unreal.AssetEditorSubsystem = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem) if hasattr(unreal, 'get_editor_subsystem') else None
        if unreal.AssetEditorSubsystem:
            unreal.AssetEditorSubsystem.open_editor_for_assets([asset])
        else:
            unreal.EditorAssetLibrary.sync_browser_to_objects([asset])
        print(_dump({{"ok": True, "opened": '{ap}'}}))
    except Exception as _oe:
        print(_dump({{"error": str(_oe)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_duplicate_asset",
    category="ue5_asset",
    safety_tier=SafetyTier.MODERATE,
    title="Duplicate Asset",
)
def ue5_duplicate_asset(
    source_path: str,
    destination_path: str,
) -> ToolResult:
    """Duplicate an asset to a new path."""
    sp = escape_path(source_path)
    dp = escape_path(destination_path)
    body = f"""
if not unreal.EditorAssetLibrary.does_asset_exist('{sp}'):
    print(_dump({{"error": "source not found"}}))
else:
    try:
        ok = unreal.EditorAssetLibrary.duplicate_asset('{sp}', '{dp}')
        print(_dump({{"ok": ok, "duplicated": '{dp}'}}))
    except Exception as _de:
        print(_dump({{"error": str(_de)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_rename_asset",
    category="ue5_asset",
    safety_tier=SafetyTier.MODERATE,
    title="Rename Asset",
)
def ue5_rename_asset(source_path: str, destination_path: str) -> ToolResult:
    """Rename/move an asset."""
    sp = escape_path(source_path)
    dp = escape_path(destination_path)
    body = f"""
try:
    ok = unreal.EditorAssetLibrary.rename_asset('{sp}', '{dp}')
    print(_dump({{"ok": ok, "renamed_to": '{dp}'}}))
except Exception as _re:
    print(_dump({{"error": str(_re)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_asset_info",
    category="ue5_asset",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Asset Info",
    output_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "exists": {"type": "boolean"},
            "class": {"type": "string"},
            "name": {"type": "string"},
            "tags": {"type": "object", "additionalProperties": {"type": "string"}},
            "error": {"type": "string"},
        },
    },
)
def ue5_asset_info(asset_path: str) -> ToolResult:
    """Return metadata about an asset (class, size, tags)."""
    ap = escape_path(asset_path)
    body = f"""
if not unreal.EditorAssetLibrary.does_asset_exist('{ap}'):
    print(_dump({{"error": "asset not found"}}))
else:
    asset = unreal.load_asset('{ap}')
    info = {{
        "path": '{ap}',
        "exists": True,
        "class": asset.get_class().get_name() if asset else None,
        "name": asset.get_name() if asset else None,
    }}
    try:
        tags = unreal.EditorAssetLibrary.get_metadata_tag_values('{ap}')
        info["tags"] = dict(tags) if tags else {{}}
    except Exception as _e:
        info["tags"] = {{}}  # metadata tags unavailable
    print(_dump(info))
"""
    return run_python(wrap_script(body))


# ============================================================================
# DATAASSET BULK OPERATIONS
# ============================================================================


@bionics_tool(
    name="ue5_dataasset_bulk_set",
    category="ue5_asset",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["dataasset-bulk", "bulk-set-properties"],
    title="DataAsset Bulk Set Properties",
)
def ue5_dataasset_bulk_set(
    records: Annotated[list[dict], "List of {asset_path, property, value} dicts"],
    save_after: Annotated[bool, "Save each asset after modification"] = True,
) -> ToolResult:
    """Batch set_editor_property + save across N assets.

    Replaces the hand-written Python scripts (e.g. bionics_wire_vertical_slice.json
    Phase 2-4) that populate 40+ DataAsset shells one property at a time.
    Each record is {asset_path: "/Game/...", property: "field_name", value: Any}.

    Value types: str, int, float, bool, list[float] (for Vector/Rotator),
    or dict with {"asset_ref": "/Game/..."} to set an object reference.

    Returns per-record success/failure with exception messages.
    """
    if not isinstance(records, list) or not records:
        return ToolResult.failure("records must be a non-empty list")
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            return ToolResult.failure(f"records[{i}] must be a dict")
        if "asset_path" not in rec or "property" not in rec:
            return ToolResult.failure(f"records[{i}] missing 'asset_path' or 'property'")

    from bionics_tools._ue5_common import safe_json_literal
    records_b64 = safe_json_literal(records)
    save = "True" if save_after else "False"
    body = f"""
import base64 as _b64
records = json.loads(_b64.b64decode('{records_b64}').decode('utf-8'))
save_after = {save}

results = []
for rec in records:
    ap = rec.get('asset_path', '')
    prop = rec.get('property', '')
    val = rec.get('value', None)
    if not ap or not prop:
        results.append({{"path": ap, "property": prop, "ok": False, "error": "missing asset_path or property"}})
        continue

    asset = unreal.load_asset(ap)
    if not asset:
        results.append({{"path": ap, "property": prop, "ok": False, "error": "asset not found"}})
        continue

    try:
        # Resolve value — asset ref dicts get loaded to UObject
        if isinstance(val, dict) and 'asset_ref' in val:
            val = unreal.load_asset(val['asset_ref'])
            if val is None:
                results.append({{"path": ap, "property": prop, "ok": False, "error": f"asset_ref not found: {{rec['value']['asset_ref']}}"}})
                continue
        elif isinstance(val, list) and len(val) == 3 and all(isinstance(v, (int, float)) for v in val):
            # [x, y, z] vector
            val = unreal.Vector(float(val[0]), float(val[1]), float(val[2]))
        elif isinstance(val, list) and len(val) == 4 and all(isinstance(v, (int, float)) for v in val):
            # [r, g, b, a] linear color
            val = unreal.LinearColor(float(val[0]), float(val[1]), float(val[2]), float(val[3]))

        asset.set_editor_property(prop, val)
        if save_after:
            unreal.EditorAssetLibrary.save_asset(ap)
        results.append({{"path": ap, "property": prop, "ok": True}})
    except Exception as _pe:
        results.append({{"path": ap, "property": prop, "ok": False, "error": str(_pe)}})

ok_count = sum(1 for r in results if r.get("ok"))
print(_dump({{
    "ok": ok_count == len(results),
    "count": len(results),
    "succeeded": ok_count,
    "failed": len(results) - ok_count,
    "results": results,
}}))
"""
    return run_python(wrap_script(body))
