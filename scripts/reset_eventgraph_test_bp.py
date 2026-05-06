"""Delete + recreate /Game/Tests/BP_EventGraphSmoke for a clean smoke run."""
import unreal

ASSET_PATH = "/Game/Tests/BP_EventGraphSmoke"
PACKAGE_PATH = "/Game/Tests"
ASSET_NAME = "BP_EventGraphSmoke"


def main():
    if unreal.EditorAssetLibrary.does_asset_exist(ASSET_PATH):
        ok = unreal.EditorAssetLibrary.delete_asset(ASSET_PATH)
        unreal.log(f"[smoke-reset] deleted {ASSET_PATH}: {ok}")

    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", unreal.Actor)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    new_bp = asset_tools.create_asset(ASSET_NAME, PACKAGE_PATH, None, factory)
    if new_bp is None:
        unreal.log_error(f"[smoke-reset] FAILED to create {ASSET_PATH}")
        return
    unreal.EditorAssetLibrary.save_asset(ASSET_PATH)
    unreal.log(f"[smoke-reset] CREATED + SAVED {ASSET_PATH}")


main()
