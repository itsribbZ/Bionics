// Copyright Jacob Ribbe. Licensed under MIT.
// EventGraph (K2 / Ubergraph) manipulation tools — programmatic Blueprint EventGraph editing.
// Mirrors AnimGraphTools.cpp's pattern but operates on the K2 schema (Blueprint EventGraph)
// instead of the AnimGraph schema. Combat-polish enabler:
//   • PlayMontage call wiring
//   • GameplayCue trigger nodes
//   • Hitstop SetTimerByFunctionName
//   • CameraShake spawn calls
//   • AnimNotify event handlers
// All operations run on the GAME THREAD via the existing AsyncTask marshaling.
//
// VERIFICATION REQUIRED: This file is shipped without UE5 rebuild + live-fire smoke.
// First-run procedure for verification:
//   1. cd C:/Users/jbro1/Documents/Sworder721/MyProject
//   2. Rebuild.bat MyProjectEditor Win64 Development -Project=...
//   3. Restart UE5
//   4. Verify load: grep "Bionics.*EventGraph" in Saved/Logs/MyProject.log
//   5. Run scripts/smoke_test_eventgraph.ps1 (8-test smoke)
// If any tool errors during smoke, this file's claims are unverified — review carefully.

#include "Tools/QueryEventGraphTool.h"
#include "Tools/AddEventGraphCallFunctionTool.h"
#include "Tools/AddEventGraphVariableNodeTool.h"
#include "Tools/AddEventGraphEventTool.h"
#include "Tools/WireEventGraphPinsTool.h"

#include "Engine/Blueprint.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"

#include "K2Node.h"
#include "K2Node_CallFunction.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "K2Node_Event.h"
#include "K2Node_CustomEvent.h"

#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "EditorAssetLibrary.h"
#include "ScopedTransaction.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"

// ==============================================================
// Shared helpers — EventGraph (K2 / Ubergraph)
// ==============================================================

namespace EventGraphHelpers
{
	/** Load a Blueprint (any K2 BP — Actor BP, Character BP, Widget BP, etc.). */
	static UBlueprint* LoadBlueprint(const FString& AssetPath, FString& OutError)
	{
		if (AssetPath.IsEmpty())
		{
			OutError = TEXT("asset_path is required");
			return nullptr;
		}
		UObject* Asset = UEditorAssetLibrary::LoadAsset(AssetPath);
		UBlueprint* BP = Cast<UBlueprint>(Asset);
		if (!BP)
		{
			OutError = FString::Printf(TEXT("Not a Blueprint: %s"), *AssetPath);
		}
		return BP;
	}

	/** Get the primary EventGraph (Ubergraph) page from a Blueprint.
	 *  UE5 BPs may have multiple Ubergraph pages — primary is the one named "EventGraph"
	 *  or the first one if naming convention drifted. */
	static UEdGraph* GetPrimaryEventGraph(UBlueprint* BP, FString& OutError)
	{
		if (!BP)
		{
			OutError = TEXT("Blueprint is null");
			return nullptr;
		}
		// Primary: find by canonical name
		for (UEdGraph* Graph : BP->UbergraphPages)
		{
			if (Graph && Graph->GetFName() == TEXT("EventGraph"))
			{
				return Graph;
			}
		}
		// Fallback: first ubergraph page (renamed EventGraph or split graph)
		if (BP->UbergraphPages.Num() > 0 && BP->UbergraphPages[0])
		{
			return BP->UbergraphPages[0];
		}
		OutError = TEXT("No EventGraph (UbergraphPages) found in this Blueprint");
		return nullptr;
	}

