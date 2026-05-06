// Copyright Jacob Ribbe. Licensed under MIT.
// AnimGraph manipulation tools — full programmatic AnimBP control.
// This is the core that makes Bionics the ONLY tool that can automate AnimGraph wiring.

#include "Tools/QueryAnimGraphTool.h"
#include "Tools/CreateAnimGraphNodeTool.h"
#include "Tools/WireAnimGraphPinsTool.h"
#include "Tools/UnwireAnimGraphPinsTool.h"
#include "Tools/DeleteAnimGraphNodeTool.h"
#include "Tools/SetAnimNodePropertyTool.h"
#include "Tools/CreateStateMachineTool.h"
#include "Tools/AddStateTransitionTool.h"

#include "Animation/AnimBlueprint.h"
#include "AnimGraphNode_Base.h"
#include "AnimGraphNode_StateMachine.h"
#include "AnimGraphNode_StateResult.h"
#include "AnimGraphNode_TransitionResult.h"
#include "AnimGraphNode_SequencePlayer.h"
#include "AnimGraphNode_BlendSpacePlayer.h"
#include "AnimGraphNode_Slot.h"
#include "AnimGraphNode_LayeredBoneBlend.h"
#include "AnimGraphNode_BlendListByBool.h"
#include "AnimGraphNode_SaveCachedPose.h"
#include "AnimGraphNode_UseCachedPose.h"
#include "AnimGraphNode_LinkedAnimLayer.h"
#include "AnimGraphNode_TwoWayBlend.h"
#include "AnimGraphNode_Inertialization.h"       // UE5 core anim — blend pop smoothing
#include "AnimGraphNode_MotionMatching.h"        // PoseSearch module — Bible Step 4 locomotion
#include "AnimStateNode.h"
#include "AnimStateTransitionNode.h"
#include "AnimStateEntryNode.h"
#include "AnimationStateMachineGraph.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "AnimationGraphSchema.h"
#include "EditorAssetLibrary.h"
#include "Engine/Blueprint.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "K2Node_VariableGet.h"
#include "ScopedTransaction.h"
#include "Animation/AnimSequence.h"
#include "Animation/BlendSpace.h"

// ==============================================================
// Shared helpers
// ==============================================================

namespace AnimGraphHelpers
{
	/** Load an AnimBlueprint from an asset path. */
	static UAnimBlueprint* LoadAnimBP(const FString& AssetPath, FString& OutError)
	{
		if (AssetPath.IsEmpty())
		{
			OutError = TEXT("asset_path is required");
			return nullptr;
		}
		UObject* Asset = UEditorAssetLibrary::LoadAsset(AssetPath);
		UAnimBlueprint* AnimBP = Cast<UAnimBlueprint>(Asset);
		if (!AnimBP)
		{
			OutError = FString::Printf(TEXT("Not an Animation Blueprint: %s"), *AssetPath);
		}
		return AnimBP;
	}

	/** Get the root AnimGraph from an AnimBlueprint. */
	static UEdGraph* GetRootAnimGraph(UAnimBlueprint* AnimBP, FString& OutError)
	{
		// Primary: find by canonical name
		for (UEdGraph* Graph : AnimBP->FunctionGraphs)
		{
			if (Graph && Graph->GetFName() == TEXT("AnimGraph"))
			{
				return Graph;
			}
		}
		// Fallback: find by schema type (handles renamed or layered AnimBPs)
		for (UEdGraph* Graph : AnimBP->FunctionGraphs)
		{
			if (Graph && Graph->GetSchema() && Graph->GetSchema()->IsA<UAnimationGraphSchema>())
			{
				return Graph;
			}
		}
		OutError = TEXT("AnimGraph not found in this Animation Blueprint");
		return nullptr;
	}

