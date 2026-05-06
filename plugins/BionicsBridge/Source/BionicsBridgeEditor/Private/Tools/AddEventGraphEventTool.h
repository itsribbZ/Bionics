// Copyright Jacob Ribbe. Licensed under MIT.
#pragma once

#include "BionicsBridgeToolBase.h"
#include "AddEventGraphEventTool.generated.h"

/**
 * Add an event entry node to a Blueprint's EventGraph.
 *
 * Two modes:
 *   • engine_event: BeginPlay, EndPlay, Tick, ActorBeginOverlap, AnyDamage, etc.
 *     (resolves UFunction from AActor / referenced base class).
 *   • custom_event: spawns K2Node_CustomEvent with a user-supplied name (e.g. "OnHitStop").
 *
 * Combat polish path: add Tick → drive hitstop timer poll. Or custom event "OnMontageNotify" →
 * triggered by AnimNotify_PlayMontageNotify.
 */
UCLASS()
class UAddEventGraphEventTool : public UBionicsBridgeToolBase
{
	GENERATED_BODY()
public:
	virtual FString GetToolName() const override { return TEXT("add_eventgraph_event"); }
	virtual FString GetToolDescription() const override
	{
		return TEXT("Add an event entry node to a Blueprint's EventGraph. "
		           "Specify event_type ('engine' for stock events like BeginPlay/Tick/ActorBeginOverlap, "
		           "or 'custom' for a custom event you'll dispatch later). "
		           "For engine: event_name = 'ReceiveBeginPlay', 'ReceiveTick', etc. "
		           "For custom: event_name = your event's name (e.g. 'OnHitStop').");
	}
	virtual FString GetCategory() const override { return TEXT("eventgraph"); }
	virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult, FString& OutError) override;
};
