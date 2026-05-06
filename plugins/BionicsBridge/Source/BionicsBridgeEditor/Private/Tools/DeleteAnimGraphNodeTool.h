// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "DeleteAnimGraphNodeTool.generated.h"

/**
 * Removes a node from an AnimBlueprint's AnimGraph.
 * Breaks all pin connections first, then removes the node.
 * Cannot delete the Output Pose (Root) node.
 */
UCLASS()
class UDeleteAnimGraphNodeTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("delete_animgraph_node"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Delete a node from an AnimBlueprint's AnimGraph. Breaks all connections first. "
		           "Cannot delete the Output Pose root node. Wraps in undo transaction.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
