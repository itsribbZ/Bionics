// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "SplicePoseFlowTool.generated.h"

/**
 * Inserts a node into an existing pose flow between source and sink pins. Atomic
 * "break existing wire → wire source→splice.in → wire splice.out→sink" operation.
 * Replaces the manual editor sequence of unwire + 2× wire, which previously
 * required 3 separate bridge calls + a query in between (and query_animgraph
 * doesn't fully return existing pin connections — see T-BRIDGE-1).
 *
 * Closes T-BRIDGE-1 hole #3 (BUGS.md 2026-05-08).
 */
UCLASS()
class USplicePoseFlowTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("splice_pose_flow"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Atomic insert: break existing source→sink wire, then wire "
		            "source→splice.in and splice.out→sink. Reports whether an "
		            "existing wire was broken (false = first-time wiring).");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
