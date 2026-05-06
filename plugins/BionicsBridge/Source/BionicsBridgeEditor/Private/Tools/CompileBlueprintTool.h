// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "CompileBlueprintTool.generated.h"

UCLASS()
class UCompileBlueprintTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("compile_blueprint"); }
	virtual FString GetToolDescription() const override { return TEXT("Compile a Blueprint asset and return errors/warnings."); }
	virtual FString GetCategory() const override { return TEXT("blueprint"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
