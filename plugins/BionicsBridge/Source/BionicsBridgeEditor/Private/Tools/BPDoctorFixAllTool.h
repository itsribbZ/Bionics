// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "BPDoctorFixAllTool.generated.h"

/**
 * Applies ALL auto-fixable BPDoctor issues from the last scan.
 * Processes fixes in priority order (errors first, then warnings).
 * Each fix wrapped in its own undo transaction.
 */
UCLASS()
class UBPDoctorFixAllTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("bpdoctor_fix_all"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Apply ALL auto-fixable BPDoctor issues from the most recent scan. "
		           "Processes in priority order. Returns count of fixes applied, "
		           "fixes failed, and remaining issues. Re-scans after to verify.");
	}
	virtual FString GetCategory() const override { return TEXT("bpdoctor"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
