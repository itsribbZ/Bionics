// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "SpawnActorEditorTool.generated.h"

UCLASS()
class USpawnActorEditorTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("spawn_actor_editor"); }
	virtual FString GetToolDescription() const override { return TEXT("Spawn an actor in the editor world with undo support."); }
	virtual FString GetCategory() const override { return TEXT("actor"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