	/** Find a node by its name (GetName()) in the graph. */
	static UEdGraphNode* FindNodeByName(UEdGraph* Graph, const FString& NodeName)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node && Node->GetName() == NodeName)
			{
				return Node;
			}
		}
		return nullptr;
	}

	/** Find a pin on a node by name. */
	static UEdGraphPin* FindPinByName(UEdGraphNode* Node, const FString& PinName, EEdGraphPinDirection Direction)
	{
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && Pin->GetName() == PinName && (Direction == EGPD_MAX || Pin->Direction == Direction))
			{
				return Pin;
			}
		}
		// Fallback: try matching display name
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && Pin->GetDisplayName().ToString() == PinName && (Direction == EGPD_MAX || Pin->Direction == Direction))
			{
				return Pin;
			}
		}
		return nullptr;
	}

	/** Serialize a pin to JSON. */
	static TSharedPtr<FJsonObject> PinToJson(UEdGraphPin* Pin)
	{
		TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("name"), Pin->GetName());
		Obj->SetStringField(TEXT("display_name"), Pin->GetDisplayName().ToString());
		Obj->SetStringField(TEXT("direction"), Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
		Obj->SetStringField(TEXT("type"), Pin->PinType.PinCategory.ToString());
		Obj->SetStringField(TEXT("sub_type"), Pin->PinType.PinSubCategory.ToString());
		Obj->SetBoolField(TEXT("hidden"), Pin->bHidden);

		// Connections
		TArray<TSharedPtr<FJsonValue>> Connections;
		for (UEdGraphPin* Linked : Pin->LinkedTo)
		{
			if (Linked && Linked->GetOwningNode())
			{
				TSharedPtr<FJsonObject> Conn = MakeShared<FJsonObject>();
				Conn->SetStringField(TEXT("node"), Linked->GetOwningNode()->GetName());
				Conn->SetStringField(TEXT("pin"), Linked->GetName());
				Connections.Add(MakeShared<FJsonValueObject>(Conn));
			}
		}
		Obj->SetArrayField(TEXT("connections"), Connections);
		return Obj;
	}

	/** Serialize a node to JSON. */
	static TSharedPtr<FJsonObject> NodeToJson(UEdGraphNode* Node)
	{
		TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("name"), Node->GetName());
		Obj->SetStringField(TEXT("class"), Node->GetClass()->GetName());
		Obj->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString());
		Obj->SetNumberField(TEXT("pos_x"), Node->NodePosX);
		Obj->SetNumberField(TEXT("pos_y"), Node->NodePosY);

		TArray<TSharedPtr<FJsonValue>> Inputs, Outputs;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (!Pin || Pin->bHidden) continue;
			if (Pin->Direction == EGPD_Input)
				Inputs.Add(MakeShared<FJsonValueObject>(PinToJson(Pin)));
			else
				Outputs.Add(MakeShared<FJsonValueObject>(PinToJson(Pin)));
		}
		Obj->SetArrayField(TEXT("input_pins"), Inputs);
		Obj->SetArrayField(TEXT("output_pins"), Outputs);
		return Obj;
	}

	/** Map of short names to UClass pointers for AnimGraph nodes.
	 *  Searches multiple modules because AnimGraph nodes live in several places:
	 *  - /Script/AnimGraph.*                — core built-ins (SequencePlayer, Slot, LBPB, etc.)
	 *  - /Script/PoseSearchEditor.*         — MotionMatching (UE5.4+ Motion Matching)
	 *  - /Script/ControlRigEditor.*         — ControlRig nodes (procedural IK)
	 *  - /Script/AnimationBlueprintEditor.* — LinkedAnimLayer editor variants
	 *  Added 2026-04-16 Phase 2: PoseSearchEditor path unlocks Bible Step 4 MM node creation.
	 */
	static UClass* ResolveNodeClass(const FString& ClassName)
	{
		// Normalize: ensure "AnimGraphNode_" prefix
		FString FullName = ClassName;
		if (!FullName.StartsWith(TEXT("AnimGraphNode_")))
		{
			FullName = TEXT("AnimGraphNode_") + ClassName;
		}

		// Module path candidates in priority order (most common first).
		// MotionMatching lives in /Script/PoseSearch (runtime package, not Editor) —
		// confirmed by Clio 2026-04-16 against actual UE5 5.7 layout. Keep both
		// PoseSearch and PoseSearchEditor as candidates since minor versions differ.
		static const TCHAR* ModulePaths[] = {
			TEXT("AnimGraph"),
			TEXT("PoseSearch"),              // MotionMatching node class (UE5.4+)
			TEXT("PoseSearchEditor"),        // fallback for versions that keep it editor-side
			TEXT("ControlRigEditor"),
			TEXT("AnimationBlueprintEditor"),
			TEXT("Persona"),
		};

		for (const TCHAR* Module : ModulePaths)
		{
			UClass* Found = FindObject<UClass>(nullptr, *FString::Printf(TEXT("/Script/%s.%s"), Module, *FullName));
			if (Found)
			{
				return Found;
			}
		}

		// Last-resort StaticLoadClass fallback on AnimGraph module
		FString ClassPath = FString::Printf(TEXT("/Script/AnimGraph.%s"), *FullName);
		return StaticLoadClass(UObject::StaticClass(), nullptr, *ClassPath);
	}

	/** Compile an AnimBP and return error count. */
	static int32 CompileAnimBP(UAnimBlueprint* AnimBP)
	{
		FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);
		FCompilerResultsLog Results;
		FKismetEditorUtilities::CompileBlueprint(AnimBP, EBlueprintCompileOptions::None, &Results);
		return Results.NumErrors;
	}
}

// ==============================================================
// 1. QueryAnimGraphTool
// ==============================================================

TSharedPtr<FJsonObject> UQueryAnimGraphTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("include_hidden_pins"), TEXT("boolean")},
	}, { TEXT("asset_path") });
}

bool UQueryAnimGraphTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                   TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const bool bIncludeHidden = GetBoolArg(Args, TEXT("include_hidden_pins"), false);

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	TArray<TSharedPtr<FJsonValue>> NodesJson;
	for (UEdGraphNode* Node : AnimGraph->Nodes)
	{
		if (!Node) continue;
		TSharedPtr<FJsonObject> NodeObj = MakeShared<FJsonObject>();
		NodeObj->SetStringField(TEXT("name"), Node->GetName());
		NodeObj->SetStringField(TEXT("class"), Node->GetClass()->GetName());
		NodeObj->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString());
		NodeObj->SetNumberField(TEXT("pos_x"), Node->NodePosX);
		NodeObj->SetNumberField(TEXT("pos_y"), Node->NodePosY);

		// Check if it's a state machine (has sub-graph)
		if (UAnimGraphNode_StateMachine* SM = Cast<UAnimGraphNode_StateMachine>(Node))
		{
			NodeObj->SetBoolField(TEXT("is_state_machine"), true);
			if (SM->EditorStateMachineGraph)
			{
				TArray<TSharedPtr<FJsonValue>> StatesJson;
				for (UEdGraphNode* SubNode : SM->EditorStateMachineGraph->Nodes)
				{
					if (UAnimStateNode* State = Cast<UAnimStateNode>(SubNode))
					{
						TSharedPtr<FJsonObject> StateObj = MakeShared<FJsonObject>();
						StateObj->SetStringField(TEXT("name"), State->GetStateName());
						StateObj->SetStringField(TEXT("node_name"), State->GetName());
						StatesJson.Add(MakeShared<FJsonValueObject>(StateObj));
					}
				}
				NodeObj->SetArrayField(TEXT("states"), StatesJson);
			}
		}

		// Pins
		TArray<TSharedPtr<FJsonValue>> Inputs, Outputs;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (!Pin) continue;
			if (Pin->bHidden && !bIncludeHidden) continue;
			TSharedPtr<FJsonObject> PinObj = AnimGraphHelpers::PinToJson(Pin);
			if (Pin->Direction == EGPD_Input)
				Inputs.Add(MakeShared<FJsonValueObject>(PinObj));
			else
				Outputs.Add(MakeShared<FJsonValueObject>(PinObj));
		}
		NodeObj->SetArrayField(TEXT("input_pins"), Inputs);
		NodeObj->SetArrayField(TEXT("output_pins"), Outputs);
		NodesJson.Add(MakeShared<FJsonValueObject>(NodeObj));
	}

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetStringField(TEXT("asset_path"), AssetPath);
	OutResult->SetStringField(TEXT("skeleton"), AnimBP->TargetSkeleton ? AnimBP->TargetSkeleton->GetPathName() : TEXT("none"));
	OutResult->SetNumberField(TEXT("node_count"), NodesJson.Num());
	OutResult->SetArrayField(TEXT("nodes"), NodesJson);
	return true;
}

