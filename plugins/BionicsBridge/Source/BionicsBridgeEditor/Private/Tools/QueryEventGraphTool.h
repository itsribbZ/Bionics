// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "QueryEventGraphTool.generated.h"

/**
 * Query a Blueprint's EventGraph (UbergraphPages) — every node, every pin.
 * Mirrors UQueryAnimGraphTool but operates on the K2 EventGraph instead of the AnimGraph.
 * Use this to discover what's already wired before adding new nodes.
 */
UCLASS()
class UQueryEventGraphTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("query_eventgraph"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Inspect a Blueprint's EventGraph (Ubergraph). Returns every node + pins + connections. "
		           "Works on any UBlueprint (Actor BP, Character BP, GameMode BP, Widget BP). "
		           "Use BEFORE adding nodes so you know which event nodes exist (BeginPlay, Tick, etc.) "
		           "and what's already connected.");
	}
	virtual FString GetCategory() const override { return TEXT("eventgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
