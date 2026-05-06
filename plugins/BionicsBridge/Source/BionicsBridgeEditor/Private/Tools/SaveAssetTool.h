// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "SaveAssetTool.generated.h"

UCLASS()
class USaveAssetTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("save_asset"); }
	virtual FString GetToolDescription() const override { return TEXT("Save an asset to disk."); }
	virtual FString GetCategory() const override { return TEXT("asset"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
