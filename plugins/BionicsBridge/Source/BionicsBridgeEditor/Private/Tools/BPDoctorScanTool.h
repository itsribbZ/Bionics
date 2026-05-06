// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "BPDoctorScanTool.generated.h"

/**
 * Triggers a BPDoctor scan on a specific Blueprint or the entire project.
 * Returns scan results as structured JSON with severity, check codes,
 * descriptions, and auto-fix availability.
 *
 * Requires the BPDoctor plugin to be loaded in the project.
 */
UCLASS()
class UBPDoctorScanTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("bpdoctor_scan"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Run BPDoctor diagnostic scan on a Blueprint or the entire project. "
		           "Returns all issues found with severity (Error/Warning/Info), check codes, "
		           "descriptions, fix availability, and health grade (A-F). "
		           "Pass asset_path for single-asset scan, or omit for full project scan.");
	}
	virtual FString GetCategory() const override { return TEXT("bpdoctor"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
