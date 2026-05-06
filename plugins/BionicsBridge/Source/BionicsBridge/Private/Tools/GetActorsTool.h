// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "GetActorsTool.generated.h"

UCLASS()
class UGetActorsTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("get_actors"); }
	virtual FString GetToolDescription() const override { return TEXT("List all actors in the current world with names, classes, and transforms."); }
	virtual FString GetCategory() const override { return TEXT("actor"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
