// Copyright Jacob Ribbe. Licensed under MIT.

using UnrealBuildTool;

public class BionicsBridgeEditor : ModuleRules
{
	public BionicsBridgeEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;
		bLegacyPublicIncludePaths = false;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"BionicsBridge",    // depends on the runtime module
			"Json",
			"JsonUtilities",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"UnrealEd",
			"AssetTools",
			"AssetRegistry",
			"Kismet",
			"KismetCompiler",
			"BlueprintGraph",
			"Projects",
			"EditorScriptingUtilities", // EditorAssetLibrary

			// AnimGraph manipulation — full programmatic AnimBP control
			"AnimGraph",
			"AnimGraphRuntime",
			"AnimationBlueprintEditor", // Required for AnimBP node-class resolution in ResolveNodeClass()
			"Persona",             // Animation editor utilities + schema actions

			// BPDoctor integration — programmatic scan + fix
			"BPDoctor",

			// PoseSearch — Motion Matching AnimGraph node support (Bible-aligned AAA locomotion)
			"PoseSearch",
			"PoseSearchEditor",

			// Control Rig editor — guarantees ControlRigEditor module loads for ResolveNodeClass() lookup
			"ControlRigEditor",
		});
	}
}
