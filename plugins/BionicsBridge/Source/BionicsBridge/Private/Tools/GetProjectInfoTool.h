// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "GetProjectInfoTool.generated.h"

UCLASS()
class UGetProjectInfoTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("get_project_info"); }
	virtual FString GetToolDescription() const override { return TEXT("Return project name, engine version, and directories."); }
	virtual FString GetCategory() const override { return TEXT("project"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
