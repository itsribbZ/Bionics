// Copyright Jacob Ribbe. Licensed under MIT.

#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "Dom/JsonObject.h"
#include "BionicsBridgeToolBase.generated.h"

/**
 * Base class for all BionicsBridge tools.
 *
 * Each tool subclasses this, overrides the metadata methods, and implements
 * Execute. Tools self-register via REGISTER_BRIDGE_TOOL or explicit call to
 * the FBionicsBridgeToolRegistry in the module's StartupModule.
 *
 * Tools run on the GAME THREAD via AsyncTask marshaling — they don't need
 * to handle threading themselves.
 */
UCLASS(Abstract)
class BIONICSBRIDGE_API UBionicsBridgeToolBase : public UObject
{
	GENERATED_BODY()

public:
	/** Tool name (matches the CLI/MCP tool name). */
	virtual FString GetToolName() const PURE_VIRTUAL(UBionicsBridgeToolBase::GetToolName, return TEXT(""); );

	/** One-line description for LLM. */
	virtual FString GetToolDescription() const PURE_VIRTUAL(UBionicsBridgeToolBase::GetToolDescription, return TEXT(""); );

	/** JSON Schema for tool inputs. */
	virtual TSharedPtr<FJsonObject> GetInputSchema() const PURE_VIRTUAL(UBionicsBridgeToolBase::GetInputSchema, return nullptr; );

	/** Category (actor/blueprint/asset/pie/...). */
	virtual FString GetCategory() const { return TEXT("general"); }

	/**
	 * Run the tool. Called on game thread.
	 * @param Arguments — parsed JSON object of arguments
	 * @param OutResult — JSON object to populate with result data
	 * @param OutError — error string if tool fails
	 * @return true on success, false on failure
	 */
	virtual bool Execute(const TSharedPtr<FJsonObject>& Arguments,
	                     TSharedPtr<FJsonObject>& OutResult,
	                     FString& OutError) PURE_VIRTUAL(UBionicsBridgeToolBase::Execute, return false; );

protected:
	// ---- Argument Helpers ----

	/** Safely extract a string argument with a default fallback. */
	FString GetStringArg(const TSharedPtr<FJsonObject>& Args, const FString& Key, const FString& Default = TEXT("")) const
	{
		if (Args.IsValid() && Args->HasTypedField<EJson::String>(Key))
			return Args->GetStringField(Key);
		return Default;
	}

	/** Safely extract a boolean argument. */
	bool GetBoolArg(const TSharedPtr<FJsonObject>& Args, const FString& Key, bool Default = false) const
	{
		if (Args.IsValid() && Args->HasTypedField<EJson::Boolean>(Key))
			return Args->GetBoolField(Key);
		return Default;
	}

	/** Safely extract an int argument. */
	int32 GetIntArg(const TSharedPtr<FJsonObject>& Args, const FString& Key, int32 Default = 0) const
	{
		if (Args.IsValid() && Args->HasTypedField<EJson::Number>(Key))
			return Args->GetIntegerField(Key);
		return Default;
	}

	/** Safely extract a float argument. */
	float GetFloatArg(const TSharedPtr<FJsonObject>& Args, const FString& Key, float Default = 0.0f) const
	{
		if (Args.IsValid() && Args->HasTypedField<EJson::Number>(Key))
			return static_cast<float>(Args->GetNumberField(Key));
		return Default;
	}

	/** Safely extract a 3-element vector argument [x,y,z]. */
	FVector GetVectorArg(const TSharedPtr<FJsonObject>& Args, const FString& Key, const FVector& Default = FVector::ZeroVector) const
	{
		if (Args.IsValid() && Args->HasTypedField<EJson::Array>(Key))
		{
			const TArray<TSharedPtr<FJsonValue>>& Arr = Args->GetArrayField(Key);
			if (Arr.Num() >= 3)
				return FVector(Arr[0]->AsNumber(), Arr[1]->AsNumber(), Arr[2]->AsNumber());
		}
		return Default;
	}

	/** Common helper: build an input schema with standard structure. */
	TSharedPtr<FJsonObject> MakeSchema(const TMap<FString, FString>& TypeMap,
	                                    const TArray<FString>& Required) const
	{
		TSharedPtr<FJsonObject> Schema = MakeShared<FJsonObject>();
		Schema->SetStringField(TEXT("type"), TEXT("object"));

		TSharedPtr<FJsonObject> Properties = MakeShared<FJsonObject>();
		for (const auto& Pair : TypeMap)
		{
			TSharedPtr<FJsonObject> Prop = MakeShared<FJsonObject>();
			Prop->SetStringField(TEXT("type"), Pair.Value);
			Properties->SetObjectField(Pair.Key, Prop);
		}
		Schema->SetObjectField(TEXT("properties"), Properties);

		if (Required.Num() > 0)
		{
			TArray<TSharedPtr<FJsonValue>> Req;
			for (const FString& R : Required)
				Req.Add(MakeShared<FJsonValueString>(R));
			Schema->SetArrayField(TEXT("required"), Req);
		}
		return Schema;
	}
};
