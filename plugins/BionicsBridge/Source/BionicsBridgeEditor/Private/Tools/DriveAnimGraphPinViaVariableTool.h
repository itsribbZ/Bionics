// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "DriveAnimGraphPinViaVariableTool.generated.h"

/**
 * Atomic spawn-wire-compile alternative to bind_pin_to_property for runtime-correct
 * variable-driven AnimGraph pins.
 *
 * Closes LIMIT 1 from the 2026-05-15 blocker-eradication campaign —
 * UBindPinToPropertyTool writes PropertyBindings metadata but does NOT recompile
 * the AnimBP, so the FExposedValueHandler subsystem array stays stale and the
 * runtime never picks up the binding.
 *
 * Engine evidence:
 *   - Runtime reads: AnimNodeBase.cpp:262-275 GetEvaluateGraphExposedInputs
 *   - Bind-to-handler bridge: AnimBlueprintExtension_Base.cpp:400-436 (compile-only)
 *   - PropertyBindings consumer gate: AnimGraphNodeBinding_Base.cpp:373-385
 *
 * Pattern: K2Node_VariableGet + MakeLinkTo + CompileBlueprint, atomic in one
 * tool call. Runtime-correct because explicit graph wires ARE what
 * ProcessNonPosePins:342-350 registers as eval handlers in the compiled class.
 */
UCLASS()
class UDriveAnimGraphPinViaVariableTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("drive_animgraph_pin_via_variable"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Atomic spawn-wire-compile: creates a K2Node_VariableGet for the "
		            "given AnimBP variable, wires its output to the target anim node's "
		            "input pin, then calls FKismetEditorUtilities::CompileBlueprint so "
		            "the runtime FExposedValueHandler subsystem picks up the binding. "
		            "Runtime-correct alternative to bind_pin_to_property which is "
		            "metadata-only and does NOT propagate without compile.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
