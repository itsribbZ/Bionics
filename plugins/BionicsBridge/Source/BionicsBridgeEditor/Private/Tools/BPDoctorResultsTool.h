// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "BPDoctorResultsTool.generated.h"

/**
 * Returns the results of the most recent BPDoctor scan as JSON.
 * Supports filtering by severity, check code, and asset path.
 */
UCLASS()
class UBPDoctorResultsTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("bpdoctor_results"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Get results from the most recent BPDoctor scan. Filter by severity "
		           "(error/warning/info), check_code, or asset_path. Returns issue list "
		           "with full detail for each finding.");
	}
	virtual FString GetCategory() const override { return TEXT("bpdoctor"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
