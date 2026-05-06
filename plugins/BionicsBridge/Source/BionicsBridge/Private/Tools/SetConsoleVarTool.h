// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "SetConsoleVarTool.generated.h"

UCLASS()
class USetConsoleVarTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("set_console_var"); }
	virtual FString GetToolDescription() const override { return TEXT("Set a UE console variable value."); }
	virtual FString GetCategory() const override { return TEXT("console"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
