"""Create /Game/Tests/BP_EventGraphSmoke (Actor BP) for EventGraph v0.5.11 smoke."""
import unreal

ASSET_PATH = "/Game/Tests/BP_EventGraphSmoke"
PACKAGE_PATH = "/Game/Tests"
ASSET_NAME = "BP_EventGraphSmoke"


def main():
    if unreal.EditorAssetLibrary.does_asset_exist(ASSET_PATH):
        unreal.log(f"[smoke-bp] Already exists: {ASSET_PATH}")
        return

    if not unreal.EditorAssetLibrary.does_directory_exist(PACKAGE_PATH):
        unreal.EditorAssetLibrary.make_directory(PACKAGE_PATH)
        unreal.log(f"[smoke-bp] Created dir: {PACKAGE_PATH}")

    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", unreal.Actor)

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    new_bp = asset_tools.create_asset(ASSET_NAME, PACKAGE_PATH, None, factory)

    if new_bp is None:
        unreal.log_error(f"[smoke-bp] FAILED to create: {ASSET_PATH}")
        return

    unreal.EditorAssetLibrary.save_asset(ASSET_PATH)
    unreal.log(f"[smoke-bp] CREATED + SAVED: {ASSET_PATH}")


main()