	/** Find a node by its name (GetName()) in the graph. */
	static UEdGraphNode* FindNodeByName(UEdGraph* Graph, const FString& NodeName)
	{
		if (!Graph) return nullptr;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node && Node->GetName() == NodeName)
			{
				return Node;
			}
		}
		return nullptr;
	}

	/** Find a pin on a node by name (matches name OR display name).
	 *  K2 graphs: exec pins are typically "execute" (input) / "then" (output);
	 *  data pins use the parameter name from the UFunction signature. */
	static UEdGraphPin* FindPinByName(UEdGraphNode* Node, const FString& PinName, EEdGraphPinDirection Direction)
	{
		if (!Node) return nullptr;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && Pin->GetName() == PinName && (Direction == EGPD_MAX || Pin->Direction == Direction))
			{
				return Pin;
			}
		}
		// Fallback: try matching display name (handles localization / tooltip names)
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && Pin->GetDisplayName().ToString() == PinName && (Direction == EGPD_MAX || Pin->Direction == Direction))
			{
				return Pin;
			}
		}
		return nullptr;
	}

	/** Serialize a pin to JSON (parallel to AnimGraphHelpers::PinToJson). */
	static TSharedPtr<FJsonObject> PinToJson(UEdGraphPin* Pin)
	{
		TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
		if (!Pin) return Obj;
		Obj->SetStringField(TEXT("name"), Pin->GetName());
		Obj->SetStringField(TEXT("display_name"), Pin->GetDisplayName().ToString());
		Obj->SetStringField(TEXT("direction"), Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
		Obj->SetStringField(TEXT("type"), Pin->PinType.PinCategory.ToString());
		Obj->SetStringField(TEXT("sub_type"), Pin->PinType.PinSubCategory.ToString());
		Obj->SetBoolField(TEXT("hidden"), Pin->bHidden);
		Obj->SetBoolField(TEXT("is_exec"), Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec);

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
		if (!Node) return Obj;
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

	/** Resolve a UClass by short name across common module paths.
	 *  Common combat-polish targets: GameplayStatics, KismetMathLibrary, KismetSystemLibrary,
	 *  AbilitySystemComponent (GAS), CameraShakeBase, GameplayCueManager. */
	static UClass* ResolveTargetClass(const FString& ClassName)
	{
		if (ClassName.IsEmpty())
		{
			return nullptr; // empty == self / member function
		}
		// Strip leading "U" if user passed "UGameplayStatics"
		FString Name = ClassName;
		if (Name.StartsWith(TEXT("U")) && Name.Len() > 1 && FChar::IsUpper(Name[1]))
		{
			Name = Name.Mid(1);
		}

		// Try common engine modules first (most combat-polish needs)
		static const TCHAR* ModulePaths[] = {
			TEXT("Engine"),
			TEXT("CoreUObject"),
			TEXT("GameplayAbilities"),       // GAS
			TEXT("GameplayTags"),
			TEXT("UMG"),
			TEXT("Niagara"),
		};
		for (const TCHAR* Module : ModulePaths)
		{
			UClass* Found = FindObject<UClass>(nullptr, *FString::Printf(TEXT("/Script/%s.%s"), Module, *Name));
			if (Found) return Found;
		}
		// StaticLoadClass last-resort
		FString ClassPath = FString::Printf(TEXT("/Script/Engine.%s"), *Name);
		return StaticLoadClass(UObject::StaticClass(), nullptr, *ClassPath);
	}

	/** Compile a Blueprint. Returns error count from results log. */
	static int32 CompileBP(UBlueprint* BP)
	{
		FBlueprintEditorUtils::MarkBlueprintAsModified(BP);
		FCompilerResultsLog Results;
		FKismetEditorUtilities::CompileBlueprint(BP, EBlueprintCompileOptions::None, &Results);
		return Results.NumErrors;
	}
}

// ==============================================================
// 1. QueryEventGraphTool
// ==============================================================

TSharedPtr<FJsonObject> UQueryEventGraphTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("include_hidden_pins"), TEXT("boolean")},
	}, { TEXT("asset_path") });
}

