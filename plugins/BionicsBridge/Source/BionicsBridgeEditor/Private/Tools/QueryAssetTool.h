// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "QueryAssetTool.generated.h"

UCLASS()
class UQueryAssetTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("query_assets"); }
	virtual FString GetToolDescription() const override { return TEXT("Search Content Browser for assets by class and path."); }
	virtual FString GetCategory() const override { return TEXT("asset"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