// ==============================================================
// 2. CreateAnimGraphNodeTool
// ==============================================================

TSharedPtr<FJsonObject> UCreateAnimGraphNodeTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("node_class"), TEXT("string")},
		{TEXT("pos_x"), TEXT("integer")},
		{TEXT("pos_y"), TEXT("integer")},
	}, { TEXT("asset_path"), TEXT("node_class") });
}

bool UCreateAnimGraphNodeTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                        TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString NodeClassName = GetStringArg(Args, TEXT("node_class"));
	const int32 PosX = GetIntArg(Args, TEXT("pos_x"), 0);
	const int32 PosY = GetIntArg(Args, TEXT("pos_y"), 0);

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	// Resolve the node class
	UClass* NodeClass = AnimGraphHelpers::ResolveNodeClass(NodeClassName);
	if (!NodeClass)
	{
		OutError = FString::Printf(TEXT("Unknown AnimGraph node class: %s. "
			"Use full name like 'AnimGraphNode_SequencePlayer', 'AnimGraphNode_BlendSpacePlayer', "
			"'AnimGraphNode_Slot', 'AnimGraphNode_StateMachine', 'AnimGraphNode_LayeredBoneBlend', "
			"'AnimGraphNode_BlendListByBool', 'AnimGraphNode_TwoWayBlend', "
			"'AnimGraphNode_SaveCachedPose', 'AnimGraphNode_UseCachedPose', "
			"'AnimGraphNode_LinkedAnimLayer', "
			"'AnimGraphNode_MotionMatching' (Bible Step 4 — requires PoseSearch plugin), "
			"'AnimGraphNode_Inertialization' (blend pop smoothing)."), *NodeClassName);
		return false;
	}

	if (!NodeClass->IsChildOf(UAnimGraphNode_Base::StaticClass()))
	{
		OutError = FString::Printf(TEXT("%s is not an AnimGraph node class"), *NodeClassName);
		return false;
	}

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "CreateAnimNode", "Bionics: Create AnimGraph Node"));
	AnimGraph->Modify();

	// Create the node — RF_Transactional MUST be set at construction for undo
	UAnimGraphNode_Base* NewNode = NewObject<UAnimGraphNode_Base>(AnimGraph, NodeClass, NAME_None, RF_Transactional);
	NewNode->CreateNewGuid();
	NewNode->NodePosX = PosX;
	NewNode->NodePosY = PosY;
	// AddNode BEFORE PostPlacedNewNode — PostPlacedNewNode needs the node in the graph
	// (e.g. StateMachine creates its sub-graph via GetGraph())
	AnimGraph->AddNode(NewNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
	NewNode->PostPlacedNewNode();
	NewNode->AllocateDefaultPins();

	// Mark dirty
	FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);

	// Serialize result
	OutResult = AnimGraphHelpers::NodeToJson(NewNode);
	OutResult->SetBoolField(TEXT("created"), true);
	return true;
}

// ==============================================================
// 3. WireAnimGraphPinsTool
// ==============================================================

TSharedPtr<FJsonObject> UWireAnimGraphPinsTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("source_node"), TEXT("string")},
		{TEXT("source_pin"), TEXT("string")},
		{TEXT("target_node"), TEXT("string")},
		{TEXT("target_pin"), TEXT("string")},
		{TEXT("auto_compile"), TEXT("boolean")},
	}, { TEXT("asset_path"), TEXT("source_node"), TEXT("source_pin"), TEXT("target_node"), TEXT("target_pin") });
}

bool UWireAnimGraphPinsTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                      TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString SourceNodeName = GetStringArg(Args, TEXT("source_node"));
	const FString SourcePinName = GetStringArg(Args, TEXT("source_pin"));
	const FString TargetNodeName = GetStringArg(Args, TEXT("target_node"));
	const FString TargetPinName = GetStringArg(Args, TEXT("target_pin"));
	const bool bAutoCompile = GetBoolArg(Args, TEXT("auto_compile"), true);

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	// Find nodes
	UEdGraphNode* SourceNode = AnimGraphHelpers::FindNodeByName(AnimGraph, SourceNodeName);
	if (!SourceNode) { OutError = FString::Printf(TEXT("Source node not found: %s"), *SourceNodeName); return false; }

	UEdGraphNode* TargetNode = AnimGraphHelpers::FindNodeByName(AnimGraph, TargetNodeName);
	if (!TargetNode) { OutError = FString::Printf(TEXT("Target node not found: %s"), *TargetNodeName); return false; }

	// Find pins
	UEdGraphPin* SourcePin = AnimGraphHelpers::FindPinByName(SourceNode, SourcePinName, EGPD_Output);
	if (!SourcePin) { OutError = FString::Printf(TEXT("Source pin not found: %s on %s"), *SourcePinName, *SourceNodeName); return false; }

	UEdGraphPin* TargetPin = AnimGraphHelpers::FindPinByName(TargetNode, TargetPinName, EGPD_Input);
	if (!TargetPin) { OutError = FString::Printf(TEXT("Target pin not found: %s on %s"), *TargetPinName, *TargetNodeName); return false; }

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "WireAnimPins", "Bionics: Wire AnimGraph Pins"));

	// Try schema-validated connection first
	const UEdGraphSchema* Schema = AnimGraph->GetSchema();
	bool bConnected = false;
	if (Schema)
	{
		const FPinConnectionResponse Response = Schema->CanCreateConnection(SourcePin, TargetPin);
		if (Response.Response != CONNECT_RESPONSE_DISALLOW)
		{
			bConnected = Schema->TryCreateConnection(SourcePin, TargetPin);
		}
		else
		{
			OutError = FString::Printf(TEXT("Connection not allowed: %s"), *Response.Message.ToString());
			return false;
		}
	}

	if (!bConnected)
	{
		OutError = TEXT("TryCreateConnection failed after schema allowed it — pin may already be connected or internal schema error");
		return false;
	}

	if (bAutoCompile)
	{
		int32 Errors = AnimGraphHelpers::CompileAnimBP(AnimBP);
		OutResult = MakeShared<FJsonObject>();
		OutResult->SetBoolField(TEXT("connected"), bConnected);
		OutResult->SetNumberField(TEXT("compile_errors"), Errors);
	}
	else
	{
		FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);
		OutResult = MakeShared<FJsonObject>();
		OutResult->SetBoolField(TEXT("connected"), bConnected);
	}

	OutResult->SetStringField(TEXT("source"), FString::Printf(TEXT("%s.%s"), *SourceNodeName, *SourcePinName));
	OutResult->SetStringField(TEXT("target"), FString::Printf(TEXT("%s.%s"), *TargetNodeName, *TargetPinName));
	return true;
}

// ==============================================================
// 4. UnwireAnimGraphPinsTool
// ==============================================================

TSharedPtr<FJsonObject> UUnwireAnimGraphPinsTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("node_name"), TEXT("string")},
		{TEXT("pin_name"), TEXT("string")},
		{TEXT("target_node"), TEXT("string")},
		{TEXT("target_pin"), TEXT("string")},
	}, { TEXT("asset_path"), TEXT("node_name"), TEXT("pin_name") });
}

bool UUnwireAnimGraphPinsTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                        TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString NodeName = GetStringArg(Args, TEXT("node_name"));
	const FString PinName = GetStringArg(Args, TEXT("pin_name"));
	const FString TargetNodeName = GetStringArg(Args, TEXT("target_node"));
	const FString TargetPinName = GetStringArg(Args, TEXT("target_pin"));

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	UEdGraphNode* Node = AnimGraphHelpers::FindNodeByName(AnimGraph, NodeName);
	if (!Node) { OutError = FString::Printf(TEXT("Node not found: %s"), *NodeName); return false; }

	UEdGraphPin* Pin = AnimGraphHelpers::FindPinByName(Node, PinName, EGPD_MAX);
	if (!Pin) { OutError = FString::Printf(TEXT("Pin not found: %s on %s"), *PinName, *NodeName); return false; }

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "UnwireAnimPins", "Bionics: Unwire AnimGraph Pins"));

	int32 BrokenCount = 0;
	if (!TargetNodeName.IsEmpty() && !TargetPinName.IsEmpty())
	{
		// Break specific link
		UEdGraphNode* TargetNode = AnimGraphHelpers::FindNodeByName(AnimGraph, TargetNodeName);
		if (!TargetNode) { OutError = FString::Printf(TEXT("Target node not found: %s"), *TargetNodeName); return false; }
		UEdGraphPin* TargetPin = AnimGraphHelpers::FindPinByName(TargetNode, TargetPinName, EGPD_MAX);
		if (!TargetPin) { OutError = FString::Printf(TEXT("Target pin not found: %s on %s"), *TargetPinName, *TargetNodeName); return false; }

		Pin->BreakLinkTo(TargetPin);
		BrokenCount = 1;
	}
	else
	{
		// Break all links on this pin
		BrokenCount = Pin->LinkedTo.Num();
		Pin->BreakAllPinLinks();
	}

	FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("ok"), true);
	OutResult->SetNumberField(TEXT("broken_count"), BrokenCount);
	return true;
}

// ==============================================================
// 5. DeleteAnimGraphNodeTool
// ==============================================================

TSharedPtr<FJsonObject> UDeleteAnimGraphNodeTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("node_name"), TEXT("string")},
	}, { TEXT("asset_path"), TEXT("node_name") });
}

bool UDeleteAnimGraphNodeTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                        TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString NodeName = GetStringArg(Args, TEXT("node_name"));

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	UEdGraphNode* Node = AnimGraphHelpers::FindNodeByName(AnimGraph, NodeName);
	if (!Node) { OutError = FString::Printf(TEXT("Node not found: %s"), *NodeName); return false; }

	// Prevent deleting structural nodes (Root, StateResult, TransitionResult)
	const FString ClassName = Node->GetClass()->GetName();
	if (ClassName.Contains(TEXT("Root")) || ClassName.Contains(TEXT("StateResult")) || ClassName.Contains(TEXT("TransitionResult")))
	{
		OutError = FString::Printf(TEXT("Cannot delete structural node %s (%s) — required by the AnimGraph"), *NodeName, *ClassName);
		return false;
	}

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "DeleteAnimNode", "Bionics: Delete AnimGraph Node"));

	// Break all connections first
	for (UEdGraphPin* Pin : Node->Pins)
	{
		if (Pin) Pin->BreakAllPinLinks();
	}

	AnimGraph->RemoveNode(Node);
	FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("deleted"), true);
	OutResult->SetStringField(TEXT("node_name"), NodeName);
	return true;
}

// ==============================================================
// 6. SetAnimNodePropertyTool
// ==============================================================

TSharedPtr<FJsonObject> USetAnimNodePropertyTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("node_name"), TEXT("string")},
		{TEXT("property_name"), TEXT("string")},
		{TEXT("property_value"), TEXT("string")},
	}, { TEXT("asset_path"), TEXT("node_name"), TEXT("property_name"), TEXT("property_value") });
}

bool USetAnimNodePropertyTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                        TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString NodeName = GetStringArg(Args, TEXT("node_name"));
	const FString PropertyName = GetStringArg(Args, TEXT("property_name"));
	const FString PropertyValue = GetStringArg(Args, TEXT("property_value"));

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	UEdGraphNode* RawNode = AnimGraphHelpers::FindNodeByName(AnimGraph, NodeName);
	if (!RawNode) { OutError = FString::Printf(TEXT("Node not found: %s"), *NodeName); return false; }

	UAnimGraphNode_Base* AnimNode = Cast<UAnimGraphNode_Base>(RawNode);
	if (!AnimNode) { OutError = TEXT("Node is not an AnimGraph node"); return false; }

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "SetAnimProp", "Bionics: Set AnimNode Property"));

	bool bSet = false;

	// Special handling: animation sequence assignment on SequencePlayer
	if (PropertyName.Equals(TEXT("Sequence"), ESearchCase::IgnoreCase) ||
	    PropertyName.Equals(TEXT("AnimSequence"), ESearchCase::IgnoreCase))
	{
		if (UAnimGraphNode_SequencePlayer* SeqPlayer = Cast<UAnimGraphNode_SequencePlayer>(AnimNode))
		{
			UAnimSequence* Seq = LoadObject<UAnimSequence>(nullptr, *PropertyValue);
			if (Seq)
			{
				// Access the inner FAnimNode struct's Sequence property via reflection
				FStructProperty* NodeProp = CastField<FStructProperty>(
					SeqPlayer->GetClass()->FindPropertyByName(TEXT("Node")));
				if (NodeProp)
				{
					void* NodeStruct = NodeProp->ContainerPtrToValuePtr<void>(SeqPlayer);
					FObjectProperty* SeqProp = CastField<FObjectProperty>(
						NodeProp->Struct->FindPropertyByName(TEXT("Sequence")));
					if (SeqProp)
					{
						SeqProp->SetObjectPropertyValue(
							SeqProp->ContainerPtrToValuePtr<void>(NodeStruct), Seq);
						bSet = true;
					}
				}
				if (!bSet)
				{
					// Fallback: try pin default value for newer UE5 versions
					// In UE5.4+ SequencePlayer uses pin-based sequence binding
					for (UEdGraphPin* Pin : SeqPlayer->Pins)
					{
						if (Pin && Pin->GetName().Contains(TEXT("Sequence")))
						{
							Pin->DefaultObject = Seq;
							bSet = true;
							break;
						}
					}
				}
			}
			else
			{
				OutError = FString::Printf(TEXT("AnimSequence not found: %s"), *PropertyValue);
				return false;
			}
		}
	}
	// Special handling: BlendSpace assignment
	else if (PropertyName.Equals(TEXT("BlendSpace"), ESearchCase::IgnoreCase))
	{
		if (UAnimGraphNode_BlendSpacePlayer* BSPlayer = Cast<UAnimGraphNode_BlendSpacePlayer>(AnimNode))
		{
			UBlendSpace* BS = LoadObject<UBlendSpace>(nullptr, *PropertyValue);
			if (BS)
			{
				for (UEdGraphPin* Pin : BSPlayer->Pins)
				{
					if (Pin && Pin->GetName().Contains(TEXT("BlendSpace")))
					{
						Pin->DefaultObject = BS;
						bSet = true;
						break;
					}
				}
			}
			else
			{
				OutError = FString::Printf(TEXT("BlendSpace not found: %s"), *PropertyValue);
				return false;
			}
		}
	}
	// Special handling: Slot name
	else if (PropertyName.Equals(TEXT("SlotName"), ESearchCase::IgnoreCase))
	{
		if (UAnimGraphNode_Slot* SlotNode = Cast<UAnimGraphNode_Slot>(AnimNode))
		{
			FStructProperty* NodeProp = CastField<FStructProperty>(
				SlotNode->GetClass()->FindPropertyByName(TEXT("Node")));
			if (NodeProp)
			{
				void* NodeStruct = NodeProp->ContainerPtrToValuePtr<void>(SlotNode);
				FNameProperty* NameProp = CastField<FNameProperty>(
					NodeProp->Struct->FindPropertyByName(TEXT("SlotName")));
				if (NameProp)
				{
					NameProp->SetPropertyValue(
						NameProp->ContainerPtrToValuePtr<void>(NodeStruct), FName(*PropertyValue));
					bSet = true;
				}
			}
		}
	}

	// Generic fallback: try UProperty reflection on the node itself
	if (!bSet)
	{
		FProperty* Prop = AnimNode->GetClass()->FindPropertyByName(FName(*PropertyName));
		if (Prop)
		{
			void* ValuePtr = Prop->ContainerPtrToValuePtr<void>(AnimNode);
			if (Prop->ImportText_Direct(*PropertyValue, ValuePtr, AnimNode, PPF_None))
			{
				bSet = true;
			}
		}
	}

	// Also try the inner FAnimNode struct
	if (!bSet)
	{
		FStructProperty* NodeProp = CastField<FStructProperty>(
			AnimNode->GetClass()->FindPropertyByName(TEXT("Node")));
		if (NodeProp)
		{
			void* NodeStruct = NodeProp->ContainerPtrToValuePtr<void>(AnimNode);
			FProperty* InnerProp = NodeProp->Struct->FindPropertyByName(FName(*PropertyName));
			if (InnerProp)
			{
				void* ValuePtr = InnerProp->ContainerPtrToValuePtr<void>(NodeStruct);
				if (InnerProp->ImportText_Direct(*PropertyValue, ValuePtr, AnimNode, PPF_None))
				{
					bSet = true;
				}
			}
		}
	}

	if (!bSet)
	{
		OutError = FString::Printf(TEXT("Could not set property '%s' on node '%s'. "
			"Special properties: Sequence, BlendSpace, SlotName. "
			"Generic properties: use the exact UPROPERTY name."), *PropertyName, *NodeName);
		return false;
	}

	FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);
	AnimNode->ReconstructNode();

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("ok"), true);
	OutResult->SetStringField(TEXT("property"), PropertyName);
	OutResult->SetStringField(TEXT("value"), PropertyValue);
	return true;
}

