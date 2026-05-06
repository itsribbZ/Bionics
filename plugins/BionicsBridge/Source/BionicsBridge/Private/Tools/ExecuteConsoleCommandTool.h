// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "ExecuteConsoleCommandTool.generated.h"

UCLASS()
class UExecuteConsoleCommandTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("execute_console_command"); }
	virtual FString GetToolDescription() const override { return TEXT("Execute a UE console command (stat fps, r.SetRes, etc.)."); }
	virtual FString GetCategory() const override { return TEXT("console"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
