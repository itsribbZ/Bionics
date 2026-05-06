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

// AnimGraph manipulation tools (8)
#include "Tools/QueryAnimGraphTool.h"
#include "Tools/CreateAnimGraphNodeTool.h"
#include "Tools/WireAnimGraphPinsTool.h"
#include "Tools/UnwireAnimGraphPinsTool.h"
#include "Tools/DeleteAnimGraphNodeTool.h"
#include "Tools/SetAnimNodePropertyTool.h"
#include "Tools/CreateStateMachineTool.h"
#include "Tools/AddStateTransitionTool.h"

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

	// General editor tools (4)
	Registry.RegisterToolClass<UCompileBlueprintTool>();
	Registry.RegisterToolClass<USaveAssetTool>();
	Registry.RegisterToolClass<UQueryAssetTool>();
	Registry.RegisterToolClass<USpawnActorEditorTool>();

	// AnimGraph manipulation tools (8)
	Registry.RegisterToolClass<UQueryAnimGraphTool>();
	Registry.RegisterToolClass<UCreateAnimGraphNodeTool>();
	Registry.RegisterToolClass<UWireAnimGraphPinsTool>();
	Registry.RegisterToolClass<UUnwireAnimGraphPinsTool>();
	Registry.RegisterToolClass<UDeleteAnimGraphNodeTool>();
	Registry.RegisterToolClass<USetAnimNodePropertyTool>();
	Registry.RegisterToolClass<UCreateStateMachineTool>();
	Registry.RegisterToolClass<UAddStateTransitionTool>();

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

	UE_LOG(LogBionicsBridge, Log, TEXT("Editor tools registered. Total tools: %d (4 general + 8 animgraph + 5 eventgraph + 4 bpdoctor)"), Registry.Num());
}

void FBionicsBridgeEditorModule::ShutdownModule()
{
	// Tool cleanup handled by runtime module
}