// ==============================================================
// 7. CreateStateMachineTool
// ==============================================================

TSharedPtr<FJsonObject> UCreateStateMachineTool::GetInputSchema() const
{
	TSharedPtr<FJsonObject> Schema = MakeShared<FJsonObject>();
	Schema->SetStringField(TEXT("type"), TEXT("object"));

	TSharedPtr<FJsonObject> Props = MakeShared<FJsonObject>();

	TSharedPtr<FJsonObject> AssetProp = MakeShared<FJsonObject>();
	AssetProp->SetStringField(TEXT("type"), TEXT("string"));
	Props->SetObjectField(TEXT("asset_path"), AssetProp);

	TSharedPtr<FJsonObject> NameProp = MakeShared<FJsonObject>();
	NameProp->SetStringField(TEXT("type"), TEXT("string"));
	Props->SetObjectField(TEXT("machine_name"), NameProp);

	TSharedPtr<FJsonObject> StatesProp = MakeShared<FJsonObject>();
	StatesProp->SetStringField(TEXT("type"), TEXT("array"));
	TSharedPtr<FJsonObject> ItemsProp = MakeShared<FJsonObject>();
	ItemsProp->SetStringField(TEXT("type"), TEXT("string"));
	StatesProp->SetObjectField(TEXT("items"), ItemsProp);
	Props->SetObjectField(TEXT("state_names"), StatesProp);

	TSharedPtr<FJsonObject> PosXProp = MakeShared<FJsonObject>();
	PosXProp->SetStringField(TEXT("type"), TEXT("integer"));
	Props->SetObjectField(TEXT("pos_x"), PosXProp);

	TSharedPtr<FJsonObject> PosYProp = MakeShared<FJsonObject>();
	PosYProp->SetStringField(TEXT("type"), TEXT("integer"));
	Props->SetObjectField(TEXT("pos_y"), PosYProp);

	Schema->SetObjectField(TEXT("properties"), Props);

	TArray<TSharedPtr<FJsonValue>> Req;
	Req.Add(MakeShared<FJsonValueString>(TEXT("asset_path")));
	Req.Add(MakeShared<FJsonValueString>(TEXT("state_names")));
	Schema->SetArrayField(TEXT("required"), Req);

	return Schema;
}

