// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "BindPinToPropertyTool.generated.h"

/**
 * Binds an AnimGraph node input pin to a UAnimInstance member variable, the
 * equivalent of right-clicking a pin in the editor and choosing "Bind to <var>".
 * Implements the binding via UAnimGraphNode_Base::PropertyBindings (PropertyAccess
 * type), the canonical UE5 path for variable-driven AnimGraph wiring.
 *
 * Closes T-BRIDGE-1 hole #2 (BUGS.md 2026-05-08).
 */
UCLASS()
class UBindPinToPropertyTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("bind_pin_to_property"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Bind an AnimGraph node input pin to a UAnimInstance member "
		            "variable (right-click → Bind in editor). Verifies the variable "
		            "exists on the AnimBP class hierarchy before binding.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
