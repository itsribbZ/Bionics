// Copyright Jacob Ribbe. Licensed under MIT.

#include "BionicsBridgeSubsystem.h"
#include "BionicsBridgeServer.h"
#include "BionicsBridgeModule.h"
#include "Misc/App.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "Misc/CoreDelegates.h"
#include "Misc/Guid.h"
#include "HAL/PlatformFileManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

#if PLATFORM_WINDOWS
#include "Windows/AllowWindowsPlatformTypes.h"
#include <Windows.h>
#include <AclAPI.h>
#include <sddl.h>
#include "Windows/HideWindowsPlatformTypes.h"
#endif

static constexpr int32 DefaultBionicsPort = 8090;
static constexpr int32 PortAutoIncrementMax = 10;

#if PLATFORM_WINDOWS
/**
 * Replace a file's DACL with one ACE that grants GENERIC_ALL to the current user only.
 * Also sets PROTECTED_DACL so the file stops inheriting parent ACEs (like BUILTIN\Users).
 * Net effect: same-user malware or other processes cannot read the bearer token
 * from .bionics-bridge/instance.json, defending against same-user token exfil.
 * No-op (returns false) on non-Windows — callers log a warning.
 */
static bool RestrictFileToCurrentUserDACL(const FString& FilePath)
{
	HANDLE hToken = nullptr;
	if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &hToken))
	{
		return false;
	}

	DWORD RequiredSize = 0;
	GetTokenInformation(hToken, TokenUser, nullptr, 0, &RequiredSize);
	if (RequiredSize == 0)
	{
		CloseHandle(hToken);
		return false;
	}

	TArray<uint8> Buffer;
	Buffer.SetNumUninitialized(static_cast<int32>(RequiredSize));
	PTOKEN_USER pTokenUser = reinterpret_cast<PTOKEN_USER>(Buffer.GetData());
	if (!GetTokenInformation(hToken, TokenUser, pTokenUser, RequiredSize, &RequiredSize))
	{
		CloseHandle(hToken);
		return false;
	}
	CloseHandle(hToken);

	// One explicit-access entry: current user, full file access, no inheritance.
	EXPLICIT_ACCESSW ea = {};
	ea.grfAccessPermissions = GENERIC_ALL;
	ea.grfAccessMode = SET_ACCESS;
	ea.grfInheritance = NO_INHERITANCE;
	ea.Trustee.TrusteeForm = TRUSTEE_IS_SID;
	ea.Trustee.TrusteeType = TRUSTEE_IS_USER;
	ea.Trustee.ptstrName = reinterpret_cast<LPWSTR>(pTokenUser->User.Sid);

	PACL pNewDACL = nullptr;
	if (SetEntriesInAclW(1, &ea, nullptr, &pNewDACL) != ERROR_SUCCESS)
	{
		return false;
	}

	// PROTECTED_DACL_SECURITY_INFORMATION strips inherited ACEs so BUILTIN\Users can't read.
	DWORD Result = SetNamedSecurityInfoW(
		const_cast<LPWSTR>(*FilePath),
		SE_FILE_OBJECT,
		DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
		nullptr, nullptr, pNewDACL, nullptr);

	if (pNewDACL) { LocalFree(pNewDACL); }
	return Result == ERROR_SUCCESS;
}
#endif  // PLATFORM_WINDOWS

/**
 * Generate a 256-bit bearer token by concatenating two GUIDs (no hyphens, hex lowercase).
 * Override via BIONICS_BRIDGE_TOKEN env var for CI/test determinism.
 */
static FString ResolveAuthToken()
{
	FString EnvToken = FPlatformMisc::GetEnvironmentVariable(TEXT("BIONICS_BRIDGE_TOKEN"));
	if (!EnvToken.IsEmpty())
	{
		return EnvToken;
	}
	// 32 hex chars × 2 = 64 hex chars = 256 bits of entropy (2 × 128-bit GUIDs).
	return FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower()
	     + FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower();
}

// Explicit ctor/dtor here (not in header) so TUniquePtr<FBionicsBridgeServer>
// sees the full type definition when it needs to call delete.
UBionicsBridgeSubsystem::UBionicsBridgeSubsystem() = default;
UBionicsBridgeSubsystem::~UBionicsBridgeSubsystem() = default;

void UBionicsBridgeSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
	Super::Initialize(Collection);

	// Skip headless/commandlet runs
	if (IsRunningCommandlet() || IsRunningDedicatedServer() || FApp::IsUnattended())
	{
		UE_LOG(LogBionicsBridge, Log, TEXT("Skipping bridge server (commandlet/headless)"));
		return;
	}

	Server = MakeUnique<FBionicsBridgeServer>();
	Server->SetAuthToken(ResolveAuthToken());  // Must be set BEFORE Start() — first request rejects otherwise.
	int32 DesiredPort = ResolveDesiredPort();

	// Try DesiredPort, DesiredPort+1, ..., up to PortAutoIncrementMax attempts
	for (int32 Offset = 0; Offset < PortAutoIncrementMax; ++Offset)
	{
		if (Server->Start(DesiredPort + Offset))
		{
			UE_LOG(LogBionicsBridge, Log,
			       TEXT("Bridge server started on http://127.0.0.1:%d/bridge"),
			       DesiredPort + Offset);
			WriteDiscoveryFile();
			return;
		}
	}

	UE_LOG(LogBionicsBridge, Error,
	       TEXT("Failed to bind any port in range %d-%d"),
	       DesiredPort, DesiredPort + PortAutoIncrementMax - 1);
}

