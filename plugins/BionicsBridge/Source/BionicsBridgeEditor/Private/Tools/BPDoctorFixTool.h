// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "BPDoctorFixTool.generated.h"

/**
 * Applies a specific BPDoctor auto-fix by issue index from the last scan.
 * Wraps the fix in an undo transaction and recompiles the Blueprint.
 */
UCLASS()
class UBPDoctorFixTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("bpdoctor_fix"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Apply a BPDoctor auto-fix for a specific issue. Pass the issue_index "
		           "from bpdoctor_scan results. Only works on auto-fixable issues. "
		           "Wraps in undo transaction for safe rollback.");
	}
	virtual FString GetCategory() const override { return TEXT("bpdoctor"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
