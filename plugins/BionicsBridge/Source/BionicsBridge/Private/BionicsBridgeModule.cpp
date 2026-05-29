// Copyright Jacob Ribbe. Licensed under MIT.

#include "BionicsBridgeModule.h"
#include "BionicsBridgeToolRegistry.h"

// Include all runtime tools here for registration
#include "Tools/GetActorsTool.h"
#include "Tools/SpawnActorRuntimeTool.h"
#include "Tools/GetConsoleVarTool.h"
#include "Tools/SetConsoleVarTool.h"
#include "Tools/ExecuteConsoleCommandTool.h"
#include "Tools/GetProjectInfoTool.h"
#include "Tools/LogTailTool.h"

DEFINE_LOG_CATEGORY(LogBionicsBridge);

IMPLEMENT_MODULE(FBionicsBridgeModule, BionicsBridge);

FBionicsBridgeModule& FBionicsBridgeModule::Get()
{
	return FModuleManager::LoadModuleChecked<FBionicsBridgeModule>("BionicsBridge");
}

void FBionicsBridgeModule::StartupModule()
{
	UE_LOG(LogBionicsBridge, Log, TEXT("BionicsBridge starting up — registering runtime tools"));

	FBionicsBridgeToolRegistry& Registry = FBionicsBridgeToolRegistry::Get();
	Registry.RegisterToolClass<UGetActorsTool>();
	Registry.RegisterToolClass<USpawnActorRuntimeTool>();
	Registry.RegisterToolClass<UGetConsoleVarTool>();
	Registry.RegisterToolClass<USetConsoleVarTool>();
	Registry.RegisterToolClass<UExecuteConsoleCommandTool>();
	Registry.RegisterToolClass<UGetProjectInfoTool>();
	Registry.RegisterToolClass<ULogTailTool>();

	UE_LOG(LogBionicsBridge, Log, TEXT("Registered %d runtime tools"), Registry.Num());
}

void FBionicsBridgeModule::ShutdownModule()
{
	UE_LOG(LogBionicsBridge, Log, TEXT("BionicsBridge shutting down"));
	FBionicsBridgeToolRegistry::Get().Shutdown();
}