bool UCreateStateMachineTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                       TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString MachineName = GetStringArg(Args, TEXT("machine_name"), TEXT("Locomotion"));
	const int32 PosX = GetIntArg(Args, TEXT("pos_x"), -200);
	const int32 PosY = GetIntArg(Args, TEXT("pos_y"), 0);

	// Parse state names from JSON array
	TArray<FString> StateNames;
	if (Args->HasTypedField<EJson::Array>(TEXT("state_names")))
	{
		for (const TSharedPtr<FJsonValue>& Val : Args->GetArrayField(TEXT("state_names")))
		{
			if (Val.IsValid()) StateNames.Add(Val->AsString());
		}
	}
	if (StateNames.Num() == 0)
	{
		OutError = TEXT("state_names array is required and must have at least one state");
		return false;
	}

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "CreateSM", "Bionics: Create State Machine"));

	// Create the state machine node — RF_Transactional at construction, AddNode BEFORE PostPlacedNewNode
	AnimGraph->Modify();
	UAnimGraphNode_StateMachine* SMNode = NewObject<UAnimGraphNode_StateMachine>(AnimGraph, UAnimGraphNode_StateMachine::StaticClass(), NAME_None, RF_Transactional);
	SMNode->CreateNewGuid();
	SMNode->NodePosX = PosX;
	SMNode->NodePosY = PosY;
	AnimGraph->AddNode(SMNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
	SMNode->PostPlacedNewNode();  // creates EditorStateMachineGraph + entry node
	SMNode->AllocateDefaultPins();

	// Get the state machine sub-graph
	UAnimationStateMachineGraph* SMGraph = SMNode->EditorStateMachineGraph;
	if (!SMGraph)
	{
		OutError = TEXT("Failed to create state machine sub-graph");
		return false;
	}

	// Create states
	TArray<TSharedPtr<FJsonValue>> StatesJson;
	int32 StateX = 200;
	for (const FString& StateName : StateNames)
	{
		UAnimStateNode* StateNode = NewObject<UAnimStateNode>(SMGraph, UAnimStateNode::StaticClass(), NAME_None, RF_Transactional);
		StateNode->CreateNewGuid();
		StateNode->NodePosX = StateX;
		StateNode->NodePosY = 0;
		SMGraph->AddNode(StateNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
		StateNode->PostPlacedNewNode();  // creates BoundGraph (UAnimationStateGraph)
		StateNode->AllocateDefaultPins();

		// Rename the state via its BoundGraph (GetStateName() returns BoundGraph->GetName())
		if (StateNode->BoundGraph)
		{
			TSharedPtr<INameValidatorInterface> NameValidator;
			FBlueprintEditorUtils::RenameGraphWithSuggestion(StateNode->BoundGraph, NameValidator, StateName);
		}

		StateX += 300;

		TSharedPtr<FJsonObject> StateObj = MakeShared<FJsonObject>();
		StateObj->SetStringField(TEXT("name"), StateName);
		StateObj->SetStringField(TEXT("node_name"), StateNode->GetName());
		StatesJson.Add(MakeShared<FJsonValueObject>(StateObj));
	}

	// Wire entry node to first state
	UAnimStateEntryNode* EntryNode = nullptr;
	for (UEdGraphNode* Node : SMGraph->Nodes)
	{
		EntryNode = Cast<UAnimStateEntryNode>(Node);
		if (EntryNode) break;
	}
	if (EntryNode && StatesJson.Num() > 0)
	{
		UAnimStateNode* FirstState = nullptr;
		for (UEdGraphNode* Node : SMGraph->Nodes)
		{
			UAnimStateNode* State = Cast<UAnimStateNode>(Node);
			if (State)
			{
				FirstState = State;
				break;
			}
		}
		if (FirstState)
		{
			// Wire entry → first state
			UEdGraphPin* EntryOut = (EntryNode->Pins.Num() > 0) ? EntryNode->Pins[0] : nullptr;
			UEdGraphPin* StateIn = nullptr;
			for (UEdGraphPin* Pin : FirstState->Pins)
			{
				if (Pin && Pin->Direction == EGPD_Input)
				{
					StateIn = Pin;
					break;
				}
			}
			if (EntryOut && StateIn)
			{
				EntryOut->MakeLinkTo(StateIn);
			}
		}
	}

	FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("created"), true);
	OutResult->SetStringField(TEXT("machine_name"), MachineName);
	OutResult->SetStringField(TEXT("sm_node"), SMNode->GetName());
	OutResult->SetArrayField(TEXT("states"), StatesJson);
	OutResult->SetNumberField(TEXT("state_count"), StateNames.Num());
	return true;
}

// ==============================================================
// 8. AddStateTransitionTool
// ==============================================================

TSharedPtr<FJsonObject> UAddStateTransitionTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("state_machine_node"), TEXT("string")},
		{TEXT("source_state"), TEXT("string")},
		{TEXT("target_state"), TEXT("string")},
		{TEXT("condition_variable"), TEXT("string")},
	}, { TEXT("asset_path"), TEXT("state_machine_node"), TEXT("source_state"), TEXT("target_state") });
}

