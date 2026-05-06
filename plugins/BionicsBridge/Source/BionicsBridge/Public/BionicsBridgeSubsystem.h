// Copyright Jacob Ribbe. Licensed under MIT.

#pragma once

#include "CoreMinimal.h"
#include "Subsystems/EngineSubsystem.h"
#include "BionicsBridgeServer.h"
#include "BionicsBridgeSubsystem.generated.h"

/**
 * Engine subsystem that manages the BionicsBridge HTTP server lifecycle.
 *
 * Starts at engine boot, survives PIE start/stop, works in editor + PIE +
 * packaged builds (unlike a GameInstanceSubsystem).
 *
 * Port resolution:
 *   1. BIONICS_BRIDGE_PORT env var
 *   2. Default 8090
 * If the port is taken, tries port+1..port+9.
 */
UCLASS()
class BIONICSBRIDGE_API UBionicsBridgeSubsystem : public UEngineSubsystem
{
	GENERATED_BODY()

public:
	UBionicsBridgeSubsystem();
	~UBionicsBridgeSubsystem();

	// UEngineSubsystem
	virtual void Initialize(FSubsystemCollectionBase& Collection) override;
	virtual void Deinitialize() override;

	/** Returns true if the HTTP server is running. */
	UFUNCTION(BlueprintCallable, Category = "BionicsBridge")
	bool IsServerRunning() const;

	/** Returns the port the server is bound to (0 if not running). */
	UFUNCTION(BlueprintCallable, Category = "BionicsBridge")
	int32 GetServerPort() const;

	/** Restart the server (e.g. after changing port). */
	UFUNCTION(BlueprintCallable, Category = "BionicsBridge")
	bool RestartServer();

	/** Stop the server. */
	UFUNCTION(BlueprintCallable, Category = "BionicsBridge")
	void StopServer();

private:
	TUniquePtr<FBionicsBridgeServer> Server;

	/** Write discovery file <ProjectDir>/.bionics-bridge/instance.json so CLI can find us. */
	void WriteDiscoveryFile() const;
	void ClearDiscoveryFile() const;

	/** Resolve desired port from env var or default. */
	int32 ResolveDesiredPort() const;
};
