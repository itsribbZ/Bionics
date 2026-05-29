// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "CreateAnimGraphVariableGetTool.generated.h"

/**
 * Spawn a K2Node_VariableGet inside an AnimBP's AnimGraph (NOT EventGraph).
 *
 * Closes LIMIT 2 from the 2026-05-15 blocker-eradication campaign — Python
 * cannot construct K2Node_VariableGet because FMemberReference has protected
 * UPROPERTY fields with no UFUNCTION mutators (MemberReference.h:63-95).
 *
 * The canonical 7-line pattern (NewObject -> SetSelfMember -> CreateNewGuid ->
 * AddNode -> PostPlacedNewNode -> AllocateDefaultPins -> MarkBlueprintAsModified)
 * is proven in EventGraphTools.cpp:454 + AnimGraphTools.cpp:1088. This tool
 * exposes it as a Bionics MCP call for AnimGraph specifically.
 *
 * For wiring + compile in one atomic op, use drive_animgraph_pin_via_variable.
 */
UCLASS()
class UCreateAnimGraphVariableGetTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("create_animgraph_variable_get"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Spawn a K2Node_VariableGet inside an AnimBP's AnimGraph. "
		            "Validates the variable exists on the AnimBP class. Returns the "
		            "new node's name + GUID + output pin name. Does NOT wire or compile — "
		            "use drive_animgraph_pin_via_variable for the atomic spawn+wire+compile.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
