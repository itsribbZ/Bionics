# UE5 Custom C++ HTTP/JSON-RPC Bridge Plugin
## Architecture Research for Bionics Integration

**Target UE Version:** 5.5 / 5.6 / 5.7
**Reference Implementation:** [softdaddy-o/soft-ue-cli](https://github.com/softdaddy-o/soft-ue-cli) — `SoftUEBridge` plugin v1.3.2
**Research Date:** 2026-04-05

---

## 1. Subsystem Choice: UGameInstanceSubsystem vs UEditorSubsystem vs UEngineSubsystem

### TL;DR — Use `UEngineSubsystem` (what SoftUEBridge does)

| Subsystem | Lifetime | Editor | PIE | Packaged Game | Dedicated Server | Commandlet |
|---|---|---|---|---|---|---|
| `UEngineSubsystem` | Engine startup → shutdown | Yes | Yes | Yes | Yes | Yes |
| `UEditorSubsystem` | Editor startup → shutdown | Yes | No | No | No | No |
| `UGameInstanceSubsystem` | GameInstance spawn → teardown | PIE only | Yes | Yes | Yes | No |
| `UWorldSubsystem` | World load → unload | Per-world | Per-world | Per-world | Per-world | No |
| `ULocalPlayerSubsystem` | Player controller lifetime | Yes | Yes | Yes | No | No |

### Why `UEngineSubsystem` Wins for an HTTP Bridge

1. **Starts earlier than GameInstance.** Engine is created before any world loads. Your HTTP server binds port at engine startup, not at "press play".
2. **Works in the editor without PIE.** `UGameInstanceSubsystem` only initializes during PIE or packaged game runs — meaning the bridge dies every time the user stops PIE. Editor-only bridges based on `UEditorSubsystem` die in packaged builds. `UEngineSubsystem` covers both.
3. **Single instance.** One server per editor process, not per world or per PIE session.
4. **Survives PIE start/stop.** The server stays alive across the entire editor session.

### The SoftUEBridge Decision (corrected from its own README)

The README says "UGameInstanceSubsystem" but the actual source uses `UEngineSubsystem`:

```cpp
UCLASS()
class SOFTUEBRIDGE_API USoftUEBridgeSubsystem : public UEngineSubsystem
```

This is deliberate. They skip non-interactive processes explicitly:

```cpp
if (IsRunningCommandlet() || IsRunningDedicatedServer() || FApp::IsUnattended())
{
    UE_LOG(LogSoftUEBridge, Log, TEXT("skipping server start (non-interactive)"));
    return;
}
```

### For Bionics

**Choose `UEngineSubsystem`.** You want the HTTP bridge available in:
- Editor (no PIE running) — for asset manipulation, content browser ops, blueprint edits
- PIE — for runtime actor queries and live debugging
- Packaged builds — for testing automation on cooked builds

---

## 2. FHttpServerModule — Official UE HTTP Server

### Module Dependencies
Add to `.Build.cs` `PrivateDependencyModuleNames`:
```csharp
"HTTP", "HTTPServer", "Sockets", "Networking", "Json", "JsonUtilities"
```

### Core API Surface

| Class / Method | Purpose |
|---|---|
| `FHttpServerModule::Get()` | Singleton access to module |
| `GetHttpRouter(uint32 Port)` | Returns `TSharedPtr<IHttpRouter>` for a port |
| `IHttpRouter::BindRoute(FHttpPath, EHttpServerRequestVerbs, FHttpRequestHandler)` | Register a route handler, returns `FHttpRouteHandle` |
| `IHttpRouter::UnbindRoute(FHttpRouteHandle)` | Remove a route |
| `StartAllListeners()` | Activate bound listeners |
| `StopAllListeners()` | Deactivate all listeners |
| `FHttpServerRequest` | Incoming request: `Verb`, `Headers`, `Body` (TArray<uint8>), `QueryParams`, `PathParams` |
| `FHttpServerResponse::Create(Body, ContentType)` | Factory for response |
| `FHttpResultCallback` | Delegate: call with `TUniquePtr<FHttpServerResponse>` to complete |

### Route Binding Pattern

```cpp
FHttpServerModule& Module = FHttpServerModule::Get();
TSharedPtr<IHttpRouter> Router = Module.GetHttpRouter(8888);
if (!Router.IsValid()) { /* port unavailable */ return false; }

FHttpRouteHandle Handle = Router->BindRoute(
    FHttpPath(TEXT("/bridge")),
    EHttpServerRequestVerbs::VERB_POST | EHttpServerRequestVerbs::VERB_GET | EHttpServerRequestVerbs::VERB_OPTIONS,
    FHttpRequestHandler::CreateRaw(this, &FMyServer::HandleRequest)
);

Module.StartAllListeners();
```

### Pre-Bind Port Check (SoftUEBridge trick)

`GetHttpRouter()` returns a valid pointer even if the port is already in use on Windows because UE sets `SO_REUSEADDR`. The workaround: test-bind a raw socket first without that flag:

```cpp
ISocketSubsystem* SocketSub = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
FSocket* TestSocket = SocketSub->CreateSocket(NAME_Stream, TEXT("PortCheck"), false);
TSharedRef<FInternetAddr> Addr = SocketSub->CreateInternetAddr();
bool bIsValid = false;
Addr->SetIp(*BindAddress, bIsValid);
Addr->SetPort(Port);
bool bBound = TestSocket->Bind(*Addr);
TestSocket->Close();
SocketSub->DestroySocket(TestSocket);
if (!bBound) { /* port in use */ }
```

### Threading Model

- Request callbacks fire on an **HTTP worker thread**, NOT the game thread
- UObject access, actor iteration, property setting — all require game thread
- Marshal with `AsyncTask(ENamedThreads::GameThread, [](){ ... })`
- The `FHttpResultCallback` can be invoked from any thread, so you call it from inside the async lambda

### The Correct Threading Pattern

```cpp
bool FBridgeServer::HandleRequest(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    // ... parse request body on HTTP thread (cheap, safe) ...
    FString Body = /* UTF8 decode Request.Body */;
    TOptional<FBridgeRequest> Parsed = FBridgeRequest::FromJsonString(Body);

    // Heavy work → game thread
    AsyncTask(ENamedThreads::GameThread, [this, Req = Parsed.GetValue(), OnComplete]()
    {
        FBridgeResponse Response = ProcessRequest(Req);  // touches UObjects
        SendResponse(OnComplete, Response);              // OnComplete can be called cross-thread
    });

    return true;  // return immediately; OnComplete fires later
}
```

---

## 3. JSON-RPC 2.0 in UE5

### JSON Parsing — FJsonSerializer + FJsonObject

```cpp
#include "Serialization/JsonSerializer.h"
#include "Dom/JsonObject.h"

TSharedPtr<FJsonObject> Root;
TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonString);
if (FJsonSerializer::Deserialize(Reader, Root) && Root.IsValid())
{
    FString Method;
    Root->TryGetStringField(TEXT("method"), Method);

    TSharedPtr<FJsonObject>* ParamsObj;
    if (Root->TryGetObjectField(TEXT("params"), ParamsObj)) { ... }
}
```

### JSON Writing

```cpp
TSharedPtr<FJsonObject> Out = MakeShareable(new FJsonObject);
Out->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
Out->SetStringField(TEXT("id"), RequestId);
Out->SetObjectField(TEXT("result"), ResultObject);

FString OutStr;
TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&OutStr);
FJsonSerializer::Serialize(Out.ToSharedRef(), Writer);
```

### Reflection-Based Dispatch (SoftUEBridge pattern)

Don't use raw UFUNCTION reflection for external commands — instead register tool classes that derive from a common `UObject` base:

```cpp
UCLASS(Abstract)
class UBridgeToolBase : public UObject
{
    virtual FString GetToolName() const PURE_VIRTUAL;
    virtual FBridgeToolResult Execute(
        const TSharedPtr<FJsonObject>& Arguments,
        const FBridgeToolContext& Context) PURE_VIRTUAL;
};
```

Then a singleton registry maps tool name → class → instance. The instances are `AddToRoot()`ed to prevent GC.

### JSON-RPC 2.0 Envelope

SoftUEBridge follows MCP (Model Context Protocol) over JSON-RPC 2.0:

```json
// Request
{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"query-level","arguments":{"limit":10}}}

// Success
{"jsonrpc":"2.0","id":"1","result":{"content":[{"type":"text","text":"..."}]}}

// Error
{"jsonrpc":"2.0","id":"1","error":{"code":-32601,"message":"Method not found"}}
```

Standard error codes: `-32700` parse, `-32600` invalid request, `-32601` method not found, `-32602` invalid params, `-32603` internal.

---

## 4. Editor-Only vs Runtime APIs

### What Needs `#if WITH_EDITOR`

Editor-only headers and modules that will not compile in packaged builds:

| API | Module | Header |
|---|---|---|
| Actor labels (`GetActorLabel`) | UnrealEd | `Engine/World.h` |
| Asset registry write ops | AssetTools | `AssetToolsModule.h` |
| Blueprint compile | KismetCompiler | `KismetCompilerModule.h` |
| Content Browser | ContentBrowser | `ContentBrowserModule.h` |
| PIE start/stop | UnrealEd | `Editor.h` |
| Viewport capture | UnrealEd | `LevelEditor.h` |
| Foliage instances | Foliage | `InstancedFoliageActor.h` |
| Landscape editing | LandscapeEditor | `LandscapeEdit.h` |
| StateTree editor ops | StateTreeEditorModule | (editor-only) |
| Widget Blueprint editing | UMGEditor | `WidgetBlueprint.h` |

### The Two-Module Pattern (what SoftUEBridge uses)

Split the plugin into two modules:
- **Runtime module** (`Type: "Runtime"`, loads in all builds) — HTTP server, query-level, call-function, set/get-property, spawn-actor, trigger-input, console vars, logs
- **Editor module** (`Type: "Editor"`, loads only in editor) — asset CRUD, blueprint graph editing, material editing, PIE control, capture, StateTree, widgets, build/relaunch

The runtime module registers its tools at `StartupModule()`. The editor module registers additional tools at `StartupModule()` into the same singleton registry. Both write into the same HTTP server.

### Can the HTTP Bridge Work in Packaged Builds?

**Yes, partially.** The runtime module works in packaged builds because `HTTPServer` is a Runtime module. You get:
- Actor queries, spawn/destroy, property get/set
- Console variable control
- Log capture
- Input simulation
- Function dispatch on UObjects

You do NOT get (packaged):
- Any asset creation/edit
- Blueprint compilation
- Content browser ops
- PIE control (no PIE concept in shipping)
- UE Insights capture
- Live Coding trigger

---

## 5. UE Automation APIs Worth Exposing

| Category | Module | API | Tool Names (SoftUEBridge for reference) |
|---|---|---|---|
| Asset registry queries | AssetRegistry | `IAssetRegistry::Get().GetAssetsByPath()` | query-asset, find-references |
| Asset CRUD (editor) | AssetTools | `IAssetTools::Get().CreateAsset()` / `DeleteAssets()` | create-asset, delete-asset, save-asset |
| Blueprint compile (editor) | KismetCompiler | `FKismetEditorUtilities::CompileBlueprint()` | compile-blueprint |
| Blueprint graph (editor) | BlueprintGraph | `UEdGraph::AddNode()`, `UEdGraphSchema::TryCreateConnection()` | add-graph-node, connect-graph-pins |
| Actor spawn/modify/delete | Engine | `World->SpawnActor<T>()`, `Actor->Destroy()` | spawn-actor, set-property |
| PIE control (editor) | UnrealEd | `GEditor->RequestPlaySession()`, `GEditor->RequestEndPlayMap()` | pie-session |
| Viewport capture (editor) | UnrealEd + ImageWrapper | `FViewport::ReadPixels()` + `IImageWrapper::SetRaw()` | capture-screenshot |
| Content browser (editor) | ContentBrowser | `FContentBrowserModule::Get().GetContentBrowser().SyncBrowserToAssets()` | open-asset |
| Material params | Engine | `UMaterialInstanceDynamic::SetScalarParameterValue()` | query-material, query-mpc |
| StateTree editing (editor) | StateTreeEditorModule | StateTree API (editor only) | add-statetree-state, add-statetree-transition |
| Widget blueprint (editor) | UMGEditor | `UWidgetBlueprint` tree inspection | inspect-widget-blueprint, add-widget |
| UE Insights trace | Trace | `FTraceAuxiliary::Start()` / `Stop()` | insights-capture, insights-analyze |
| Console vars | Engine | `IConsoleManager::Get().FindConsoleVariable()` | get-console-var, set-console-var |
| Python exec (editor) | PythonScriptPlugin | `IPythonScriptPlugin::Get()->ExecPythonCommandEx()` | run-python-script |
| Live Coding (editor) | LiveCoding | `ILiveCodingModule::Compile()` | trigger-live-coding |
| Log capture | Engine | `FOutputDevice` override | get-logs |
| Input simulation | InputCore | `FSlateApplication::ProcessKeyDownEvent()` | trigger-input |
| DataTable rows (editor) | Engine | `UDataTable::AddRow()` | add-datatable-row |

---

## 6. Python Remote Execution + Remote Control API vs Custom C++ Bridge

### What Stock UE Tools Ship With

**Python Remote Execution (port 9998)**
- Plugin: `PythonScriptPlugin`
- Protocol: Custom UDP multicast + TCP command channel
- Editor only — disabled in packaged builds
- Sends Python code strings for execution
- What Bionics already uses (`ue5_modules/remote_execution.py`)

**Remote Control API (port 30010)**
- Plugin: `RemoteControl` + `RemoteControlAPI` + `WebRemoteControl`
- Protocol: HTTP + WebSocket
- Editor and runtime (if enabled in cooked builds)
- Exposes preset-based property access + arbitrary function calls on registered objects
- Feature set: object create/find/describe, property get/set, function call, preset binding, asset search

### What Custom C++ Plugin Can Do That Stock Cannot

| Capability | Python RE | Remote Control | Custom C++ |
|---|---|---|---|
| Structured tool schemas (JSON schema per tool) | No | No | Yes |
| MCP / JSON-RPC 2.0 protocol compliance | No | No | Yes |
| Works in packaged builds without cooking steps | No | Limited | Yes |
| Zero-overhead dispatch (no Python interpreter) | No | Yes | Yes |
| Startup latency < 50ms | No (300ms+) | No (slower than direct) | Yes |
| Access to editor-only modules via explicit #if WITH_EDITOR split | Yes | Partial | Yes |
| Custom validation, rate limiting, auth at framework level | No | Limited | Yes |
| Binary payloads (screenshots, thumbnails) direct in response | No | Awkward | Yes |
| Deterministic, typed error codes | No (stringly-typed) | Limited | Yes |
| StateTree editing | No direct API | No | Yes |
| Blueprint graph node manipulation | Via Python reflection (slow, brittle) | No | Yes |
| UE Insights start/stop/analyze | Via Python | No | Yes |
| Unattended mode suppression (prevents modal dialog hangs) | No | No | Yes (`GIsRunningUnattendedScript`) |

### Where Stock Is Better

- **Python RE**: great for one-off scripts, no plugin rebuild needed, massive existing ecosystem of UE Python samples
- **Remote Control**: works out of the box, officially supported, good for OSC/MIDI/TouchOSC integrations where you just need knobs on properties

### Concrete Examples Where Custom Wins

1. **"Get me every actor in the current level with a transform and component list as JSON in under 20ms."**
   Python RE: ~400ms cold, ~150ms warm, returns stringified Python list you must re-parse. Custom C++: ~8ms, direct FJsonObject.

2. **"Insert a Sequence node between two existing nodes in BP_Player event graph and rewire pins."**
   Python RE: possible but brittle (reflection traversal). Remote Control: not supported. Custom C++: direct `UEdGraphSchema` calls.

3. **"Capture the active viewport as PNG, base64 it, return in the same HTTP response."**
   Python RE: would need to write to disk then read back. Remote Control: not supported. Custom C++: `FViewport::ReadPixels` → `IImageWrapper::GetCompressed` → base64 → JSON in one response.

4. **"Suppress modal dialogs during a batch 'Overwrite Existing Object' operation."**
   Python RE: no mechanism. Remote Control: no. Custom C++: `FUnattendedScriptGuard` RAII.

5. **"Pre-validate port, fall back to ports 8080-8089, write discovery file for external CLI."**
   Python RE: N/A (it picks its own port). Remote Control: fixed 30010. Custom C++: full control.

### Decision for Bionics

Bionics already has `ue5_modules/remote_execution.py` (the Python RE client) and uses it for animation Blueprint wiring. That is appropriate for scripted one-off macros.

**Build the custom C++ plugin when you need:**
- Sub-20ms command latency (Watch Mode feedback loops)
- Editor-only features (asset edits, BP graph manipulation)
- Stable, typed tool schemas for Claude's tool use
- Packaged-build testing (automation on cooked projects)
- Dialog suppression during batch operations

**Keep Python RE when you need:**
- Quick script prototypes, rapid iteration without engine rebuild
- Access to the entire `unreal.*` Python API surface (which the custom bridge would have to re-expose tool by tool)

**Recommended hybrid:** The custom bridge exposes a `run-python-script` tool (which SoftUEBridge does) — C++ bridge handles the HTTP/JSON-RPC envelope, tool discovery, validation, routing; falls through to Python RE for unscripted-but-needed APIs.

---

## 7. Plugin Distribution

### Directory Layout

```
YourProject/Plugins/
  BionicsBridge/
    BionicsBridge.uplugin
    Source/
      BionicsBridge/                 (runtime module)
        BionicsBridge.Build.cs
        Public/
          BionicsBridgeModule.h
          Server/
            BridgeServer.h
          Subsystem/
            BridgeSubsystem.h
          Tools/
            BridgeToolBase.h
            BridgeToolRegistry.h
        Private/
          BionicsBridgeModule.cpp
          Server/
            BridgeServer.cpp
          Subsystem/
            BridgeSubsystem.cpp
          Tools/
            QueryLevelTool.h/.cpp
            SpawnActorTool.h/.cpp
            ... etc
      BionicsBridgeEditor/            (editor-only module)
        BionicsBridgeEditor.Build.cs
        Public/
          BionicsBridgeEditorModule.h
        Private/
          BionicsBridgeEditorModule.cpp
          Tools/
            Asset/CreateAssetTool.cpp
            Blueprint/CompileBlueprintTool.cpp
            ...
```

### .uplugin File

```json
{
    "FileVersion": 3,
    "Version": 1,
    "VersionName": "0.1.0",
    "FriendlyName": "Bionics Bridge",
    "Description": "HTTP/JSON-RPC bridge for external automation tools.",
    "Category": "Scripting",
    "CreatedBy": "Jacob Ribbe",
    "CanContainContent": false,
    "EnabledByDefault": false,
    "Plugins": [
        { "Name": "EditorScriptingUtilities", "Enabled": true },
        { "Name": "PythonScriptPlugin", "Enabled": true, "Optional": true }
    ],
    "Modules": [
        {
            "Name": "BionicsBridge",
            "Type": "Runtime",
            "LoadingPhase": "Default"
        },
        {
            "Name": "BionicsBridgeEditor",
            "Type": "Editor",
            "LoadingPhase": "PostEngineInit"
        }
    ]
}
```

Key fields:
- `"Type": "Runtime"` — module loads in editor, PIE, and packaged
- `"Type": "Editor"` — editor only, skipped when cooking
- `"LoadingPhase": "PostEngineInit"` — editor module loads after engine subsystems are ready (needed because editor UI extension requires `GEditor`)
- `"EnabledByDefault": false` — user opts in per-project

### Runtime Module .Build.cs

```csharp
using UnrealBuildTool;

public class BionicsBridge : ModuleRules
{
    public BionicsBridge(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        // Allow editor module to inherit from runtime tool classes
        PublicIncludePaths.Add(System.IO.Path.Combine(ModuleDirectory, "Private"));

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core", "CoreUObject", "Engine"
        });

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "HTTP", "HTTPServer", "Sockets", "Networking",
            "Json", "JsonUtilities",
            "InputCore", "Projects",
            "ImageWrapper", "RenderCore", "RHI"
        });

        if (Target.bBuildEditor)
        {
            PrivateDependencyModuleNames.Add("UnrealEd");
        }
    }
}
```

### Install Flow

1. Drop plugin folder into `MyProject/Plugins/`
2. Add to `.uproject`:
   ```json
   "Plugins": [{ "Name": "BionicsBridge", "Enabled": true }]
   ```
3. Delete `Intermediate/` and `Binaries/` (force rebuild)
4. Right-click `.uproject` → Generate Visual Studio project files
5. Build from IDE or launch UE with `-waitmutex` → triggers rebuild
6. On editor start, check Output Log for `LogBionicsBridge: Bridge server started on http://127.0.0.1:8888/bridge`

### Optional: Environment-Gated Compilation

In `MyProject.Target.cs`:
```csharp
if (Environment.GetEnvironmentVariable("BIONICS_BRIDGE") == "1")
{
    ExtraModuleNames.Add("BionicsBridge");
}
```

Developers not doing automation work skip the module entirely.

---

## 8. Security

### Localhost-Only Binding (mandatory)

```cpp
Server->Start(8888, TEXT("127.0.0.1"));  // NEVER 0.0.0.0 by default
```

The HTTP server binds to `127.0.0.1` only. External network access requires an explicit opt-in flag. Epic explicitly warns against internet exposure for Remote Control API — same reasoning applies here.

### Authentication Token

Add a shared-secret header check in the request handler before dispatch:

```cpp
const TArray<FString>* AuthHeaders = Request.Headers.Find(TEXT("X-Bionics-Token"));
if (!AuthHeaders || AuthHeaders->Num() == 0 ||
    !FCString::Strcmp(*(*AuthHeaders)[0], *ExpectedToken) == 0)
{
    SendError(OnComplete, 401, EBridgeErrorCode::Unauthorized, TEXT("Bad token"));
    return true;
}
```

Store the token in `~/.bionics/bridge-token` (chmod 600), regenerate per editor session, write to `.bionics-bridge/instance.json` next to port. External CLI reads both at startup.

### Request Validation

- Validate JSON-RPC envelope before dispatch (jsonrpc=="2.0", id present, method is string)
- Validate tool name against registry — never trust caller
- Validate argument types against tool's declared `GetInputSchema()` before calling `Execute()`
- Enforce `GetRequiredParams()` — reject missing required fields with `-32602` InvalidParams
- Reject bodies over 1 MB (configurable) — prevent OOM

### Rate Limiting

Simple token bucket per client IP (localhost is one client in normal usage):

```cpp
class FRateLimiter
{
    double LastRefill;
    int32 Tokens = 100;
    int32 MaxTokens = 100;
    double RefillRate = 10.0;  // tokens per second
    FCriticalSection Lock;

    bool TryConsume()
    {
        FScopeLock L(&Lock);
        double Now = FPlatformTime::Seconds();
        Tokens = FMath::Min<int32>(MaxTokens, Tokens + (int32)((Now - LastRefill) * RefillRate));
        LastRefill = Now;
        if (Tokens <= 0) return false;
        --Tokens;
        return true;
    }
};
```

Reject over-quota with HTTP 429.

### Dialog Suppression (SoftUEBridge pattern)

UE's modal dialogs (asset overwrite, delete confirm, compile error popups) freeze the game thread and hang HTTP requests. Suppress with RAII guard:

```cpp
struct FUnattendedScriptGuard
{
    bool bPrev;
    FUnattendedScriptGuard() : bPrev(GIsRunningUnattendedScript) { GIsRunningUnattendedScript = true; }
    ~FUnattendedScriptGuard() { GIsRunningUnattendedScript = bPrev; }
};

// In HandleToolsCall:
FUnattendedScriptGuard Guard;
Result = Registry.ExecuteTool(...);
```

---

## 9. Minimal Complete Plugin — get_all_actors

Below is the smallest viable plugin (5 files) that:
1. Loads as `UEngineSubsystem` — works editor, PIE, packaged
2. Starts HTTP server on port 8888
3. Exposes one endpoint `POST /bridge` with JSON-RPC tool `get_all_actors`
4. Marshals to game thread correctly
5. Returns JSON array of actors in current world

### BionicsBridge.uplugin

```json
{
    "FileVersion": 3,
    "Version": 1,
    "VersionName": "0.1.0",
    "FriendlyName": "Bionics Bridge",
    "Description": "Minimal HTTP/JSON-RPC bridge exposing UE automation to Bionics.",
    "Category": "Scripting",
    "CreatedBy": "Jacob Ribbe",
    "CanContainContent": false,
    "EnabledByDefault": false,
    "Modules": [
        {
            "Name": "BionicsBridge",
            "Type": "Runtime",
            "LoadingPhase": "Default"
        }
    ]
}
```

### Source/BionicsBridge/BionicsBridge.Build.cs

```csharp
using UnrealBuildTool;

public class BionicsBridge : ModuleRules
{
    public BionicsBridge(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core", "CoreUObject", "Engine"
        });

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "HTTP", "HTTPServer", "Sockets", "Networking",
            "Json", "JsonUtilities"
        });
    }
}
```

### Source/BionicsBridge/Public/BionicsBridgeModule.h

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"

DECLARE_LOG_CATEGORY_EXTERN(LogBionicsBridge, Log, All);

class FBionicsBridgeModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;
};
```

### Source/BionicsBridge/Public/BionicsBridgeSubsystem.h

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Subsystems/EngineSubsystem.h"
#include "HttpServerModule.h"
#include "IHttpRouter.h"
#include "HttpRouteHandle.h"
#include "HttpServerRequest.h"
#include "HttpServerResponse.h"
#include "BionicsBridgeSubsystem.generated.h"

UCLASS()
class BIONICSBRIDGE_API UBionicsBridgeSubsystem : public UEngineSubsystem
{
    GENERATED_BODY()

public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;

private:
    TSharedPtr<IHttpRouter> HttpRouter;
    FHttpRouteHandle RouteHandle;
    int32 ServerPort = 8888;
    bool bIsRunning = false;

    bool StartServer();
    void StopServer();

    bool HandleRequest(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete);
    void DispatchOnGameThread(const FString& RequestId, const FString& Method,
                              const TSharedPtr<FJsonObject>& Params,
                              const FHttpResultCallback& OnComplete);

    TSharedPtr<FJsonObject> Tool_GetAllActors(const TSharedPtr<FJsonObject>& Args);

    void SendJson(const FHttpResultCallback& OnComplete, const TSharedRef<FJsonObject>& Body, int32 HttpCode = 200);
    void SendError(const FHttpResultCallback& OnComplete, const FString& Id, int32 RpcCode, const FString& Message, int32 HttpCode = 400);
};
```

