// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "AddStateTransitionTool.generated.h"

/**
 * Adds a transition rule between two states in an AnimGraph state machine.
 * Can set the transition condition via a boolean variable name.
 */
UCLASS()
class UAddStateTransitionTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("add_state_transition"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Add a transition between two states in an AnimGraph state machine. "
		           "Specify source_state and target_state names. Optionally set a boolean "
		           "condition variable for the transition rule.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