bool UQueryEventGraphTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                    TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const bool bIncludeHidden = GetBoolArg(Args, TEXT("include_hidden_pins"), false);

	UBlueprint* BP = EventGraphHelpers::LoadBlueprint(AssetPath, OutError);
	if (!BP) return false;

	UEdGraph* EventGraph = EventGraphHelpers::GetPrimaryEventGraph(BP, OutError);
	if (!EventGraph) return false;

	TArray<TSharedPtr<FJsonValue>> NodesJson;
	for (UEdGraphNode* Node : EventGraph->Nodes)
	{
		if (!Node) continue;
		TSharedPtr<FJsonObject> NodeObj = MakeShared<FJsonObject>();
		NodeObj->SetStringField(TEXT("name"), Node->GetName());
		NodeObj->SetStringField(TEXT("class"), Node->GetClass()->GetName());
		NodeObj->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString());
		NodeObj->SetNumberField(TEXT("pos_x"), Node->NodePosX);
		NodeObj->SetNumberField(TEXT("pos_y"), Node->NodePosY);

		// Tag the kind for callers
		if (Cast<UK2Node_Event>(Node))            NodeObj->SetStringField(TEXT("kind"), TEXT("event"));
		else if (Cast<UK2Node_CustomEvent>(Node)) NodeObj->SetStringField(TEXT("kind"), TEXT("custom_event"));
		else if (Cast<UK2Node_CallFunction>(Node)) NodeObj->SetStringField(TEXT("kind"), TEXT("call_function"));
		else if (Cast<UK2Node_VariableGet>(Node)) NodeObj->SetStringField(TEXT("kind"), TEXT("var_get"));
		else if (Cast<UK2Node_VariableSet>(Node)) NodeObj->SetStringField(TEXT("kind"), TEXT("var_set"));
		else                                       NodeObj->SetStringField(TEXT("kind"), TEXT("other"));

		// Function name for call function nodes
		if (UK2Node_CallFunction* CF = Cast<UK2Node_CallFunction>(Node))
		{
			NodeObj->SetStringField(TEXT("function_name"), CF->GetFunctionName().ToString());
		}

		TArray<TSharedPtr<FJsonValue>> Inputs, Outputs;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (!Pin) continue;
			if (Pin->bHidden && !bIncludeHidden) continue;
			TSharedPtr<FJsonObject> PinObj = EventGraphHelpers::PinToJson(Pin);
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
	OutResult->SetStringField(TEXT("blueprint_class"), BP->GetClass()->GetName());
	OutResult->SetNumberField(TEXT("ubergraph_pages"), BP->UbergraphPages.Num());
	OutResult->SetNumberField(TEXT("node_count"), NodesJson.Num());
	OutResult->SetArrayField(TEXT("nodes"), NodesJson);
	return true;
}

// ==============================================================
// 2. AddEventGraphCallFunctionTool
// ==============================================================

TSharedPtr<FJsonObject> UAddEventGraphCallFunctionTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("target_class"), TEXT("string")},
		{TEXT("function_name"), TEXT("string")},
		{TEXT("pos_x"), TEXT("integer")},
		{TEXT("pos_y"), TEXT("integer")},
	}, { TEXT("asset_path"), TEXT("function_name") });
}

bool UAddEventGraphCallFunctionTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                              TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString TargetClassName = GetStringArg(Args, TEXT("target_class"));
	const FString FunctionName = GetStringArg(Args, TEXT("function_name"));
	const int32 PosX = GetIntArg(Args, TEXT("pos_x"), 0);
	const int32 PosY = GetIntArg(Args, TEXT("pos_y"), 0);

	UBlueprint* BP = EventGraphHelpers::LoadBlueprint(AssetPath, OutError);
	if (!BP) return false;

	UEdGraph* EventGraph = EventGraphHelpers::GetPrimaryEventGraph(BP, OutError);
	if (!EventGraph) return false;

	// Resolve the target class. Empty target_class == self (member function on this BP).
	UClass* TargetClass = nullptr;
	if (!TargetClassName.IsEmpty())
	{
		TargetClass = EventGraphHelpers::ResolveTargetClass(TargetClassName);
		if (!TargetClass)
		{
			OutError = FString::Printf(TEXT("Target class not found: %s. Common: 'GameplayStatics', "
				"'KismetMathLibrary', 'KismetSystemLibrary', 'AbilitySystemComponent'. "
				"Leave empty for self."), *TargetClassName);
			return false;
		}
	}
	else
	{
		// Self lookup: use the BP's parent class
		TargetClass = BP->ParentClass;
		if (!TargetClass)
		{
			OutError = TEXT("Blueprint has no ParentClass and no target_class supplied");
			return false;
		}
	}

	// Find the UFunction
	UFunction* Function = TargetClass->FindFunctionByName(FName(*FunctionName));
	if (!Function)
	{
		OutError = FString::Printf(TEXT("Function not found: %s::%s"),
			*TargetClass->GetName(), *FunctionName);
		return false;
	}

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "AddEventGraphCallFunc",
		"Bionics: Add CallFunction Node"));
	EventGraph->Modify();

	// Create the node
	UK2Node_CallFunction* NewNode = NewObject<UK2Node_CallFunction>(
		EventGraph, UK2Node_CallFunction::StaticClass(), NAME_None, RF_Transactional);
	NewNode->CreateNewGuid();
	NewNode->SetFromFunction(Function);
	NewNode->NodePosX = PosX;
	NewNode->NodePosY = PosY;

	EventGraph->AddNode(NewNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
	NewNode->PostPlacedNewNode();
	NewNode->AllocateDefaultPins();

	FBlueprintEditorUtils::MarkBlueprintAsModified(BP);

	OutResult = EventGraphHelpers::NodeToJson(NewNode);
	OutResult->SetBoolField(TEXT("created"), true);
	OutResult->SetStringField(TEXT("function_resolved"),
		FString::Printf(TEXT("%s::%s"), *TargetClass->GetName(), *FunctionName));
	return true;
}

// ==============================================================
// 3. AddEventGraphVariableNodeTool
// ==============================================================

TSharedPtr<FJsonObject> UAddEventGraphVariableNodeTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("variable_name"), TEXT("string")},
		{TEXT("operation"), TEXT("string")},  // "get" or "set"
		{TEXT("pos_x"), TEXT("integer")},
		{TEXT("pos_y"), TEXT("integer")},
	}, { TEXT("asset_path"), TEXT("variable_name"), TEXT("operation") });
}

bool UAddEventGraphVariableNodeTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                              TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString VariableName = GetStringArg(Args, TEXT("variable_name"));
	const FString Operation = GetStringArg(Args, TEXT("operation")).ToLower();
	const int32 PosX = GetIntArg(Args, TEXT("pos_x"), 0);
	const int32 PosY = GetIntArg(Args, TEXT("pos_y"), 0);

	if (Operation != TEXT("get") && Operation != TEXT("set"))
	{
		OutError = TEXT("operation must be 'get' or 'set'");
		return false;
	}

	UBlueprint* BP = EventGraphHelpers::LoadBlueprint(AssetPath, OutError);
	if (!BP) return false;

	UEdGraph* EventGraph = EventGraphHelpers::GetPrimaryEventGraph(BP, OutError);
	if (!EventGraph) return false;

	// Validate the variable exists on the BP class
	const FName VarFName(*VariableName);
	FProperty* Prop = nullptr;
	if (UClass* SkelClass = BP->SkeletonGeneratedClass)
	{
		Prop = SkelClass->FindPropertyByName(VarFName);
	}
	if (!Prop)
	{
		OutError = FString::Printf(TEXT("Variable '%s' not found on Blueprint %s"),
			*VariableName, *BP->GetName());
		return false;
	}

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "AddEventGraphVarNode",
		"Bionics: Add Variable Node"));
	EventGraph->Modify();

	UK2Node* NewNode = nullptr;
	if (Operation == TEXT("get"))
	{
		UK2Node_VariableGet* GetNode = NewObject<UK2Node_VariableGet>(
			EventGraph, UK2Node_VariableGet::StaticClass(), NAME_None, RF_Transactional);
		GetNode->VariableReference.SetSelfMember(VarFName);
		NewNode = GetNode;
	}
	else // set
	{
		UK2Node_VariableSet* SetNode = NewObject<UK2Node_VariableSet>(
			EventGraph, UK2Node_VariableSet::StaticClass(), NAME_None, RF_Transactional);
		SetNode->VariableReference.SetSelfMember(VarFName);
		NewNode = SetNode;
	}

	NewNode->CreateNewGuid();
	NewNode->NodePosX = PosX;
	NewNode->NodePosY = PosY;

	EventGraph->AddNode(NewNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
	NewNode->PostPlacedNewNode();
	NewNode->AllocateDefaultPins();

	FBlueprintEditorUtils::MarkBlueprintAsModified(BP);

	OutResult = EventGraphHelpers::NodeToJson(NewNode);
	OutResult->SetBoolField(TEXT("created"), true);
	OutResult->SetStringField(TEXT("variable_name"), VariableName);
	OutResult->SetStringField(TEXT("operation"), Operation);
	return true;
}

