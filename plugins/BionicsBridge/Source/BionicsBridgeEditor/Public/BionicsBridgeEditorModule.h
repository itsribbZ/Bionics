// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"

class FBionicsBridgeEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
};
