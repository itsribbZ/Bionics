// Copyright Jacob Ribbe. Licensed under MIT.
// All editor tool implementations in one file for compile efficiency.

#include "Tools/CompileBlueprintTool.h"
#include "Tools/SaveAssetTool.h"
#include "Tools/QueryAssetTool.h"
#include "Tools/SpawnActorEditorTool.h"

#include "Engine/Blueprint.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Kismet2/CompilerResultsLog.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "EditorAssetLibrary.h"
#include "Subsystems/EditorActorSubsystem.h"
#include "Editor.h"
#include "ScopedTransaction.h"

// ---- CompileBlueprintTool ----

TSharedPtr<FJsonObject> UCompileBlueprintTool::GetInputSchema() const
{
	return MakeSchema({ {TEXT("asset_path"), TEXT("string")} }, { TEXT("asset_path") });
}

bool UCompileBlueprintTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                     TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	if (AssetPath.IsEmpty()) { OutError = TEXT("asset_path required"); return false; }

	UObject* Asset = UEditorAssetLibrary::LoadAsset(AssetPath);
	UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
	if (!Blueprint) { OutError = FString::Printf(TEXT("Not a Blueprint: %s"), *AssetPath); return false; }

	FCompilerResultsLog Results;
	FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None, &Results);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("ok"), Results.NumErrors == 0);
	OutResult->SetNumberField(TEXT("errors"), Results.NumErrors);
	OutResult->SetNumberField(TEXT("warnings"), Results.NumWarnings);
	OutResult->SetStringField(TEXT("asset_path"), AssetPath);
	return true;
}

// ---- SaveAssetTool ----

TSharedPtr<FJsonObject> USaveAssetTool::GetInputSchema() const
{
	return MakeSchema({ {TEXT("asset_path"), TEXT("string")} }, { TEXT("asset_path") });
}

bool USaveAssetTool::Execute(const TSharedPtr<FJsonObject>& Args,
                              TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	if (AssetPath.IsEmpty()) { OutError = TEXT("asset_path required"); return false; }
	if (!UEditorAssetLibrary::DoesAssetExist(AssetPath))
	{
		OutError = FString::Printf(TEXT("Asset not found: %s"), *AssetPath);
		return false;
	}
	bool bOk = UEditorAssetLibrary::SaveAsset(AssetPath, /*bOnlyIfIsDirty=*/true);
	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("ok"), bOk);
	OutResult->SetStringField(TEXT("asset_path"), AssetPath);
	return bOk;
}

// ---- QueryAssetTool ----

TSharedPtr<FJsonObject> UQueryAssetTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("class_name"), TEXT("string")},
		{TEXT("path_prefix"), TEXT("string")},
		{TEXT("limit"), TEXT("integer")},
	}, {});
}

bool UQueryAssetTool::Execute(const TSharedPtr<FJsonObject>& Args,
                               TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString ClassName = GetStringArg(Args, TEXT("class_name"));
	const FString PathPrefix = GetStringArg(Args, TEXT("path_prefix"), TEXT("/Game"));
	const int32 Limit = FMath::Clamp(GetIntArg(Args, TEXT("limit"), 100), 1, 1000);

	FAssetRegistryModule& ARM = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry");
	IAssetRegistry& AR = ARM.Get();

	FARFilter Filter;
	Filter.PackagePaths.Add(FName(*PathPrefix));
	Filter.bRecursivePaths = true;
	if (!ClassName.IsEmpty())
	{
		Filter.ClassPaths.Add(FTopLevelAssetPath(FName(TEXT("/Script/Engine")), FName(*ClassName)));
	}

	TArray<FAssetData> Assets;
	AR.GetAssets(Filter, Assets);

	TArray<TSharedPtr<FJsonValue>> AssetsJson;
	for (const FAssetData& Asset : Assets)
	{
		if (AssetsJson.Num() >= Limit) break;
		TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("name"), Asset.AssetName.ToString());
		Obj->SetStringField(TEXT("class"), Asset.AssetClassPath.ToString());
		Obj->SetStringField(TEXT("path"), Asset.PackageName.ToString());
		AssetsJson.Add(MakeShared<FJsonValueObject>(Obj));
	}
	OutResult = MakeShared<FJsonObject>();
	OutResult->SetArrayField(TEXT("assets"), AssetsJson);
	OutResult->SetNumberField(TEXT("count"), AssetsJson.Num());
	OutResult->SetNumberField(TEXT("total_matched"), Assets.Num());
	return true;
}

// ---- SpawnActorEditorTool ----

TSharedPtr<FJsonObject> USpawnActorEditorTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("actor_class"), TEXT("string")},
		{TEXT("location"), TEXT("array")},
		{TEXT("rotation"), TEXT("array")},
		{TEXT("label"), TEXT("string")},
	}, { TEXT("actor_class") });
}

bool USpawnActorEditorTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                     TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString ClassPath = GetStringArg(Args, TEXT("actor_class"));
	if (ClassPath.IsEmpty()) { OutError = TEXT("actor_class required"); return false; }

	UClass* ActorCls = LoadObject<UClass>(nullptr, *ClassPath);
	if (!ActorCls)
	{
		UObject* BpAsset = UEditorAssetLibrary::LoadAsset(ClassPath);
		if (UBlueprint* Bp = Cast<UBlueprint>(BpAsset)) ActorCls = Bp->GeneratedClass;
	}
	if (!ActorCls) { OutError = FString::Printf(TEXT("Class not found: %s"), *ClassPath); return false; }

	const FVector Location = GetVectorArg(Args, TEXT("location"));
	const FVector Rot = GetVectorArg(Args, TEXT("rotation"));
	const FRotator Rotation(Rot.X, Rot.Y, Rot.Z);

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "SpawnActor", "Spawn Actor"));
	// Use UEditorActorSubsystem (UEditorLevelLibrary is deprecated in UE5.3+)
	UEditorActorSubsystem* ActorSub = GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
	if (!ActorSub) { OutError = TEXT("EditorActorSubsystem unavailable"); return false; }
	AActor* Spawned = ActorSub->SpawnActorFromClass(ActorCls, Location, Rotation);
	if (!Spawned) { OutError = TEXT("Spawn failed"); return false; }

	const FString Label = GetStringArg(Args, TEXT("label"));
	if (!Label.IsEmpty()) Spawned->SetActorLabel(Label);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetStringField(TEXT("name"), Spawned->GetName());
	OutResult->SetStringField(TEXT("label"), Spawned->GetActorLabel());
	OutResult->SetStringField(TEXT("class"), ActorCls->GetName());
	return true;
}
