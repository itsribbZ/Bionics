// Copyright Jacob Ribbe. Licensed under MIT.
// All runtime tool implementations in one file for compile efficiency.

#include "Tools/GetActorsTool.h"
#include "Tools/SpawnActorRuntimeTool.h"
#include "Tools/GetConsoleVarTool.h"
#include "Tools/SetConsoleVarTool.h"
#include "Tools/ExecuteConsoleCommandTool.h"
#include "Tools/GetProjectInfoTool.h"

#include "BionicsBridgeModule.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "Kismet/GameplayStatics.h"
#include "HAL/IConsoleManager.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/App.h"
#include "Misc/EngineVersion.h"
#include "Misc/Paths.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/UObjectIterator.h"

static UWorld* ResolveTargetWorld()
{
	// Prefer game/PIE world if one exists
	if (GEngine && GEngine->GetWorldContexts().Num() > 0)
	{
		for (const FWorldContext& Ctx : GEngine->GetWorldContexts())
		{
			if (Ctx.World() && Ctx.WorldType == EWorldType::PIE)
			{
				return Ctx.World();
			}
		}
		for (const FWorldContext& Ctx : GEngine->GetWorldContexts())
		{
			if (Ctx.World() && Ctx.WorldType == EWorldType::Game)
			{
				return Ctx.World();
			}
		}
#if WITH_EDITOR
		for (const FWorldContext& Ctx : GEngine->GetWorldContexts())
		{
			if (Ctx.World() && Ctx.WorldType == EWorldType::Editor)
			{
				return Ctx.World();
			}
		}
#endif
	}
	return nullptr;
}

// ---- GetActorsTool ----

TSharedPtr<FJsonObject> UGetActorsTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("class_filter"), TEXT("string")},
		{TEXT("name_filter"),  TEXT("string")},
		{TEXT("limit"),        TEXT("integer")},
	}, {});
}

bool UGetActorsTool::Execute(const TSharedPtr<FJsonObject>& Args,
                              TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	UWorld* World = ResolveTargetWorld();
	if (!World) { OutError = TEXT("No world available"); return false; }

	const FString ClassFilter = GetStringArg(Args, TEXT("class_filter"));
	const FString NameFilter = GetStringArg(Args, TEXT("name_filter"));
	const int32 Limit = FMath::Clamp(GetIntArg(Args, TEXT("limit"), 100), 1, 10000);

	TArray<TSharedPtr<FJsonValue>> ActorsJson;
	int32 Found = 0;
	for (TActorIterator<AActor> It(World); It; ++It)
	{
		AActor* Actor = *It;
		if (!Actor) continue;
		if (!ClassFilter.IsEmpty() && !Actor->GetClass()->GetName().Contains(ClassFilter)) continue;
#if WITH_EDITOR
		const FString Label = Actor->GetActorLabel();
#else
		const FString Label = Actor->GetName();
#endif
		const FString Name = Actor->GetName();
		if (!NameFilter.IsEmpty() && !Label.Contains(NameFilter) && !Name.Contains(NameFilter)) continue;

		TSharedPtr<FJsonObject> ActorJson = MakeShared<FJsonObject>();
		ActorJson->SetStringField(TEXT("name"), Name);
		ActorJson->SetStringField(TEXT("label"), Label);
		ActorJson->SetStringField(TEXT("class"), Actor->GetClass()->GetName());
		const FVector Loc = Actor->GetActorLocation();
		TArray<TSharedPtr<FJsonValue>> LocArr = {
			MakeShared<FJsonValueNumber>(Loc.X),
			MakeShared<FJsonValueNumber>(Loc.Y),
			MakeShared<FJsonValueNumber>(Loc.Z),
		};
		ActorJson->SetArrayField(TEXT("location"), LocArr);
		ActorsJson.Add(MakeShared<FJsonValueObject>(ActorJson));
		if (++Found >= Limit) break;
	}

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetArrayField(TEXT("actors"), ActorsJson);
	OutResult->SetNumberField(TEXT("count"), Found);
	return true;
}

// ---- SpawnActorRuntimeTool ----

TSharedPtr<FJsonObject> USpawnActorRuntimeTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("actor_class"), TEXT("string")},
		{TEXT("location"),    TEXT("array")},
		{TEXT("rotation"),    TEXT("array")},
		{TEXT("label"),       TEXT("string")},
	}, {TEXT("actor_class")});
}

