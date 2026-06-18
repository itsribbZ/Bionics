"""Planets readiness — VERIFY the sw.planet.refviewer command is live in the running editor.

Fires `sw.planet.refviewer demo` WITHOUT PIE (zero-risk: no widget, no fetch). If the
LogPlanetRefViewer category emits the 'requires PIE' line, that directly proves the 05-15
C++ is compiled + the command is registered in THIS editor (turns inferred -> verified).
"""
import os
import sys
import json
import time
from pathlib import Path

BIONICS = r"C:\Users\jbro1\Desktop\T1\Bionics"
PROJECT = r"C:\Users\jbro1\Documents\Sworder721\MyProject"
LOG = Path(PROJECT) / "Saved" / "Logs" / "MyProject.log"

inst = Path(PROJECT) / ".bionics-bridge" / "instance.json"
data = json.loads(inst.read_text(encoding="utf-8"))
os.environ["BIONICS_BRIDGE_URL"] = data["url"]
os.environ["BIONICS_BRIDGE_TOKEN"] = data["token"]

sys.path.insert(0, BIONICS)
os.chdir(BIONICS)

from bionics_tools.ue5_native import call_bridge_tool  # noqa: E402

# Mark current log size so we only read NEW lines.
start = LOG.stat().st_size if LOG.exists() else 0
print(f"[verify] log={LOG} start_offset={start}")

fire = call_bridge_tool("execute_console_command", {"command": "sw.planet.refviewer demo"})
print(f"[verify] fire ok={getattr(fire,'ok',None)} error={getattr(fire,'error',None)!r}")
print(f"[verify] fire content={getattr(fire,'content','')[:300]}")

# The command runs on the game thread; give it a moment to log.
time.sleep(2.5)

new = ""
if LOG.exists():
    with LOG.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(start)
        new = f.read()

print(f"[verify] new log bytes={len(new)}")
markers = ["LogPlanetRefViewer", "RefViewer", "requires PIE", "no PlayerController",
           "Fetching:", "Fetch complete", "Auto-spawned", "WBP class not found",
           "Bound impostor", "cmd toggle"]
hits = [ln for ln in new.splitlines() if any(m in ln for m in markers)]
print(f"[verify] === {len(hits)} relevant log line(s) ===")
for ln in hits[-40:]:
    print("   " + ln)

verdict = "UNKNOWN"
if any("requires PIE" in h or "no PlayerController" in h for h in hits):
    verdict = "COMMAND LIVE (compiled+registered); needs PIE for full smoke"
elif any("Fetch complete" in h or "Fetching:" in h for h in hits):
    verdict = "COMMAND LIVE + already fetched (PIE was active)"
elif any("RefViewer" in h or "LogPlanetRefViewer" in h for h in hits):
    verdict = "COMMAND LIVE (RefViewer responded)"
elif not hits:
    verdict = "NO RefViewer log output — command may NOT be registered (or log not flushed)"
print(f"\n[verify] VERDICT: {verdict}")
