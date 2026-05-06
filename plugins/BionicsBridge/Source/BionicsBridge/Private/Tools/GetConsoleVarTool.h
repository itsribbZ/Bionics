// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "GetConsoleVarTool.generated.h"

UCLASS()
class UGetConsoleVarTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("get_console_var"); }
	virtual FString GetToolDescription() const override { return TEXT("Read a UE console variable value."); }
	virtual FString GetCategory() const override { return TEXT("console"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