bool USpawnActorRuntimeTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                      TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	UWorld* World = ResolveTargetWorld();
	if (!World) { OutError = TEXT("No world available"); return false; }

	const FString ClassPath = GetStringArg(Args, TEXT("actor_class"));
	if (ClassPath.IsEmpty()) { OutError = TEXT("actor_class required"); return false; }

	UClass* ActorCls = LoadObject<UClass>(nullptr, *ClassPath);
	if (!ActorCls)
	{
		// Try native class lookup
		for (TObjectIterator<UClass> It; It; ++It)
		{
			if (It->IsChildOf(AActor::StaticClass()) && It->GetName() == ClassPath)
			{
				ActorCls = *It;
				break;
			}
		}
	}
	if (!ActorCls) { OutError = FString::Printf(TEXT("Class not found: %s"), *ClassPath); return false; }

	const FVector Location = GetVectorArg(Args, TEXT("location"), FVector::ZeroVector);
	const FVector Rot = GetVectorArg(Args, TEXT("rotation"), FVector::ZeroVector);
	const FRotator Rotation(Rot.X, Rot.Y, Rot.Z);

	FActorSpawnParameters Params;
	Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	AActor* Spawned = World->SpawnActor<AActor>(ActorCls, Location, Rotation, Params);
	if (!Spawned) { OutError = TEXT("Spawn failed"); return false; }

	const FString Label = GetStringArg(Args, TEXT("label"));
#if WITH_EDITOR
	if (!Label.IsEmpty()) Spawned->SetActorLabel(Label);
#endif

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetStringField(TEXT("name"), Spawned->GetName());
#if WITH_EDITOR
	OutResult->SetStringField(TEXT("label"), Spawned->GetActorLabel());
#endif
	OutResult->SetStringField(TEXT("class"), ActorCls->GetName());
	return true;
}

// ---- GetConsoleVarTool ----

TSharedPtr<FJsonObject> UGetConsoleVarTool::GetInputSchema() const
{
	return MakeSchema({ {TEXT("name"), TEXT("string")} }, { TEXT("name") });
}

bool UGetConsoleVarTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                  TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString Name = GetStringArg(Args, TEXT("name"));
	if (Name.IsEmpty()) { OutError = TEXT("name required"); return false; }
	IConsoleVariable* CVar = IConsoleManager::Get().FindConsoleVariable(*Name);
	if (!CVar) { OutError = FString::Printf(TEXT("CVar not found: %s"), *Name); return false; }

	OutResult = MakeShared<FJsonObject>();
	OutResult->SetStringField(TEXT("name"), Name);
	OutResult->SetStringField(TEXT("string_value"), CVar->GetString());
	OutResult->SetNumberField(TEXT("float_value"), CVar->GetFloat());
	OutResult->SetNumberField(TEXT("int_value"), CVar->GetInt());
	return true;
}

// ---- SetConsoleVarTool ----

TSharedPtr<FJsonObject> USetConsoleVarTool::GetInputSchema() const
{
	return MakeSchema({
		{TEXT("name"),  TEXT("string")},
		{TEXT("value"), TEXT("string")},
	}, {TEXT("name"), TEXT("value")});
}

bool USetConsoleVarTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                  TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString Name = GetStringArg(Args, TEXT("name"));
	const FString Value = GetStringArg(Args, TEXT("value"));
	if (Name.IsEmpty()) { OutError = TEXT("name required"); return false; }
	IConsoleVariable* CVar = IConsoleManager::Get().FindConsoleVariable(*Name);
	if (!CVar) { OutError = FString::Printf(TEXT("CVar not found: %s"), *Name); return false; }
	CVar->Set(*Value, ECVF_SetByConsole);
	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("ok"), true);
	OutResult->SetStringField(TEXT("name"), Name);
	OutResult->SetStringField(TEXT("new_value"), CVar->GetString());
	return true;
}

// ---- ExecuteConsoleCommandTool ----

TSharedPtr<FJsonObject> UExecuteConsoleCommandTool::GetInputSchema() const
{
	return MakeSchema({ {TEXT("command"), TEXT("string")} }, { TEXT("command") });
}

bool UExecuteConsoleCommandTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                          TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	const FString Command = GetStringArg(Args, TEXT("command"));
	if (Command.IsEmpty()) { OutError = TEXT("command required"); return false; }
	UWorld* World = ResolveTargetWorld();
	if (GEngine)
	{
		GEngine->Exec(World, *Command);
	}
	OutResult = MakeShared<FJsonObject>();
	OutResult->SetBoolField(TEXT("ok"), true);
	OutResult->SetStringField(TEXT("command"), Command);
	return true;
}

// ---- GetProjectInfoTool ----

TSharedPtr<FJsonObject> UGetProjectInfoTool::GetInputSchema() const
{
	return MakeSchema({}, {});
}

bool UGetProjectInfoTool::Execute(const TSharedPtr<FJsonObject>& Args,
                                   TSharedPtr<FJsonObject>& OutResult, FString& OutError)
{
	OutResult = MakeShared<FJsonObject>();
	OutResult->SetStringField(TEXT("project_name"), FApp::GetProjectName());
	OutResult->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());
	OutResult->SetStringField(TEXT("project_dir"), FPaths::ProjectDir());
	OutResult->SetStringField(TEXT("content_dir"), FPaths::ProjectContentDir());
	OutResult->SetStringField(TEXT("engine_dir"), FPaths::EngineDir());
	OutResult->SetBoolField(TEXT("is_editor"), GIsEditor);
	return true;
}
