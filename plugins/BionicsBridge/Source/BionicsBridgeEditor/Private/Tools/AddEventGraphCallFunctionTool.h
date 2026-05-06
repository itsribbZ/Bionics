// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "AddEventGraphCallFunctionTool.generated.h"

/**
 * Add a K2Node_CallFunction node to a Blueprint's EventGraph.
 *
 * This is THE high-leverage EventGraph tool — unlocks programmatic wiring of:
 *   • PlayMontage / PlayAnimation
 *   • UGameplayStatics::SpawnEmitterAtLocation (Niagara/Cascade VFX)
 *   • UGameplayStatics::PlaySoundAtLocation (combat audio)
 *   • UAbilitySystemComponent::ExecuteGameplayCue (GAS cues)
 *   • USetTimerByFunctionName (hitstop timers)
 *   • UCameraShakeBase::PlayCameraShake (screenshake)
 *   • Any UFUNCTION (BlueprintCallable / BlueprintPure / BlueprintNativeEvent).
 *
 * Caller specifies the target_class (e.g. "GameplayStatics") + function_name (e.g. "SpawnEmitterAtLocation").
 * Tool resolves the UFunction, creates the node, wires it for execution.
 */
UCLASS()
class UAddEventGraphCallFunctionTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("add_eventgraph_call_function"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Add a CallFunction node to an EventGraph. Specify target_class (e.g. 'GameplayStatics', "
		           "'KismetMathLibrary', or '' for self) and function_name (e.g. 'SpawnEmitterAtLocation', "
		           "'PlayMontage'). Returns the new node's name + pin list. Use ue5_wire_eventgraph_pins "
		           "to connect its exec/data pins.");
	}
	virtual FString GetCategory() const override { return TEXT("eventgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
