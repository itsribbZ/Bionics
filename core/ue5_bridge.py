"""Bionics UE5 Bridge - Remote Control API + Python Script Execution.

Provides programmatic access to UE5 Editor operations via:
1. Web Remote Control Plugin (HTTP REST API on localhost:30010)
2. Python Remote Execution via UE5's remote_execution.py protocol
   (UDP multicast on 239.0.0.1:6766 for discovery, TCP for commands)

Setup required in UE5 Editor:
  - Enable plugin: "Web Remote Control" (for HTTP API)
  - Enable plugin: "Python Editor Script Plugin" (for Python execution)
  - Enable "Remote Execution" in Python plugin settings

For Blueprint/AnimGraph pin connections: ONLY Python execution works.
The HTTP API cannot modify graph topology (nodes, pins, connections).

Graceful degradation: if UE5 isn't running or plugins aren't enabled,
the bridge reports unavailable and the agent falls back to vision+click.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

import requests

logger = logging.getLogger("bionics.ue5_bridge")

DEFAULT_RC_HOST = "127.0.0.1"
DEFAULT_RC_PORT = 30010
DEFAULT_PYTHON_PORT = 9998
RC_TIMEOUT = 5.0


class ConnectionStatus(Enum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    PLUGIN_MISSING = auto()
    EDITOR_NOT_RUNNING = auto()


@dataclass
class UE5Response:
    """Response from a UE5 API call."""
    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""
    status_code: int = 0


class UE5Bridge:
    """Bridge to Unreal Engine 5 Editor via Remote Control and Python Execution."""

    def __init__(
        self,
        rc_host: str = DEFAULT_RC_HOST,
        rc_port: int = DEFAULT_RC_PORT,
        python_port: int = DEFAULT_PYTHON_PORT,
    ):
        self._rc_base = f"http://{rc_host}:{rc_port}"
        self._rc_host = rc_host
        self._rc_port = rc_port
        self._python_port = python_port
        self._status = ConnectionStatus.DISCONNECTED
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._last_check: float = 0

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def check_connection(self) -> ConnectionStatus:
        """Check if UE5 Editor is running with Remote Control enabled."""
        try:
            resp = self._session.get(f"{self._rc_base}/remote/info", timeout=RC_TIMEOUT)
            if resp.status_code == 200:
                self._status = ConnectionStatus.CONNECTED
                info = resp.json()
                logger.info(f"UE5 connected: {info}")
            else:
                self._status = ConnectionStatus.PLUGIN_MISSING
                logger.warning(f"UE5 responded but RC may be misconfigured: {resp.status_code}")
        except requests.ConnectionError:
            self._status = ConnectionStatus.EDITOR_NOT_RUNNING
            logger.info("UE5 Editor not reachable (not running or RC plugin disabled)")
        except requests.Timeout:
            self._status = ConnectionStatus.EDITOR_NOT_RUNNING
            logger.info("UE5 connection timed out")
        except Exception as e:
            self._status = ConnectionStatus.DISCONNECTED
            logger.error(f"UE5 connection check failed: {e}")

        self._last_check = time.time()
        return self._status

    # ---- Remote Control API (HTTP) ----

    def get_property(self, object_path: str, property_name: str) -> UE5Response:
        """Get a property value from a UObject."""
        payload = {
            "objectPath": object_path,
            "propertyName": property_name,
            "access": "READ_ACCESS",
        }
        return self._rc_request("PUT", "/remote/object/property", payload)

    def set_property(self, object_path: str, property_name: str, value) -> UE5Response:
        """Set a property value on a UObject."""
        payload = {
            "objectPath": object_path,
            "propertyName": property_name,
            "propertyValue": value,
            "access": "WRITE_ACCESS",
        }
        return self._rc_request("PUT", "/remote/object/property", payload)

    def call_function(
        self,
        object_path: str,
        function_name: str,
        parameters: dict | None = None,
    ) -> UE5Response:
        """Call a UFUNCTION on a UObject."""
        payload = {
            "objectPath": object_path,
            "functionName": function_name,
        }
        if parameters:
            payload["parameters"] = parameters
        return self._rc_request("PUT", "/remote/object/call", payload)

    def search_assets(
        self,
        query: str = "",
        class_name: str = "",
        package_path: str = "/Game/",
        limit: int = 50,
    ) -> UE5Response:
        """Search for assets in the project."""
        payload = {
            "query": query,
            "filter": {},
        }
        if class_name:
            payload["filter"]["classNames"] = [class_name]
        if package_path:
            payload["filter"]["packagePaths"] = [package_path]
        payload["filter"]["limit"] = limit
        return self._rc_request("PUT", "/remote/search/assets", payload)

    def search_objects(
        self,
        query: str = "",
        class_name: str = "",
        outer_path: str = "",
        limit: int = 50,
    ) -> UE5Response:
        """Search for UObjects in the editor."""
        payload = {
            "query": query,
            "filter": {},
        }
        if class_name:
            payload["filter"]["classNames"] = [class_name]
        if outer_path:
            payload["filter"]["outerPath"] = outer_path
        payload["filter"]["limit"] = limit
        return self._rc_request("PUT", "/remote/search/objects", payload)

    # ---- Editor Utility Functions ----

    def open_asset(self, asset_path: str) -> UE5Response:
        """Open an asset in the editor."""
        return self.call_function(
            "/Script/UnrealEd.Default__EditorAssetLibrary",
            "OpenEditorForAssets",
            {"AssetPaths": [asset_path]},
        )

    def save_asset(self, asset_path: str) -> UE5Response:
        """Save an asset."""
        return self.call_function(
            "/Script/UnrealEd.Default__EditorAssetLibrary",
            "SaveAsset",
            {"AssetToSave": asset_path, "bOnlyIfIsDirty": True},
        )

    def save_all(self) -> UE5Response:
        """Save all dirty assets."""
        return self.call_function(
            "/Script/UnrealEd.Default__EditorAssetLibrary",
            "SaveLoadedAssets",
            {"bOnlyIfIsDirty": True},
        )

    def compile_blueprint(self, blueprint_path: str) -> UE5Response:
        """Compile a Blueprint asset."""
        return self.call_function(
            "/Script/UnrealEd.Default__KismetEditorUtilities",
            "CompileBlueprint",
            {"BlueprintObj": blueprint_path},
        )

    def get_selected_actors(self) -> UE5Response:
        """Get currently selected actors in the level editor."""
        return self.call_function(
            "/Script/UnrealEd.Default__EditorLevelLibrary",
            "GetSelectedLevelActors",
        )

    def execute_console_command(self, command: str) -> UE5Response:
        """Execute an editor console command."""
        return self.call_function(
            "/Script/UnrealEd.Default__EditorLevelLibrary",
            "EditorExecuteConsoleCommand",
            {"ConsoleCommand": command},
        )

    # ---- Python Script Execution ----

    def execute_python(self, script: str) -> UE5Response:
        """Execute a Python script in UE5's embedded interpreter.

        Uses UE5's Remote Execution protocol:
        - UDP multicast on 239.0.0.1:6766 for editor discovery
        - TCP connection for command execution
        - The script has full access to the `unreal` module

        If UE5's remote_execution.py is available (copied from your UE5 install
        at Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/),
        it will use that directly. Otherwise falls back to HTTP-based execution.

        Required UE5 setup:
        - Python Editor Script Plugin enabled
        - Remote Execution enabled in plugin settings
        """
        # Strategy 1: Try via remote_execution module (proper UE5 protocol)
        result = self._execute_python_remote_exec(script)
        if result.success or result.data:
            return result  # Script was sent to UE5 — don't re-execute via another strategy

        # Strategy 2: Try via HTTP (PythonScriptLibrary.ExecutePythonCommand)
        result = self._execute_python_via_http(script)
        if result.success or result.data:
            return result  # Script was sent — don't re-execute

        # Strategy 3: Try via console command
        result = self._execute_python_via_console(script)
        return result

    def _execute_python_remote_exec(self, script: str) -> UE5Response:
        """Execute via UE5's remote_execution.py protocol (UDP multicast discovery + TCP)."""
        try:
            # Try to import UE5's remote_execution module
            # Users should copy it from their UE5 install to Bionics/ue5_modules/
            import importlib
            import sys

            # Add module path once (not every call)
            if "remote_execution" not in sys.modules:
                ue5_path = str(Path(__file__).parent.parent / "ue5_modules")
                if ue5_path not in sys.path:
                    sys.path.insert(0, ue5_path)

            try:
                remote_execution = importlib.import_module("remote_execution")
            except ImportError:
                return UE5Response(
                    success=False,
                    error="remote_execution.py not found. Copy it from your UE5 install: "
                          "Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py "
                          "to Bionics/ue5_modules/remote_execution.py"
                )

            remote_exec = remote_execution.RemoteExecution()
            try:
                remote_exec.start()
                time.sleep(0.3)  # Brief discovery window

                if not remote_exec.remote_nodes:
                    time.sleep(0.7)  # Extended wait only if nothing found yet
                if not remote_exec.remote_nodes:
                    return UE5Response(success=False, error="No UE5 editor instances found via Remote Execution")

                node_id = remote_exec.remote_nodes[0]["node_id"]
                remote_exec.open_command_connection(node_id)
                result = remote_exec.run_command(script, unattended=True)

                success = result.get("success", False)
                return UE5Response(
                    success=success,
                    data={
                        "output": result.get("output", []),
                        "result": result.get("result", ""),
                    },
                    error="" if success else result.get("result", "Execution failed"),
                )
            finally:
                remote_exec.stop()

        except Exception as e:
            logger.debug(f"Remote Execution protocol failed: {e}")
            return UE5Response(success=False, error=str(e))

    def _execute_python_via_http(self, script: str) -> UE5Response:
        """Execute Python via HTTP — calls PythonScriptLibrary.ExecutePythonCommand."""
        return self.call_function(
            "/Script/PythonScriptPlugin.Default__PythonScriptLibrary",
            "ExecutePythonCommand",
            {"PythonCommand": script},
        )

    def _execute_python_via_console(self, script: str) -> UE5Response:
        """Execute Python via editor console command (single-line only).

        Multi-line scripts with indentation (loops, functions, etc.) cannot
        be flattened to semicolons — they produce syntax errors.  For those,
        we encode the script as base64 and exec() it in one line.
        """
        lines = [ln for ln in script.split("\n") if ln.strip()]
        # Simple single-statement scripts can be sent directly
        if len(lines) <= 1:
            return self.execute_console_command(f"py {lines[0].strip()}" if lines else "py pass")

        # Multi-line: base64-encode and exec() to preserve indentation
        import base64
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        safe_cmd = f"py import base64; exec(base64.b64decode('{encoded}').decode('utf-8'))"
        return self.execute_console_command(safe_cmd)

    # ---- AnimGraph / Blueprint Graph Helpers ----

    def get_blueprint_nodes(self, blueprint_path: str) -> UE5Response:
        """Get all nodes in a Blueprint graph via Python execution."""
        script = f"""
import unreal
bp = unreal.load_asset('{blueprint_path}')
if bp:
    graphs = bp.get_editor_property('ubergraph_pages')
    result = []
    for graph in graphs:
        nodes = graph.get_editor_property('nodes')
        for node in nodes:
            result.append({{
                'name': node.get_name(),
                'class': node.get_class().get_name(),
                'x': node.node_pos_x,
                'y': node.node_pos_y,
            }})
    print(unreal.Json.to_json_string(result))
"""
        return self.execute_python(script)

    def get_anim_graph_nodes(self, anim_bp_path: str) -> UE5Response:
        """Get AnimGraph nodes from an Animation Blueprint."""
        script = f"""
import unreal
anim_bp = unreal.load_asset('{anim_bp_path}')
if anim_bp:
    graphs = unreal.AnimationBlueprintLibrary.get_anim_graph_nodes(anim_bp)
    result = []
    for node in graphs:
        result.append({{
            'name': node.get_name(),
            'class': node.get_class().get_name(),
        }})
    print(unreal.Json.to_json_string(result))
"""
        return self.execute_python(script)

    def connect_blueprint_pins(
        self,
        blueprint_path: str,
        source_node: str,
        source_pin: str,
        target_node: str,
        target_pin: str,
    ) -> UE5Response:
        """Connect two Blueprint node pins programmatically."""
        script = f"""
import unreal
bp = unreal.load_asset('{blueprint_path}')
if bp:
    graphs = bp.get_editor_property('ubergraph_pages')
    source = None
    target = None
    for graph in graphs:
        for node in graph.get_editor_property('nodes'):
            if node.get_name() == '{source_node}':
                source = node
            if node.get_name() == '{target_node}':
                target = node
    if source and target:
        # Find pins by name
        src_pin = None
        tgt_pin = None
        for pin in source.get_editor_property('pins'):
            if pin.get_name() == '{source_pin}':
                src_pin = pin
                break
        for pin in target.get_editor_property('pins'):
            if pin.get_name() == '{target_pin}':
                tgt_pin = pin
                break
        if src_pin and tgt_pin:
            src_pin.make_link_to(tgt_pin)
            unreal.BlueprintEditorLibrary.compile_blueprint(bp)
            print('SUCCESS: Pins connected')
        else:
            print('ERROR: Pin not found')
    else:
        print('ERROR: Node not found')
"""
        return self.execute_python(script)

    def create_blueprint_node(
        self,
        blueprint_path: str,
        node_class: str,
        pos_x: int = 0,
        pos_y: int = 0,
    ) -> UE5Response:
        """Create a new node in a Blueprint graph."""
        script = f"""
import unreal
bp = unreal.load_asset('{blueprint_path}')
if bp:
    graph = bp.get_editor_property('ubergraph_pages')[0]
    node = unreal.BlueprintEditorLibrary.add_node_to_graph(
        graph, '{node_class}', {pos_x}, {pos_y}
    )
    if node:
        print(f'SUCCESS: Created {{node.get_name()}}')
    else:
        print('ERROR: Failed to create node')
"""
        return self.execute_python(script)

    # ---- Internal ----

    def _rc_request(self, method: str, endpoint: str, payload: dict | None = None) -> UE5Response:
        """Make an HTTP request to the Remote Control API."""
        url = f"{self._rc_base}{endpoint}"
        try:
            if method == "GET":
                resp = self._session.get(url, timeout=RC_TIMEOUT)
            elif method == "PUT":
                resp = self._session.put(url, json=payload, timeout=RC_TIMEOUT)
            elif method == "POST":
                resp = self._session.post(url, json=payload, timeout=RC_TIMEOUT)
            else:
                return UE5Response(success=False, error=f"Unsupported method: {method}")

            try:
                data = resp.json() if resp.content else {}
            except ValueError:
                data = {"raw_response": resp.text[:500]}
            success = 200 <= resp.status_code < 300

            if not success:
                logger.warning(f"UE5 API error {resp.status_code}: {data}")

            return UE5Response(
                success=success,
                data=data,
                status_code=resp.status_code,
            )

        except requests.ConnectionError:
            self._status = ConnectionStatus.EDITOR_NOT_RUNNING
            return UE5Response(success=False, error="UE5 not reachable")
        except requests.Timeout:
            return UE5Response(success=False, error="Request timed out")
        except Exception as e:
            return UE5Response(success=False, error=str(e))

    def close(self):
        self._session.close()