### Source/BionicsBridge/Private/BionicsBridgeModule.cpp

```cpp
#include "BionicsBridgeModule.h"
#include "Modules/ModuleManager.h"

DEFINE_LOG_CATEGORY(LogBionicsBridge);

void FBionicsBridgeModule::StartupModule()
{
    UE_LOG(LogBionicsBridge, Log, TEXT("BionicsBridge module started"));
}

void FBionicsBridgeModule::ShutdownModule()
{
    UE_LOG(LogBionicsBridge, Log, TEXT("BionicsBridge module shutdown"));
}

IMPLEMENT_MODULE(FBionicsBridgeModule, BionicsBridge)
```

### Source/BionicsBridge/Private/BionicsBridgeSubsystem.cpp

```cpp
#include "BionicsBridgeSubsystem.h"
#include "BionicsBridgeModule.h"
#include "HttpPath.h"
#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Engine/World.h"
#include "Engine/Engine.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"

void UBionicsBridgeSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);

    if (IsRunningCommandlet() || IsRunningDedicatedServer() || FApp::IsUnattended())
    {
        return;
    }

    StartServer();
}

void UBionicsBridgeSubsystem::Deinitialize()
{
    StopServer();
    Super::Deinitialize();
}

bool UBionicsBridgeSubsystem::StartServer()
{
    if (bIsRunning) return true;

    FHttpServerModule& HttpModule = FHttpServerModule::Get();
    HttpRouter = HttpModule.GetHttpRouter(ServerPort);
    if (!HttpRouter.IsValid())
    {
        UE_LOG(LogBionicsBridge, Error, TEXT("Could not acquire HTTP router on port %d"), ServerPort);
        return false;
    }

    RouteHandle = HttpRouter->BindRoute(
        FHttpPath(TEXT("/bridge")),
        EHttpServerRequestVerbs::VERB_POST | EHttpServerRequestVerbs::VERB_GET,
        FHttpRequestHandler::CreateUObject(this, &UBionicsBridgeSubsystem::HandleRequest));

    if (!RouteHandle.IsValid())
    {
        UE_LOG(LogBionicsBridge, Error, TEXT("Failed to bind /bridge route"));
        return false;
    }

    HttpModule.StartAllListeners();
    bIsRunning = true;

    UE_LOG(LogBionicsBridge, Log, TEXT("Bridge server started on http://127.0.0.1:%d/bridge"), ServerPort);
    return true;
}

void UBionicsBridgeSubsystem::StopServer()
{
    if (!bIsRunning) return;
    if (HttpRouter.IsValid() && RouteHandle.IsValid())
    {
        HttpRouter->UnbindRoute(RouteHandle);
    }
    bIsRunning = false;
    UE_LOG(LogBionicsBridge, Log, TEXT("Bridge server stopped"));
}

bool UBionicsBridgeSubsystem::HandleRequest(const FHttpServerRequest& Request, const FHttpResultCallback& OnComplete)
{
    // Simple health check via GET
    if (Request.Verb == EHttpServerRequestVerbs::VERB_GET)
    {
        TSharedRef<FJsonObject> Health = MakeShared<FJsonObject>();
        Health->SetBoolField(TEXT("running"), true);
        Health->SetNumberField(TEXT("port"), ServerPort);
        SendJson(OnComplete, Health, 200);
        return true;
    }

    // Parse body (HTTP worker thread — safe, no UObjects)
    FString Body;
    if (Request.Body.Num() > 0)
    {
        FUTF8ToTCHAR Convert(reinterpret_cast<const ANSICHAR*>(Request.Body.GetData()), Request.Body.Num());
        Body = FString(Convert.Length(), Convert.Get());
    }

    TSharedPtr<FJsonObject> Root;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Body);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        SendError(OnComplete, TEXT(""), -32700, TEXT("Parse error"), 400);
        return true;
    }

    FString RequestId, Method;
    Root->TryGetStringField(TEXT("id"), RequestId);
    if (!Root->TryGetStringField(TEXT("method"), Method))
    {
        SendError(OnComplete, RequestId, -32600, TEXT("Missing method"), 400);
        return true;
    }

    const TSharedPtr<FJsonObject>* ParamsPtr = nullptr;
    Root->TryGetObjectField(TEXT("params"), ParamsPtr);
    TSharedPtr<FJsonObject> Params = ParamsPtr ? *ParamsPtr : MakeShared<FJsonObject>();

    // Marshal to game thread for UObject access
    DispatchOnGameThread(RequestId, Method, Params, OnComplete);
    return true;
}

void UBionicsBridgeSubsystem::DispatchOnGameThread(const FString& RequestId, const FString& Method,
                                                    const TSharedPtr<FJsonObject>& Params,
                                                    const FHttpResultCallback& OnComplete)
{
    TWeakObjectPtr<UBionicsBridgeSubsystem> WeakThis(this);

    AsyncTask(ENamedThreads::GameThread, [WeakThis, RequestId, Method, Params, OnComplete]()
    {
        UBionicsBridgeSubsystem* Self = WeakThis.Get();
        if (!Self) return;

        TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
        Envelope->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
        Envelope->SetStringField(TEXT("id"), RequestId);

        if (Method == TEXT("get_all_actors"))
        {
            TSharedPtr<FJsonObject> Result = Self->Tool_GetAllActors(Params);
            Envelope->SetObjectField(TEXT("result"), Result);
        }
        else
        {
            TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
            Err->SetNumberField(TEXT("code"), -32601);
            Err->SetStringField(TEXT("message"), FString::Printf(TEXT("Method not found: %s"), *Method));
            Envelope->SetObjectField(TEXT("error"), Err);
        }

        Self->SendJson(OnComplete, Envelope, 200);
    });
}

TSharedPtr<FJsonObject> UBionicsBridgeSubsystem::Tool_GetAllActors(const TSharedPtr<FJsonObject>& Args)
{
    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
    TArray<TSharedPtr<FJsonValue>> ActorsArr;

    // Find first valid world (editor world, PIE world, or game world)
    UWorld* World = nullptr;
    if (GEngine)
    {
        for (const FWorldContext& Ctx : GEngine->GetWorldContexts())
        {
            if (Ctx.WorldType == EWorldType::PIE || Ctx.WorldType == EWorldType::Game)
            {
                World = Ctx.World();
                if (World) break;
            }
        }
        if (!World)
        {
            for (const FWorldContext& Ctx : GEngine->GetWorldContexts())
            {
                if (Ctx.WorldType == EWorldType::Editor)
                {
                    World = Ctx.World();
                    if (World) break;
                }
            }
        }
    }

    if (!World)
    {
        Result->SetStringField(TEXT("error"), TEXT("No world available"));
        Result->SetArrayField(TEXT("actors"), ActorsArr);
        return Result;
    }

    for (TActorIterator<AActor> It(World); It; ++It)
    {
        AActor* Actor = *It;
        if (!Actor) continue;

        TSharedPtr<FJsonObject> A = MakeShared<FJsonObject>();
        A->SetStringField(TEXT("name"), Actor->GetName());
        A->SetStringField(TEXT("class"), Actor->GetClass()->GetName());

        const FVector Loc = Actor->GetActorLocation();
        TSharedPtr<FJsonObject> LocJson = MakeShared<FJsonObject>();
        LocJson->SetNumberField(TEXT("x"), Loc.X);
        LocJson->SetNumberField(TEXT("y"), Loc.Y);
        LocJson->SetNumberField(TEXT("z"), Loc.Z);
        A->SetObjectField(TEXT("location"), LocJson);

        ActorsArr.Add(MakeShared<FJsonValueObject>(A));
    }

    Result->SetStringField(TEXT("world"), World->GetName());
    Result->SetNumberField(TEXT("count"), ActorsArr.Num());
    Result->SetArrayField(TEXT("actors"), ActorsArr);
    return Result;
}

void UBionicsBridgeSubsystem::SendJson(const FHttpResultCallback& OnComplete, const TSharedRef<FJsonObject>& Body, int32 HttpCode)
{
    FString Str;
    TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Str);
    FJsonSerializer::Serialize(Body, Writer);

    TUniquePtr<FHttpServerResponse> Resp = FHttpServerResponse::Create(Str, TEXT("application/json"));
    Resp->Code = static_cast<EHttpServerResponseCodes>(HttpCode);
    Resp->Headers.Add(TEXT("Access-Control-Allow-Origin"), {TEXT("*")});
    OnComplete(MoveTemp(Resp));
}

void UBionicsBridgeSubsystem::SendError(const FHttpResultCallback& OnComplete, const FString& Id, int32 RpcCode, const FString& Message, int32 HttpCode)
{
    TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
    Envelope->SetStringField(TEXT("jsonrpc"), TEXT("2.0"));
    Envelope->SetStringField(TEXT("id"), Id);
    TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
    Err->SetNumberField(TEXT("code"), RpcCode);
    Err->SetStringField(TEXT("message"), Message);
    Envelope->SetObjectField(TEXT("error"), Err);
    SendJson(OnComplete, Envelope, HttpCode);
}
```

