// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "SetAnimNodePropertyTool.generated.h"

/**
 * Sets properties on AnimGraph nodes — animation sequences, blend spaces,
 * slot names, blend weights, bone references, and other node-specific settings.
 */
UCLASS()
class USetAnimNodePropertyTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("set_animnode_property"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Set a property on an AnimGraph node. Supports: animation sequence assignment "
		           "(for SequencePlayer), blend space assignment, slot name (for Slot nodes), "
		           "blend weights, and generic UPROPERTY values via reflection.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