bool UAddStateTransitionTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                       TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString SMNodeName = GetStringArg(Args, TEXT("state_machine_node"));
	const FString SourceStateName = GetStringArg(Args, TEXT("source_state"));
	const FString TargetStateName = GetStringArg(Args, TEXT("target_state"));
	const FString ConditionVar = GetStringArg(Args, TEXT("condition_variable"));

	UAnimBlueprint* AnimBP = AnimGraphHelpers::LoadAnimBP(AssetPath, OutError);
	if (!AnimBP) return false;

	UEdGraph* AnimGraph = AnimGraphHelpers::GetRootAnimGraph(AnimBP, OutError);
	if (!AnimGraph) return false;

	// Find the state machine node
	UEdGraphNode* RawSMNode = AnimGraphHelpers::FindNodeByName(AnimGraph, SMNodeName);
	UAnimGraphNode_StateMachine* SMNode = Cast<UAnimGraphNode_StateMachine>(RawSMNode);
	if (!SMNode) { OutError = FString::Printf(TEXT("State machine node not found: %s"), *SMNodeName); return false; }

	UAnimationStateMachineGraph* SMGraph = SMNode->EditorStateMachineGraph;
	if (!SMGraph) { OutError = TEXT("State machine has no sub-graph"); return false; }

	// Find source and target states
	UAnimStateNode* SourceState = nullptr;
	UAnimStateNode* TargetState = nullptr;
	for (UEdGraphNode* Node : SMGraph->Nodes)
	{
		UAnimStateNode* State = Cast<UAnimStateNode>(Node);
		if (!State) continue;
		if (State->GetStateName() == SourceStateName) SourceState = State;
		if (State->GetStateName() == TargetStateName) TargetState = State;
	}
	if (!SourceState) { OutError = FString::Printf(TEXT("Source state not found: %s"), *SourceStateName); return false; }
	if (!TargetState) { OutError = FString::Printf(TEXT("Target state not found: %s"), *TargetStateName); return false; }

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "AddTransition", "Bionics: Add State Transition"));

	// Create transition node — RF_Transactional at construction, AddNode BEFORE PostPlacedNewNode
	UAnimStateTransitionNode* TransNode = NewObject<UAnimStateTransitionNode>(SMGraph, UAnimStateTransitionNode::StaticClass(), NAME_None, RF_Transactional);
	TransNode->CreateNewGuid();
	TransNode->NodePosX = (SourceState->NodePosX + TargetState->NodePosX) / 2;
	TransNode->NodePosY = (SourceState->NodePosY + TargetState->NodePosY) / 2 - 50;
	SMGraph->AddNode(TransNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
	TransNode->PostPlacedNewNode();
	TransNode->AllocateDefaultPins();

	// Wire: SourceState output → Transition input, Transition output → TargetState input
	UEdGraphPin* SourceOut = nullptr;
	for (UEdGraphPin* Pin : SourceState->Pins)
	{
		if (Pin && Pin->Direction == EGPD_Output) { SourceOut = Pin; break; }
	}
	UEdGraphPin* TransIn = nullptr;
	UEdGraphPin* TransOut = nullptr;
	for (UEdGraphPin* Pin : TransNode->Pins)
	{
		if (Pin && Pin->Direction == EGPD_Input) TransIn = Pin;
		if (Pin && Pin->Direction == EGPD_Output) TransOut = Pin;
	}
	UEdGraphPin* TargetIn = nullptr;
	for (UEdGraphPin* Pin : TargetState->Pins)
	{
		if (Pin && Pin->Direction == EGPD_Input) { TargetIn = Pin; break; }
	}

	if (SourceOut && TransIn) SourceOut->MakeLinkTo(TransIn);
	if (TransOut && TargetIn) TransOut->MakeLinkTo(TargetIn);

	FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("created"), true);
	OutResult->SetStringField(TEXT("transition_node"), TransNode->GetName());
	OutResult->SetStringField(TEXT("from"), SourceStateName);
	OutResult->SetStringField(TEXT("to"), TargetStateName);

	// Wire the condition_variable into the transition's BoundGraph rule.
	// The BoundGraph holds a UAnimGraphNode_TransitionResult with one bool input
	// pin ("bCanEnterTransition"). Wiring a K2Node_VariableGet for the named bool
	// var into that pin replaces the default always-true transition with a
	// data-driven rule.
	if (!ConditionVar.IsEmpty())
	{
		OutResult->SetStringField(TEXT("condition_variable"), ConditionVar);

		UEdGraph* RuleGraph = TransNode->BoundGraph;
		if (!RuleGraph)
		{
			OutResult->SetStringField(TEXT("condition_warning"), TEXT("transition has no BoundGraph — condition not wired"));
			return true;
		}

		// Find the result node (auto-created when transition was constructed).
		UAnimGraphNode_TransitionResult* ResultNode = nullptr;
		for (UEdGraphNode* Node : RuleGraph->Nodes)
		{
			ResultNode = Cast<UAnimGraphNode_TransitionResult>(Node);
			if (ResultNode) break;
		}
		if (!ResultNode)
		{
			OutResult->SetStringField(TEXT("condition_warning"), TEXT("BoundGraph missing UAnimGraphNode_TransitionResult — condition not wired"));
			return true;
		}

		// Verify the variable exists on the AnimBP class and is a bool.
		const FName VarFName(*ConditionVar);
		UClass* AnimClass = AnimBP->GeneratedClass ? AnimBP->GeneratedClass.Get() : AnimBP->ParentClass.Get();
		FProperty* VarProp = AnimClass ? AnimClass->FindPropertyByName(VarFName) : nullptr;
		if (!VarProp || !CastField<FBoolProperty>(VarProp))
		{
			OutResult->SetStringField(TEXT("condition_warning"),
				FString::Printf(TEXT("variable '%s' not found on %s or not a bool — transition defaults to always-true"),
					*ConditionVar, AnimClass ? *AnimClass->GetName() : TEXT("<null class>")));
			return true;
		}

		// Create K2Node_VariableGet inside the rule graph (mirrors EventGraphTools.cpp pattern).
		UK2Node_VariableGet* GetNode = NewObject<UK2Node_VariableGet>(
			RuleGraph, UK2Node_VariableGet::StaticClass(), NAME_None, RF_Transactional);
		GetNode->VariableReference.SetSelfMember(VarFName);
		GetNode->CreateNewGuid();
		GetNode->NodePosX = ResultNode->NodePosX - 220;
		GetNode->NodePosY = ResultNode->NodePosY;
		RuleGraph->AddNode(GetNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
		GetNode->PostPlacedNewNode();
		GetNode->AllocateDefaultPins();

		// Wire: GetNode bool output → ResultNode "bCanEnterTransition" input.
		UEdGraphPin* GetOutPin = GetNode->FindPin(VarFName);
		UEdGraphPin* ResultInPin = nullptr;
		for (UEdGraphPin* Pin : ResultNode->Pins)
		{
			if (Pin && Pin->Direction == EGPD_Input && Pin->PinType.PinCategory == TEXT("bool"))
			{
				ResultInPin = Pin;
				break;
			}
		}
		if (!GetOutPin || !ResultInPin)
		{
			OutResult->SetStringField(TEXT("condition_warning"),
				FString::Printf(TEXT("could not locate pins (get_out=%s, result_in=%s) — node placed but not wired"),
					GetOutPin ? TEXT("ok") : TEXT("missing"),
					ResultInPin ? TEXT("ok") : TEXT("missing")));
			return true;
		}

		GetOutPin->MakeLinkTo(ResultInPin);
		FBlueprintEditorUtils::MarkBlueprintAsModified(AnimBP);

		OutResult->SetBoolField(TEXT("condition_wired"), true);
		OutResult->SetStringField(TEXT("condition_var_node"), GetNode->GetName());
	}
	return true;
}