### Verifying It Works

After building and launching UE, from Bionics Python:

```python
import httpx
r = httpx.post("http://127.0.0.1:8888/bridge", json={
    "jsonrpc": "2.0", "id": "1", "method": "get_all_actors"
})
print(r.json())
# {"jsonrpc":"2.0","id":"1","result":{"world":"Untitled_1","count":47,"actors":[...]}}
```

---

## 10. Detailed Comparison Matrix: Stock vs Custom

| Dimension | Python Remote Execution | Remote Control HTTP (port 30010) | Custom C++ Bridge |
|---|---|---|---|
| **Protocol** | Custom UDP multicast + TCP | HTTP + WebSocket | HTTP + JSON-RPC 2.0 |
| **Port** | 9998 (UDP discovery) + ephemeral TCP | 30010 (configurable) | Fully configurable |
| **Transport latency** | 100-400ms (multicast + Python dispatch) | 30-80ms | 5-20ms |
| **Payload size limit** | Limited by Python string buffering | HTTP default (~1MB) | Configurable |
| **Request throughput** | ~10 req/sec (Python interpreter bottleneck) | ~50 req/sec | 200+ req/sec |
| **Editor support** | Yes (required) | Yes | Yes |
| **PIE support** | Limited (separate Python context) | Yes | Yes |
| **Packaged build support** | **No** | Yes (with plugin cooked) | Yes |
| **Dedicated server support** | No | Limited | Yes |
| **API coverage** | All of `unreal.*` Python bindings | Public reflected UFUNCTIONs + properties | Whatever you expose |
| **Type safety** | Stringly-typed Python | JSON schema at runtime | Compile-time C++ + JSON schema |
| **Tool discovery** | No (send arbitrary code) | Via preset objects | Structured via registry |
| **Error codes** | Python exception strings | HTTP status + message | JSON-RPC standard + custom |
| **Binary data (images)** | Write-to-disk workaround | Base64 awkward | Direct base64 in JSON |
| **Modal dialog handling** | Locks up | Locks up | RAII suppression |
| **Authentication** | None (local only) | Token-based | Whatever you build |
| **Custom rate limiting** | No | Limited | Yes |
| **Plugin install complexity** | Zero (ships with UE) | Enable plugin + restart | Build custom plugin |
| **Build/rebuild cost** | None | Minimal | Each change needs C++ rebuild |
| **Learning curve** | Low (Python) | Medium (preset setup) | High (UE C++ + modules) |
| **Observability** | Python logs only | UE log category | Full control |

