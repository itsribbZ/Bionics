// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "LogTailTool.generated.h"

UCLASS()
class ULogTailTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("log_tail"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Tail UE5 project log (Saved/Logs/<ProjectName>.log). "
		            "Returns new lines since cursor + new cursor for incremental polling. "
		            "Optional regex filter for server-side line selection.");
	}
	virtual FString GetCategory() const override { return TEXT("debug"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
