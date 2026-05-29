// Copyright Jacob Ribbe. Licensed under MIT.

#include "BionicsBridgeEditorModule.h"
#include "BionicsBridgeToolRegistry.h"
#include "BionicsBridgeModule.h"
#include "Modules/ModuleManager.h"

// General editor tools
#include "Tools/CompileBlueprintTool.h"
#include "Tools/SaveAssetTool.h"
#include "Tools/QueryAssetTool.h"
#include "Tools/SpawnActorEditorTool.h"
#include "Tools/LiveCodingCompileTool.h"

// AnimGraph manipulation tools (13) — 8 base + 3 T-BRIDGE-1 wiring + 2 godspeed
// 2026-05-15 (create_animgraph_variable_get, drive_animgraph_pin_via_variable) —
// closes manual-editor handoff for FBoneReference structs, UPROPERTY pin bindings,
// pose-flow splicing, AnimGraph K2Node_VariableGet creation (LIMIT 2), and the
// runtime-correct alternative to metadata-only bind_pin_to_property (LIMIT 1).
#include "Tools/QueryAnimGraphTool.h"
#include "Tools/CreateAnimGraphNodeTool.h"
#include "Tools/WireAnimGraphPinsTool.h"
#include "Tools/UnwireAnimGraphPinsTool.h"
#include "Tools/DeleteAnimGraphNodeTool.h"
#include "Tools/SetAnimNodePropertyTool.h"
#include "Tools/CreateStateMachineTool.h"
#include "Tools/AddStateTransitionTool.h"
#include "Tools/SetBoneReferenceTool.h"
#include "Tools/BindPinToPropertyTool.h"
#include "Tools/UnbindPinFromPropertyTool.h"
#include "Tools/SplicePoseFlowTool.h"
#include "Tools/CreateAnimGraphVariableGetTool.h"
#include "Tools/DriveAnimGraphPinViaVariableTool.h"

// EventGraph (K2 / Ubergraph) manipulation tools (5) — combat polish enabler
#include "Tools/QueryEventGraphTool.h"
#include "Tools/AddEventGraphCallFunctionTool.h"
#include "Tools/AddEventGraphVariableNodeTool.h"
#include "Tools/AddEventGraphEventTool.h"
#include "Tools/WireEventGraphPinsTool.h"

// BPDoctor integration tools (4)
#include "Tools/BPDoctorScanTool.h"
#include "Tools/BPDoctorResultsTool.h"
#include "Tools/BPDoctorFixTool.h"
#include "Tools/BPDoctorFixAllTool.h"

IMPLEMENT_MODULE(FBionicsBridgeEditorModule, BionicsBridgeEditor);

void FBionicsBridgeEditorModule::StartupModule()
{
	UE_LOG(LogBionicsBridge, Log, TEXT("BionicsBridgeEditor starting up — registering editor tools"));

	FBionicsBridgeToolRegistry& Registry = FBionicsBridgeToolRegistry::Get();

	// General editor tools (5)
	Registry.RegisterToolClass<UCompileBlueprintTool>();
	Registry.RegisterToolClass<USaveAssetTool>();
	Registry.RegisterToolClass<UQueryAssetTool>();
	Registry.RegisterToolClass<USpawnActorEditorTool>();
	Registry.RegisterToolClass<ULiveCodingCompileTool>();

	// AnimGraph manipulation tools (13)
	Registry.RegisterToolClass<UQueryAnimGraphTool>();
	Registry.RegisterToolClass<UCreateAnimGraphNodeTool>();
	Registry.RegisterToolClass<UWireAnimGraphPinsTool>();
	Registry.RegisterToolClass<UUnwireAnimGraphPinsTool>();
	Registry.RegisterToolClass<UDeleteAnimGraphNodeTool>();
	Registry.RegisterToolClass<USetAnimNodePropertyTool>();
	Registry.RegisterToolClass<UCreateStateMachineTool>();
	Registry.RegisterToolClass<UAddStateTransitionTool>();
	Registry.RegisterToolClass<USetBoneReferenceTool>();
	Registry.RegisterToolClass<UBindPinToPropertyTool>();
	Registry.RegisterToolClass<UUnbindPinFromPropertyTool>();           // 2026-05-15 orphan-impl restore
	Registry.RegisterToolClass<USplicePoseFlowTool>();
	Registry.RegisterToolClass<UCreateAnimGraphVariableGetTool>();      // LIMIT 2 fix
	Registry.RegisterToolClass<UDriveAnimGraphPinViaVariableTool>();    // LIMIT 1 fix

	// EventGraph (K2 / Ubergraph) manipulation tools (5) — combat polish enabler
	Registry.RegisterToolClass<UQueryEventGraphTool>();
	Registry.RegisterToolClass<UAddEventGraphCallFunctionTool>();
	Registry.RegisterToolClass<UAddEventGraphVariableNodeTool>();
	Registry.RegisterToolClass<UAddEventGraphEventTool>();
	Registry.RegisterToolClass<UWireEventGraphPinsTool>();

	// BPDoctor integration tools (4)
	Registry.RegisterToolClass<UBPDoctorScanTool>();
	Registry.RegisterToolClass<UBPDoctorResultsTool>();
	Registry.RegisterToolClass<UBPDoctorFixTool>();
	Registry.RegisterToolClass<UBPDoctorFixAllTool>();

	UE_LOG(LogBionicsBridge, Log, TEXT("Editor tools registered. Total tools: %d (5 general + 13 animgraph + 5 eventgraph + 4 bpdoctor)"), Registry.Num());
}

void FBionicsBridgeEditorModule::ShutdownModule()
{
	// Tool cleanup handled by runtime module
}