// ==============================================================
// 4. AddEventGraphEventTool
// ==============================================================

TSharedPtr<FJsonObject> UAddEventGraphEventTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("asset_path"), TEXT("string")},
		{TEXT("event_type"), TEXT("string")},   // "engine" or "custom"
		{TEXT("event_name"), TEXT("string")},
		{TEXT("pos_x"), TEXT("integer")},
		{TEXT("pos_y"), TEXT("integer")},
	}, { TEXT("asset_path"), TEXT("event_type"), TEXT("event_name") });
}

bool UAddEventGraphEventTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                       TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString EventType = GetStringArg(Args, TEXT("event_type")).ToLower();
	const FString EventName = GetStringArg(Args, TEXT("event_name"));
	const int32 PosX = GetIntArg(Args, TEXT("pos_x"), 0);
	const int32 PosY = GetIntArg(Args, TEXT("pos_y"), 0);

	if (EventType != TEXT("engine") && EventType != TEXT("custom"))
	{
		OutError = TEXT("event_type must be 'engine' or 'custom'");
		return false;
	}

	UBlueprint* BP = EventGraphHelpers::LoadBlueprint(AssetPath, OutError);
	if (!BP) return false;

	UEdGraph* EventGraph = EventGraphHelpers::GetPrimaryEventGraph(BP, OutError);
	if (!EventGraph) return false;

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "AddEventGraphEvent",
		"Bionics: Add Event Node"));
	EventGraph->Modify();

	UK2Node* NewNode = nullptr;

	if (EventType == TEXT("engine"))
	{
		// Find the UFunction on the parent class (e.g. AActor::ReceiveBeginPlay)
		UClass* ParentClass = BP->ParentClass;
		if (!ParentClass)
		{
			OutError = TEXT("Blueprint has no ParentClass — engine events require a parent");
			return false;
		}
		UFunction* EventFn = ParentClass->FindFunctionByName(FName(*EventName));
		if (!EventFn)
		{
			OutError = FString::Printf(TEXT("Engine event not found on %s: %s. Common: "
				"'ReceiveBeginPlay', 'ReceiveEndPlay', 'ReceiveTick', 'ReceiveActorBeginOverlap', "
				"'ReceiveAnyDamage'."), *ParentClass->GetName(), *EventName);
			return false;
		}

		UK2Node_Event* EventNode = NewObject<UK2Node_Event>(
			EventGraph, UK2Node_Event::StaticClass(), NAME_None, RF_Transactional);
		EventNode->EventReference.SetExternalMember(FName(*EventName), ParentClass);
		EventNode->bOverrideFunction = true;
		NewNode = EventNode;
	}
	else // custom
	{
		UK2Node_CustomEvent* CustomEvent = NewObject<UK2Node_CustomEvent>(
			EventGraph, UK2Node_CustomEvent::StaticClass(), NAME_None, RF_Transactional);
		CustomEvent->CustomFunctionName = FName(*EventName);
		NewNode = CustomEvent;
	}

	NewNode->CreateNewGuid();
	NewNode->NodePosX = PosX;
	NewNode->NodePosY = PosY;

	EventGraph->AddNode(NewNode, /*bFromUI=*/false, /*bSelectNewNode=*/false);
	NewNode->PostPlacedNewNode();
	NewNode->AllocateDefaultPins();

	FBlueprintEditorUtils::MarkBlueprintAsModified(BP);

	OutResult = EventGraphHelpers::NodeToJson(NewNode);
	OutResult->SetBoolField(TEXT("created"), true);
	OutResult->SetStringField(TEXT("event_type"), EventType);
	OutResult->SetStringField(TEXT("event_name"), EventName);
	return true;
}

