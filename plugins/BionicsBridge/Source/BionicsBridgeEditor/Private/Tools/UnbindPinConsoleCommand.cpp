// Copyright Jacob Ribbe. Licensed under MIT.
//
// One-shot console command to fire UAnimGraphNode_Base::RemoveBindings without
// needing the bridge dispatcher's tool catalog to be re-registered. Live Coding
// patches the FAutoConsoleCommand static initializer cleanly.
//
// Usage:
//   sw.bridge.unbind_pin /Game/Path/ABP.ABP AnimGraphNode_LayeredBoneBlend_0 BlendWeights_0

#include "CoreMinimal.h"
#include "Animation/AnimBlueprint.h"
#include "AnimGraphNode_Base.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "AnimationGraphSchema.h"
#include "EditorAssetLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "ScopedTransaction.h"
#include "HAL/IConsoleManager.h"

namespace SworderBridgeUnbindHelpers
{
	static UAnimBlueprint* LoadAnimBP_MultiStrategy(const FString& AssetPath)
	{
		if (AssetPath.IsEmpty()) return nullptr;
		UObject* Asset = UEditorAssetLibrary::LoadAsset(AssetPath);
		if (!Asset && !AssetPath.Contains(TEXT(".")))
		{
			FString ShortName;
			AssetPath.Split(TEXT("/"), nullptr, &ShortName, ESearchCase::IgnoreCase, ESearchDir::FromEnd);
			if (!ShortName.IsEmpty())
			{
				Asset = UEditorAssetLibrary::LoadAsset(AssetPath + TEXT(".") + ShortName);
			}
		}
		if (!Asset)
		{
			Asset = StaticLoadObject(UAnimBlueprint::StaticClass(), nullptr, *AssetPath);
		}
		if (!Asset && !AssetPath.Contains(TEXT(".")))
		{
			FString ShortName;
			AssetPath.Split(TEXT("/"), nullptr, &ShortName, ESearchCase::IgnoreCase, ESearchDir::FromEnd);
			if (!ShortName.IsEmpty())
			{
				Asset = StaticLoadObject(UAnimBlueprint::StaticClass(), nullptr, *(AssetPath + TEXT(".") + ShortName));
			}
		}
		return Cast<UAnimBlueprint>(Asset);
	}

	static UEdGraph* GetRootAnimGraph_Local(UAnimBlueprint* AnimBP)
	{
		for (UEdGraph* g : AnimBP->FunctionGraphs)
		{
			if (g && g->GetFName() == TEXT("AnimGraph")) return g;
		}
		for (UEdGraph* g : AnimBP->FunctionGraphs)
		{
			if (g && g->GetSchema() && g->GetSchema()->IsA<UAnimationGraphSchema>()) return g;
		}
		return nullptr;
	}
}

static FAutoConsoleCommand GBridgeUnbindPinCmd(
	TEXT("sw.bridge.unbind_pin"),
	TEXT("sw.bridge.unbind_pin <asset_path> <node_name> <pin_name> — remove a property binding from an AnimGraph node pin."),
	FConsoleCommandWithArgsDelegate::CreateStatic([](const TArray<FString>& Args)
	{
		if (Args.Num() < 3)
		{
			UE_LOG(LogTemp, Error, TEXT("[BRIDGE-UNBIND] Usage: sw.bridge.unbind_pin <asset_path> <node_name> <pin_name>"));
			return;
		}
		const FString AssetPath = Args[0];
		const FString NodeName  = Args[1];
		const FString PinName   = Args[2];

		UAnimBlueprint* AnimBP = SworderBridgeUnbindHelpers::LoadAnimBP_MultiStrategy(AssetPath);
		if (!AnimBP)
		{
			UE_LOG(LogTemp, Error, TEXT("[BRIDGE-UNBIND] LoadAnimBP failed for: %s"), *AssetPath);
			return;
		}
		UEdGraph* AnimGraph = SworderBridgeUnbindHelpers::GetRootAnimGraph_Local(AnimBP);
		if (!AnimGraph)
		{
			UE_LOG(LogTemp, Error, TEXT("[BRIDGE-UNBIND] AnimGraph not found in %s"), *AssetPath);
			return;
		}

		UAnimGraphNode_Base* Target = nullptr;
		for (UEdGraphNode* N : AnimGraph->Nodes)
		{
			if (N && N->GetName() == NodeName)
			{
				Target = Cast<UAnimGraphNode_Base>(N);
				break;
			}
		}
		if (!Target)
		{
			UE_LOG(LogTemp, Error, TEXT("[BRIDGE-UNBIND] Node not found: %s"), *NodeName);
			return;
		}

		const FName PinFName(*PinName);
		const bool bHadBinding = Target->HasBinding(PinFName);
		UE_LOG(LogTemp, Display, TEXT("[BRIDGE-UNBIND] node=%s pin=%s had_binding=%d"),
			*NodeName, *PinName, (int32)bHadBinding);

		if (bHadBinding)
		{
			FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "ConsoleUnbindPin", "Bridge: Console Unbind Pin"));
			Target->Modify();
			Target->RemoveBindings(PinFName);
			FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);
			Target->ReconstructNode();

			// Compile + save
			FKismetEditorUtilities::CompileBlueprint(AnimBP, EBlueprintCompileOptions::None);
			UEditorAssetLibrary::SaveLoadedAsset(AnimBP, /*bOnlyIfIsDirty*/ false);

			UE_LOG(LogTemp, Display, TEXT("[BRIDGE-UNBIND] ✓ stripped + compiled + saved"));
		}
		else
		{
			UE_LOG(LogTemp, Display, TEXT("[BRIDGE-UNBIND] no-op (binding already absent)"));
		}
	}),
	ECVF_Default);
