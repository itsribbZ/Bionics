// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "AddEventGraphVariableNodeTool.generated.h"

/**
 * Add a K2Node_VariableGet or K2Node_VariableSet node to a Blueprint's EventGraph.
 *
 * Reads/writes a member variable on the Blueprint (or a referenced object).
 * Combat polish path: read `Health`, write `LastHitTime`, set `bIsInvulnerable` from a montage notify.
 */
UCLASS()
class UAddEventGraphVariableNodeTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("add_eventgraph_variable_node"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Add a VariableGet (read) or VariableSet (write) node to an EventGraph. "
		           "Specify variable_name (must exist on the BP class) and operation ('get' or 'set'). "
		           "Returns the new node's pin list.");
	}
	virtual FString GetCategory() const override { return TEXT("eventgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
