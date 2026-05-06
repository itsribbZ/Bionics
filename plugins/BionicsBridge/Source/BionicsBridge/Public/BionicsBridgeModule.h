// Copyright Jacob Ribbe. Licensed under MIT.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"
#include "Modules/ModuleManager.h"

BIONICSBRIDGE_API DECLARE_LOG_CATEGORY_EXTERN(LogBionicsBridge, Log, All);

class FBionicsBridgeModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

	static FBionicsBridgeModule& Get();
};