void UBionicsBridgeSubsystem::Deinitialize()
{
	if (Server.IsValid())
	{
		Server->Stop();
		Server.Reset();
		ClearDiscoveryFile();
	}
	Super::Deinitialize();
}

bool UBionicsBridgeSubsystem::IsServerRunning() const
{
	return Server.IsValid() && Server->IsRunning();
}

int32 UBionicsBridgeSubsystem::GetServerPort() const
{
	return Server.IsValid() ? Server->GetPort() : 0;
}

bool UBionicsBridgeSubsystem::RestartServer()
{
	if (!Server.IsValid())
	{
		Server = MakeUnique<FBionicsBridgeServer>();
	}
	Server->Stop();
	Server->SetAuthToken(ResolveAuthToken());  // Rotate token on restart.
	int32 DesiredPort = ResolveDesiredPort();
	for (int32 Offset = 0; Offset < PortAutoIncrementMax; ++Offset)
	{
		if (Server->Start(DesiredPort + Offset))
		{
			WriteDiscoveryFile();
			return true;
		}
	}
	return false;
}

void UBionicsBridgeSubsystem::StopServer()
{
	if (Server.IsValid())
	{
		Server->Stop();
		ClearDiscoveryFile();
	}
}

int32 UBionicsBridgeSubsystem::ResolveDesiredPort() const
{
	FString EnvPort = FPlatformMisc::GetEnvironmentVariable(TEXT("BIONICS_BRIDGE_PORT"));
	if (!EnvPort.IsEmpty())
	{
		int32 Parsed = FCString::Atoi(*EnvPort);
		if (Parsed > 0 && Parsed <= 65535)
			return Parsed;
	}
	return DefaultBionicsPort;
}

void UBionicsBridgeSubsystem::WriteDiscoveryFile() const
{
	if (!Server.IsValid() || !Server->IsRunning()) return;

	FString ProjectDir = FPaths::ProjectDir();
	FString DiscoveryDir = FPaths::Combine(ProjectDir, TEXT(".bionics-bridge"));
	IPlatformFile& FileManager = FPlatformFileManager::Get().GetPlatformFile();
	if (!FileManager.DirectoryExists(*DiscoveryDir))
	{
		FileManager.CreateDirectory(*DiscoveryDir);
	}

	TSharedPtr<FJsonObject> Json = MakeShared<FJsonObject>();
	Json->SetStringField(TEXT("host"), TEXT("127.0.0.1"));
	Json->SetNumberField(TEXT("port"), Server->GetPort());
	Json->SetStringField(TEXT("path"), TEXT("/bridge"));
	Json->SetStringField(TEXT("url"),
	    FString::Printf(TEXT("http://127.0.0.1:%d/bridge"), Server->GetPort()));
	Json->SetStringField(TEXT("project"), FApp::GetProjectName());
	Json->SetNumberField(TEXT("pid"), FPlatformProcess::GetCurrentProcessId());
	Json->SetStringField(TEXT("token"), Server->GetAuthToken());

	FString Output;
	TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Output);
	FJsonSerializer::Serialize(Json.ToSharedRef(), Writer);

	FString DiscoveryPath = FPaths::Combine(DiscoveryDir, TEXT("instance.json"));
	if (!FFileHelper::SaveStringToFile(Output, *DiscoveryPath))
	{
		UE_LOG(LogBionicsBridge, Error, TEXT("Failed to write instance.json at %s"), *DiscoveryPath);
		return;
	}

#if PLATFORM_WINDOWS
	// Lock the DACL so only the current Windows user can read the bearer token.
	// Without this, same-user processes (malicious pip/npm subprocesses, rogue UE
	// plugins) could read the token file even with Bearer auth on the HTTP bridge.
	if (!RestrictFileToCurrentUserDACL(DiscoveryPath))
	{
		UE_LOG(LogBionicsBridge, Warning,
		       TEXT("Failed to restrict instance.json ACL to current user. "
		            "Token may be readable by other same-user processes. "
		            "See SECURITY.md for manual ACL hardening."));
	}
#else
	// Non-Windows: UE5's cross-platform FileManager doesn't expose chmod (0600 equivalent).
	// Document the caveat instead — users must place .bionics-bridge/ on a user-restricted path.
	UE_LOG(LogBionicsBridge, Log,
	       TEXT("Non-Windows platform — ensure .bionics-bridge/ is on a user-restricted path."));
#endif
}

void UBionicsBridgeSubsystem::ClearDiscoveryFile() const
{
	FString ProjectDir = FPaths::ProjectDir();
	FString DiscoveryPath = FPaths::Combine(
		ProjectDir, TEXT(".bionics-bridge"), TEXT("instance.json"));
	IPlatformFile& FileManager = FPlatformFileManager::Get().GetPlatformFile();
	if (FileManager.FileExists(*DiscoveryPath))
	{
		FileManager.DeleteFile(*DiscoveryPath);
	}
}
