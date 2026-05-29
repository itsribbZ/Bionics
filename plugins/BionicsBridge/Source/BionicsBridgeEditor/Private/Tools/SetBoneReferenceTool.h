// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "SetBoneReferenceTool.generated.h"

/**
 * Sets an FBoneReference field on an AnimGraph node (e.g. ModifyBone.BoneToModify,
 * TwoBoneIK.IKBone). Resolves MeshBoneIndex against the AnimBP's TargetSkeleton —
 * the step that set_animnode_property cannot do because ImportText_Direct does not
 * call FBoneReference::Initialize().
 *
 * Closes T-BRIDGE-1 hole #1 (BUGS.md 2026-05-08).
 */
UCLASS()
class USetBoneReferenceTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("set_bone_reference"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Set an FBoneReference field on an AnimGraph node. Resolves "
		            "MeshBoneIndex against the AnimBP's TargetSkeleton. Use for "
		            "ModifyBone.BoneToModify, TwoBoneIK.IKBone, etc.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
