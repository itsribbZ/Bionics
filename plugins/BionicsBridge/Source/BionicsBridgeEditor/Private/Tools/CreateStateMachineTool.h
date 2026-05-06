// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "CreateStateMachineTool.generated.h"

/**
 * Creates an AnimGraph state machine with named states.
 * Each state gets its own sub-graph. Entry state is auto-wired.
 * Use add_state_transition to wire transitions between states.
 */
UCLASS()
class UCreateStateMachineTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("create_state_machine"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Create a state machine node in an AnimBP's AnimGraph with named states. "
		           "Each state gets a sub-graph. The first state is wired as the entry state. "
		           "Returns the state machine node ID and state node IDs.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
