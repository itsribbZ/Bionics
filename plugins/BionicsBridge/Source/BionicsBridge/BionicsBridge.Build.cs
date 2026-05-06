// Copyright Jacob Ribbe. Licensed under MIT.

using UnrealBuildTool;

public class BionicsBridge : ModuleRules
{
	public BionicsBridge(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;
		bLegacyPublicIncludePaths = false;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"HTTP",
			"HTTPServer",
			"Sockets",
			"Networking",
			"Json",
			"JsonUtilities",
			"Projects",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"InputCore",
		});

		// Editor-only extras for editor-world actor spawning
		if (Target.bBuildEditor)
		{
			PrivateDependencyModuleNames.Add("UnrealEd");
			// UEditorSubsystem base is provided by UnrealEd; no separate module needed
		}

		// Windows-only: link Advapi32 for Win32 security APIs (DACL hardening on
		// .bionics-bridge/instance.json via SetNamedSecurityInfoW / SetEntriesInAclW).
		if (Target.Platform == UnrealTargetPlatform.Win64)
		{
			PublicSystemLibraries.Add("Advapi32.lib");
		}
	}
}
