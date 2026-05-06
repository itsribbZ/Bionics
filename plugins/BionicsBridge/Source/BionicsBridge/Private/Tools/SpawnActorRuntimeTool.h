// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "SpawnActorRuntimeTool.generated.h"

UCLASS()
class USpawnActorRuntimeTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("spawn_actor_runtime"); }
	virtual FString GetToolDescription() const override { return TEXT("Spawn an actor in the current world (works in PIE and packaged builds)."); }
	virtual FString GetCategory() const override { return TEXT("actor"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