// ==============================================================
// 5. WireEventGraphPinsTool
// ==============================================================

TSharedPtr<FJsonObject> UWireEventGraphPinsTool::GetInputSchema() const
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

bool UWireEventGraphPinsTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                       TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString AssetPath = GetStringArg(Args, TEXT("asset_path"));
	const FString SourceNodeName = GetStringArg(Args, TEXT("source_node"));
	const FString SourcePinName = GetStringArg(Args, TEXT("source_pin"));
	const FString TargetNodeName = GetStringArg(Args, TEXT("target_node"));
	const FString TargetPinName = GetStringArg(Args, TEXT("target_pin"));
	const bool bAutoCompile = GetBoolArg(Args, TEXT("auto_compile"), true);

	UBlueprint* BP = EventGraphHelpers::LoadBlueprint(AssetPath, OutError);
	if (!BP) return false;

	UEdGraph* EventGraph = EventGraphHelpers::GetPrimaryEventGraph(BP, OutError);
	if (!EventGraph) return false;

	UEdGraphNode* SourceNode = EventGraphHelpers::FindNodeByName(EventGraph, SourceNodeName);
	if (!SourceNode) { OutError = FString::Printf(TEXT("Source node not found: %s"), *SourceNodeName); return false; }

	UEdGraphNode* TargetNode = EventGraphHelpers::FindNodeByName(EventGraph, TargetNodeName);
	if (!TargetNode) { OutError = FString::Printf(TEXT("Target node not found: %s"), *TargetNodeName); return false; }

	UEdGraphPin* SourcePin = EventGraphHelpers::FindPinByName(SourceNode, SourcePinName, EGPD_Output);
	if (!SourcePin) { OutError = FString::Printf(TEXT("Source pin not found: %s on %s"), *SourcePinName, *SourceNodeName); return false; }

	UEdGraphPin* TargetPin = EventGraphHelpers::FindPinByName(TargetNode, TargetPinName, EGPD_Input);
	if (!TargetPin) { OutError = FString::Printf(TEXT("Target pin not found: %s on %s"), *TargetPinName, *TargetNodeName); return false; }

	const UEdGraphSchema* Schema = EventGraph->GetSchema();
	if (!Schema) { OutError = TEXT("EventGraph has no schema"); return false; }

	FScopedTransaction Transaction(NSLOCTEXT("BionicsBridge", "WireEventGraphPins",
		"Bionics: Wire EventGraph Pins"));
	EventGraph->Modify();

	const FPinConnectionResponse Response = Schema->CanCreateConnection(SourcePin, TargetPin);
	if (Response.Response == CONNECT_RESPONSE_DISALLOW)
	{
		OutError = FString::Printf(TEXT("Cannot connect: %s"), *Response.Message.ToString());
		return false;
	}

	const bool bConnected = Schema->TryCreateConnection(SourcePin, TargetPin);
	if (!bConnected)
	{
		OutError = TEXT("TryCreateConnection returned false (schema rejected the connection)");
		return false;
	}

	FBlueprintEditorUtils::MarkBlueprintAsModified(BP);

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("connected"), true);
	OutResult->SetStringField(TEXT("source"), FString::Printf(TEXT("%s.%s"), *SourceNodeName, *SourcePinName));
	OutResult->SetStringField(TEXT("target"), FString::Printf(TEXT("%s.%s"), *TargetNodeName, *TargetPinName));
	OutResult->SetStringField(TEXT("schema_message"), Response.Message.ToString());

	if (bAutoCompile)
	{
		const int32 ErrorCount = EventGraphHelpers::CompileBP(BP);
		OutResult->SetBoolField(TEXT("compiled"), true);
		OutResult->SetNumberField(TEXT("compile_errors"), ErrorCount);
	}
	return true;
}
