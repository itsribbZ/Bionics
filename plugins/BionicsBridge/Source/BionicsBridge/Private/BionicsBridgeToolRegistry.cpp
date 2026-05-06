// Copyright Jacob Ribbe. Licensed under MIT.

#include "BionicsBridgeToolRegistry.h"
#include "BionicsBridgeModule.h"

FBionicsBridgeToolRegistry& FBionicsBridgeToolRegistry::Get()
{
	static FBionicsBridgeToolRegistry Instance;
	return Instance;
}

UBionicsBridgeToolBase* FBionicsBridgeToolRegistry::FindTool(const FString& Name) const
{
	if (UBionicsBridgeToolBase* const* Found = Tools.Find(Name))
	{
		return *Found;
	}
	return nullptr;
}

TArray<FString> FBionicsBridgeToolRegistry::GetToolNames() const
{
	TArray<FString> Names;
	Tools.GetKeys(Names);
	Names.Sort();
	return Names;
}

TSharedPtr<FJsonObject> FBionicsBridgeToolRegistry::GetToolsListJson() const
{
	TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
	TArray<TSharedPtr<FJsonValue>> ToolsArray;

	for (const auto& Pair : Tools)
	{
		UBionicsBridgeToolBase* Tool = Pair.Value;
		if (!Tool) continue;

		TSharedPtr<FJsonObject> ToolJson = MakeShared<FJsonObject>();
		ToolJson->SetStringField(TEXT("name"), Tool->GetToolName());
		ToolJson->SetStringField(TEXT("description"), Tool->GetToolDescription());
		ToolJson->SetStringField(TEXT("category"), Tool->GetCategory());
		if (TSharedPtr<FJsonObject> Schema = Tool->GetInputSchema())
		{
			ToolJson->SetObjectField(TEXT("inputSchema"), Schema);
		}
		ToolsArray.Add(MakeShared<FJsonValueObject>(ToolJson));
	}

	Result->SetArrayField(TEXT("tools"), ToolsArray);
	return Result;
}

void FBionicsBridgeToolRegistry::Shutdown()
{
	// During engine exit, GC may have collected our tools and torn down the
	// UObject array. Touching IsRooted() / RemoveFromRoot() in that state
	// crashes via FUObjectArray::IndexToObject (assertion Index >= 0).
	// Skip the unroot — engine teardown reclaims everything anyway.
	if (IsEngineExitRequested())
	{
		Tools.Empty();
		return;
	}

	// Hot-reload / module-only unload path — UObject array is still live.
	// IsValidLowLevelFast(false) avoids the GUObjectArray index lookup that
	// IsRooted() does and is safe against partially-collected objects.
	for (auto& Pair : Tools)
	{
		UBionicsBridgeToolBase* Tool = Pair.Value;
		if (Tool && Tool->IsValidLowLevelFast(/*bRecursive=*/false) && Tool->IsRooted())
		{
			Tool->RemoveFromRoot();
		}
	}
	Tools.Empty();
}
