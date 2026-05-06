// Copyright Jacob Ribbe. Licensed under MIT.

#pragma once

#include "CoreMinimal.h"
#include "BionicsBridgeToolBase.h"

/**
 * Singleton registry of all BionicsBridge tools.
 *
 * Tools register themselves at module startup via RegisterToolClass<T>().
 * Instances are root-referenced to prevent GC.
 */
class BIONICSBRIDGE_API FBionicsBridgeToolRegistry
{
public:
	/** Access the process-wide registry. */
	static FBionicsBridgeToolRegistry& Get();

	/** Register a tool class. Creates one instance and roots it. */
	template<typename T>
	void RegisterToolClass()
	{
		static_assert(TIsDerivedFrom<T, UBionicsBridgeToolBase>::IsDerived,
		              "Tool must derive from UBionicsBridgeToolBase");
		T* Instance = NewObject<T>();
		Instance->AddToRoot();
		const FString Name = Instance->GetToolName();
		if (!Name.IsEmpty())
		{
			Tools.Add(Name, Instance);
		}
	}

	/** Look up a tool by name. */
	UBionicsBridgeToolBase* FindTool(const FString& Name) const;

	/** List all registered tool names. */
	TArray<FString> GetToolNames() const;

	/** List all registered tools with their metadata for tools/list. */
	TSharedPtr<FJsonObject> GetToolsListJson() const;

	/** Total tool count. */
	int32 Num() const { return Tools.Num(); }

	/** Clear registry (for module shutdown). */
	void Shutdown();

private:
	FBionicsBridgeToolRegistry() = default;
	~FBionicsBridgeToolRegistry() = default;

	TMap<FString, UBionicsBridgeToolBase*> Tools;
};
