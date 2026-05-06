// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "UnwireAnimGraphPinsTool.generated.h"

/**
 * Disconnects AnimGraph pins. Can break a specific link or all links on a pin.
 */
UCLASS()
class UUnwireAnimGraphPinsTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("unwire_animgraph_pins"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Disconnect pins in an AnimBlueprint's AnimGraph. Specify node and pin to "
		           "break all connections, or specify both source and target to break a specific link.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
