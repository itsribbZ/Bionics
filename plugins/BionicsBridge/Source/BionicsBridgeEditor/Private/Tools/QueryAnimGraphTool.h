// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "QueryAnimGraphTool.generated.h"

/**
 * Returns the full AnimGraph structure of an AnimBlueprint:
 * all nodes with class, position, pins, and connections.
 * This is the "eyes" for Bionics into any AnimBP graph.
 */
UCLASS()
class UQueryAnimGraphTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("query_animgraph"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Get full AnimGraph structure (nodes, pins, connections) of an Animation Blueprint. "
		           "Returns every node with its class, display name, position, input/output pins, "
		           "and what each pin is connected to.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
