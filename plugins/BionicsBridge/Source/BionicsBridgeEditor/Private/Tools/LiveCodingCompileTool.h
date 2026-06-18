// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "LiveCodingCompileTool.generated.h"

UCLASS()
class ULiveCodingCompileTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("live_coding_compile"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Trigger Live Coding hot reload (Ctrl+Alt+F11 equivalent). "
		            "Returns module_loaded + triggered status; surfaces enable hint if disabled.");
	}
	virtual FString GetCategory() const override { return TEXT("build"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