### Bottom-Line Recommendations

**Use Python Remote Execution when:**
- Iterating fast on one-off scripts
- Don't need packaged-build automation
- OK with 100-400ms round-trips

**Use Remote Control when:**
- You want property knobs for external control surfaces
- You can enumerate your controlled objects into presets up front
- Don't need custom command dispatch

**Build a Custom C++ Bridge when:**
- Sub-50ms latency matters (interactive tools, Watch Mode)
- You need structured tool schemas for LLM tool-use
- You need packaged-build automation coverage
- You want dialog suppression, rate limiting, auth
- You plan 20+ custom tools (upfront cost amortizes)
- You're integrating with Claude Code / MCP where typed schemas are first-class

For **Bionics specifically:** the custom C++ bridge is justified because:
1. Bionics already targets LLM tool-use (typed schemas matter for Claude)
2. Watch Mode requires low-latency feedback loops (custom bridge: 5-20ms)
3. You're building a 30+ tool surface long-term (amortize cost)
4. You already have Python RE in `ue5_modules/` as a fallback for edge cases
5. Packaged-build testing will matter once Sworder:721 cooks

**Migration path:**
- Phase 1: Keep Python RE for all current AnimBP wiring
- Phase 2: Build minimal custom bridge (this doc's minimal example) for `get_all_actors`, `spawn_actor`, `set_property`, `get_property` — the hot path tools
- Phase 3: Add editor module with `compile_blueprint`, `create_asset`, `capture_screenshot`
- Phase 4: Deprecate Python RE paths one by one as custom tools replace them; keep `run_python_script` tool as the escape hatch

---

## Key Source References

- SoftUEBridge plugin: https://github.com/softdaddy-o/soft-ue-cli (path: `soft_ue_cli/plugin_data/SoftUEBridge/`)
- UE5 Subsystems doc: https://dev.epicgames.com/documentation/en-us/unreal-engine/programming-subsystems-in-unreal-engine
- FHttpServerModule API: https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Runtime/HttpServer/FHttpServerModule
- Remote Control API: https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-api-http-reference-for-unreal-engine
- HTTP Router port binding forum thread: https://forums.unrealengine.com/t/how-to-get-unused-http-router/250567
- UE Simple HTTP Server reference: https://github.com/Kaboms/UE-Simple-Http-Server
