# BionicsBridge — Native C++ Plugin for UE5

Native HTTP bridge plugin that exposes UE5 editor + runtime automation to
the Bionics CLI/MCP server over JSON-RPC 2.0 on localhost.

**Why this exists:** Python Remote Execution has an estimated ~100-400ms round-trip
latency and only works in the editor. This C++ plugin is architecturally expected to:

- deliver **~5-20ms** native round-trip latency (in-process loopback HTTP vs Python remote-exec) — benchmark pending
- **Works in packaged builds** (runtime module) and editor (editor module)
- **No Python dependency** — direct UE API calls on the game thread
- **Self-discovery** — writes `<ProjectDir>/.bionics-bridge/instance.json`

## Install

### 1. Copy the plugin to your UE5 project

```
cp -r plugins/BionicsBridge YourProject/Plugins/BionicsBridge
```

### 2. Edit `YourProject.uproject`

Add to the `"Plugins"` array:

```json
{ "Name": "BionicsBridge", "Enabled": true }
```

### 3. Regenerate project files + rebuild

- Windows: right-click `.uproject` → **Generate Visual Studio project files**
- Mac/Linux: `UnrealBuildTool -projectfiles -project=YourProject.uproject -game`

Then rebuild in your IDE (VS/Rider/Xcode).

### 4. Launch the editor — check the log

Look for:

```
LogBionicsBridge: Bridge server started on http://127.0.0.1:8090/bridge
LogBionicsBridge: Editor tools registered. Total tools: 27 (5 general + 13 animgraph + 5 eventgraph + 4 bpdoctor)
```

### 5. Verify from Bionics CLI

```bash
python cli.py run ue5_native_status
# or via curl:
curl http://127.0.0.1:8090/bridge
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `BIONICS_BRIDGE_PORT` | 8090 | Port to bind (auto-increments if taken) |

## Architecture

```
Bionics CLI / MCP
    ↓ HTTP POST /bridge (JSON-RPC 2.0)
BionicsBridgeSubsystem (UEngineSubsystem)
    ↓ AsyncTask(GameThread)
BionicsBridgeToolRegistry
    ↓
UBionicsBridgeToolBase subclasses
    ↓
UE5 API (native C++ calls)
```

## Built-in Tools

### Runtime module (works everywhere)

| Tool | Description |
|------|-------------|
| `get_actors` | List actors in the current world |
| `spawn_actor_runtime` | Spawn actor in current world (PIE/packaged) |
| `get_console_var` | Read a console variable |
| `set_console_var` | Set a console variable |
| `execute_console_command` | Run a console command |
| `get_project_info` | Project name, engine version, paths |

### Editor module (editor-only)

| Tool | Description |
|------|-------------|
| `compile_blueprint` | Compile a BP asset + return error/warning counts |
| `save_asset` | Save an asset to disk |
| `query_assets` | Search the Content Browser by class + path |
| `spawn_actor_editor` | Spawn actor in editor world w/ undo support |

## Extending: Add Your Own Tool

**1. Create header** `Source/BionicsBridge/Private/Tools/MyTool.h`:

```cpp
#pragma once
#include "BionicsBridgeToolBase.h"
#include "MyTool.generated.h"

UCLASS()
class UMyTool : public UBionicsBridgeToolBase {
    GENERATED_BODY()
public:
    virtual FString GetToolName() const override { return TEXT("my_tool"); }
    virtual FString GetToolDescription() const override { return TEXT("..."); }
    virtual FString GetCategory() const override { return TEXT("custom"); }
    virtual TSharedPtr<FJsonObject> GetInputSchema() const override;
    virtual bool Execute(const TSharedPtr<FJsonObject>& Args,
                         TSharedPtr<FJsonObject>& Out, FString& Err) override;
};
```

**2. Implement** in a `.cpp`:

```cpp
#include "Tools/MyTool.h"

TSharedPtr<FJsonObject> UMyTool::GetInputSchema() const {
    return MakeSchema({ {TEXT("name"), TEXT("string")} }, { TEXT("name") });
}

bool UMyTool::Execute(const TSharedPtr<FJsonObject>& Args,
                       TSharedPtr<FJsonObject>& Out, FString& Err) {
    FString Name = GetStringArg(Args, TEXT("name"));
    Out = MakeShared<FJsonObject>();
    Out->SetStringField(TEXT("hello"), Name);
    return true;
}
```

**3. Register** in `BionicsBridgeModule.cpp::StartupModule()`:

```cpp
Registry.RegisterToolClass<UMyTool>();
```

## Discovery File

When the plugin starts successfully, it writes:

```
YourProject/.bionics-bridge/instance.json
{
  "host": "127.0.0.1",
  "port": 8090,
  "url": "http://127.0.0.1:8090/bridge",
  "project": "YourProject",
  "pid": 12345
}
```

The Bionics CLI walks up from the cwd to find this file, so you can run
tools from any subdirectory of your UE5 project.

## Security

- **Localhost-only bind** — the HTTP server only accepts `127.0.0.1` connections
- **Bearer-token authentication** — `POST /bridge` requires `Authorization: Bearer <token>`; the token is generated per-instance and written to `<ProjectDir>/.bionics-bridge/instance.json`. An empty token disables auth (dev/test only — warned at startup). Designed for local-only use; do not expose externally.
- **CORS enabled** — for browser clients running on localhost
- **Game-thread marshaling** — all tool execution respects UE thread safety

## License

MIT — see repository root.
