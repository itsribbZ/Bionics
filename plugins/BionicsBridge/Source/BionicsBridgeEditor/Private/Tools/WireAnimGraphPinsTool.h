// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "WireAnimGraphPinsTool.generated.h"

/**
 * Connects two AnimGraph pins together.
 * Uses UEdGraphSchema::TryCreateConnection for type-safe wiring.
 * Auto-compiles the AnimBP after connection.
 */
UCLASS()
class UWireAnimGraphPinsTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("wire_animgraph_pins"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Connect two pins in an AnimBlueprint's AnimGraph. Specify source and target "
		           "node names and pin names. Validates pin compatibility before connecting. "
		           "Auto-compiles after wiring.");
	}
	virtual FString GetCategory() const override { return TEXT("animgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
