// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "UnbindPinFromPropertyTool.generated.h"

/**
 * Removes a property binding from an AnimGraph node input pin. Sister tool to
 * UBindPinToPropertyTool — the canonical "Remove Binding" path used by the editor
 * itself when a user right-clicks a bound pin and selects Remove Binding.
 *
 * Mutates the private UAnimGraphNodeBinding_Base::PropertyBindings TMap via
 * reflection (same pattern as BindPinToPropertyTool — the symmetric forward op).
 * Removing a binding is metadata-only at runtime — the AnimBP must be recompiled
 * to drop the corresponding FExposedValueHandler from the subsystem array. For
 * runtime-correct rewiring use drive_animgraph_pin_via_variable (atomic
 * spawn+wire+CompileBlueprint) which sidesteps the binding system entirely.
 *
 * Built for B-CROUCH-WEAPON-POSE-1 root-cause cleanup (2026-05-11) — strip stale
 * LayeredBoneBlend.BlendWeights_0 ← UpperBodyBlendAlpha binding (float→TArray
 * type mismatch firing compile warning since 2026-05-09 eve).
 *
 * Impl restored 2026-05-15 godspeed campaign (was orphan header — link-breaking
 * pure-virtual declaration with no .cpp shipped). See
 * feedback_animgraph_binding_requires_compile.md for the metadata-only context.
 */
UCLASS()
class UUnbindPinFromPropertyTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("unbind_pin_from_property"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Remove a property binding from an AnimGraph node input pin "
		            "(reverse of bind_pin_to_property). Idempotent — no-op if no "
		            "binding exists on the named pin. Compile the AnimBP after to "
		            "purge the runtime FExposedValueHandler entry.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
