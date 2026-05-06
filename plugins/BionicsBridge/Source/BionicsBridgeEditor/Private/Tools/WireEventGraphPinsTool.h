// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "WireEventGraphPinsTool.generated.h"

/**
 * Wire two pins in a Blueprint's EventGraph using UEdGraphSchema_K2::TryCreateConnection.
 * Auto-compiles the Blueprint after wiring (configurable).
 *
 * Mirrors UWireAnimGraphPinsTool but for K2 EventGraph pins (exec + data wildcards + typed).
 */
UCLASS()
class UWireEventGraphPinsTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("wire_eventgraph_pins"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Connect two pins in a Blueprint's EventGraph. Uses UEdGraphSchema_K2 for type-safe "
		           "wiring (handles wildcards + automatic conversion nodes for compatible types). "
		           "Specify source_node + source_pin and target_node + target_pin (use 'execute' / 'then' "
		           "for exec pins, or the parameter name for data pins). Auto-compiles after wiring.");
	}
	virtual FString GetCategory() const override { return TEXT("eventgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
