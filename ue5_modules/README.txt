UE5 Remote Execution Module
============================

Copy remote_execution.py from your UE5 installation to this directory:

Source:
  <UE5_INSTALL>/Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py

Example (UE 5.4):
  C:\Program Files\Epic Games\UE_5.4\Engine\Plugins\Experimental\PythonScriptPlugin\Content\Python\remote_execution.py

This module enables Bionics to execute Python scripts directly inside the UE5 Editor
with full access to the `unreal` module. Required for Blueprint/AnimGraph pin connections
and other graph topology operations that cannot be done via the HTTP Remote Control API.

UE5 Editor Setup:
  1. Enable plugin: "Python Editor Script Plugin"
  2. Enable plugin: "Web Remote Control" (for HTTP API)
  3. In Python plugin settings, enable "Remote Execution"
  4. Restart the editor
