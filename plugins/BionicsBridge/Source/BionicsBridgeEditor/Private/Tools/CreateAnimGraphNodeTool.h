// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "CreateAnimGraphNodeTool.generated.h"

/**
 * Creates a new AnimGraph node in an Animation Blueprint.
 * Supports all standard node types: SequencePlayer, BlendSpacePlayer,
 * StateMachine, Slot, LayeredBoneBlend, BlendListByBool, SaveCachedPose,
 * UseCachedPose, LinkedAnimLayer, TwoWayBlend, and more.
 */
UCLASS()
class UCreateAnimGraphNodeTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("create_animgraph_node"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Create a new node in an AnimBlueprint's AnimGraph. Specify node_class "
		           "(e.g. 'AnimGraphNode_SequencePlayer', 'AnimGraphNode_BlendSpacePlayer', "
		           "'AnimGraphNode_StateMachine', 'AnimGraphNode_Slot', 'AnimGraphNode_LayeredBoneBlend', "
		           "'AnimGraphNode_BlendListByBool', 'AnimGraphNode_TwoWayBlend', "
		           "'AnimGraphNode_SaveCachedPose', 'AnimGraphNode_UseCachedPose', "
		           "'AnimGraphNode_LinkedAnimLayer', 'AnimGraphNode_MotionMatching' (requires PoseSearch), "
		           "'AnimGraphNode_Inertialization') "
		           "and optional position. Returns the new node's ID and pin list.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
