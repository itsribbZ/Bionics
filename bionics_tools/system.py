"""System Tools — Input, Capture, Vision, Windows, Clipboard, Processes.

These are Bionics-native tools that work on any desktop app (not UE5-specific).
They form the differentiator vs soft-ue-cli: vision-based automation that
falls through when API routes fail.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Literal

from core.bridge import SafetyTier, ToolResult, bionics_tool

logger = logging.getLogger("bionics.tools.system")


# ============================================================================
# INPUT — Mouse & Keyboard
# ============================================================================


@bionics_tool(
    name="click",
    category="input",
    safety_tier=SafetyTier.MODERATE,
    destructive=False,
    idempotent=False,
    title="Click at Coordinates",
)
def click(
    x: Annotated[int, "X pixel coordinate (0 = left edge)"],
    y: Annotated[int, "Y pixel coordinate (0 = top edge)"],
    button: Literal["left", "right", "middle"] = "left",
    clicks: Annotated[int, "Number of clicks (1-3)"] = 1,
) -> ToolResult:
    """Click at screen coordinates (left/right/middle button, 1-3 clicks)."""
    import pyautogui
    try:
        # Clamp to screen for safety
        w, h = pyautogui.size()
        if w <= 0 or h <= 0:
            return ToolResult.failure("Cannot determine screen size (0x0)")
        cx = max(1, min(x, w - 2))
        cy = max(1, min(y, h - 2))
        # pyautogui.click handles 1-3 clicks via the `clicks` kwarg
        pyautogui.click(cx, cy, button=button, clicks=max(1, min(clicks, 3)))
        return ToolResult.success(
            content=f"Clicked at ({cx},{cy}) with {button}",
            data={"x": cx, "y": cy, "button": button, "clicks": clicks},
        )
    except Exception as e:
        return ToolResult.failure(f"Click failed: {e}")


@bionics_tool(
    name="mouse_move",
    category="input",
    safety_tier=SafetyTier.SAFE,
    read_only=False,
    idempotent=True,
    title="Move Mouse",
)
def mouse_move(
    x: Annotated[int, "X pixel coordinate"],
    y: Annotated[int, "Y pixel coordinate"],
    duration: Annotated[float, "Move duration in seconds"] = 0.3,
) -> ToolResult:
    """Move the mouse cursor to screen coordinates without clicking."""
    import pyautogui
    w, h = pyautogui.size()
    cx = max(1, min(x, w - 2))
    cy = max(1, min(y, h - 2))
    pyautogui.moveTo(cx, cy, duration=duration)
    return ToolResult.success(
        content=f"Mouse moved to ({cx},{cy})",
        data={"x": cx, "y": cy},
    )


@bionics_tool(
    name="drag",
    category="input",
    safety_tier=SafetyTier.MODERATE,
    title="Drag",
)
def drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.5,
    button: Literal["left", "right", "middle"] = "left",
) -> ToolResult:
    """Drag from (start_x, start_y) to (end_x, end_y)."""
    import pyautogui
    pyautogui.moveTo(start_x, start_y, duration=0.2)
    pyautogui.mouseDown(button=button)
    try:
        pyautogui.moveTo(end_x, end_y, duration=duration)
    finally:
        pyautogui.mouseUp(button=button)
    return ToolResult.success(
        content=f"Dragged ({start_x},{start_y}) → ({end_x},{end_y})",
        data={"start": [start_x, start_y], "end": [end_x, end_y]},
    )


@bionics_tool(
    name="scroll",
    category="input",
    safety_tier=SafetyTier.SAFE,
    read_only=False,
    title="Scroll",
)
def scroll(
    clicks: Annotated[int, "Scroll amount (positive=up, negative=down)"],
    x: int | None = None,
    y: int | None = None,
) -> ToolResult:
    """Scroll the mouse wheel (positive=up, negative=down)."""
    import pyautogui
    if x is not None and y is not None:
        pyautogui.scroll(clicks, x, y)
    else:
        pyautogui.scroll(clicks)
    return ToolResult.success(
        content=f"Scrolled {clicks}",
        data={"clicks": clicks},
    )


@bionics_tool(
    name="type_text",
    category="input",
    safety_tier=SafetyTier.MODERATE,
    title="Type Text",
)
def type_text(
    text: Annotated[str, "Text to type (supports Unicode)"],
    interval: Annotated[float, "Delay between keystrokes (seconds)"] = 0.02,
) -> ToolResult:
    """Type text character-by-character (full Unicode support via pynput)."""
    from pynput.keyboard import Controller
    kb = Controller()
    for ch in text:
        kb.type(ch)
        time.sleep(interval)
    return ToolResult.success(
        content=f"Typed {len(text)} characters",
        data={"char_count": len(text)},
    )


@bionics_tool(
    name="hotkey",
    category="input",
    safety_tier=SafetyTier.MODERATE,
    title="Press Hotkey",
)
def hotkey(
    keys: Annotated[list[str], "List of keys to press together, e.g. ['ctrl','shift','s']"],
) -> ToolResult:
    """Press a keyboard chord (e.g. ctrl+shift+s)."""
    import pyautogui
    pyautogui.hotkey(*keys)
    return ToolResult.success(
        content=f"Pressed {' + '.join(keys)}",
        data={"keys": keys},
    )


@bionics_tool(
    name="press_key",
    category="input",
    safety_tier=SafetyTier.MODERATE,
    title="Press Single Key",
)
def press_key(
    key: Annotated[str, "Single key name (e.g. 'enter', 'escape', 'f9')"],
    presses: int = 1,
) -> ToolResult:
    """Press a single key one or more times."""
    import pyautogui
    pyautogui.press(key, presses=presses)
    return ToolResult.success(
        content=f"Pressed '{key}' x{presses}",
        data={"key": key, "presses": presses},
    )


@bionics_tool(
    name="get_mouse_pos",
    category="input",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    title="Get Mouse Position",
    output_schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
    },
)
def get_mouse_pos() -> ToolResult:
    """Return the current mouse cursor position."""
    import pyautogui
    pos = pyautogui.position()
    return ToolResult.success(
        content=f"Mouse at ({pos.x}, {pos.y})",
        data={"x": pos.x, "y": pos.y},
    )


@bionics_tool(
    name="get_screen_size",
    category="input",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="Get Screen Size",
    output_schema={
        "type": "object",
        "properties": {
            "width": {"type": "integer"},
            "height": {"type": "integer"},
        },
        "required": ["width", "height"],
    },
)
def get_screen_size() -> ToolResult:
    """Return the primary display resolution."""
    import pyautogui
    sz = pyautogui.size()
    return ToolResult.success(
        content=f"{sz.width}x{sz.height}",
        data={"width": sz.width, "height": sz.height},
    )


# ============================================================================
# CAPTURE — Screenshots
# ============================================================================


@bionics_tool(
    name="capture_screen",
    category="capture",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    title="Capture Screen",
)
def capture_screen(
    monitor: Annotated[int, "Monitor index (0=primary, 1+=secondary)"] = 0,
    save_path: Annotated[str, "Optional PNG save path"] = "",
    as_base64: Annotated[bool, "Return PNG as base64 data URL"] = False,
) -> ToolResult:
    """Capture a screenshot of the specified monitor."""
    try:
        import mss
        import mss.tools
        with mss.mss() as sct:
            # mss.monitors[0] is the virtual bounding rect; monitors[1..] are real displays.
            real_monitors = sct.monitors[1:]
            if monitor < 0 or monitor >= len(real_monitors):
                return ToolResult.failure(
                    f"Invalid monitor index {monitor}; "
                    f"available: 0-{len(real_monitors)-1} ({len(real_monitors)} displays)",
                )
            img = sct.grab(real_monitors[monitor])
            png = mss.tools.to_png(img.rgb, img.size)

            data: dict = {
                "width": img.size[0],
                "height": img.size[1],
                "monitor": monitor,
                "bytes_size": len(png),
            }

            if save_path:
                p = Path(save_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(png)
                data["saved_to"] = str(p)

            if as_base64:
                import base64
                data["image_base64"] = base64.b64encode(png).decode("ascii")
                data["mime_type"] = "image/png"

            return ToolResult.success(
                content=f"Captured {img.size[0]}x{img.size[1]} ({len(png)} bytes)",
                data=data,
            )
    except Exception as e:
        return ToolResult.failure(f"Capture failed: {e}")


@bionics_tool(
    name="capture_region",
    category="capture",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Capture Region",
)
def capture_region(
    x: int,
    y: int,
    width: int,
    height: int,
    save_path: str = "",
    as_base64: bool = False,
) -> ToolResult:
    """Capture a rectangular region of the primary display."""
    try:
        import mss
        import mss.tools
        region = {"left": x, "top": y, "width": width, "height": height}
        with mss.mss() as sct:
            img = sct.grab(region)
            png = mss.tools.to_png(img.rgb, img.size)
            data: dict = {
                "width": width,
                "height": height,
                "region": [x, y, width, height],
                "bytes_size": len(png),
            }
            if save_path:
                p = Path(save_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(png)
                data["saved_to"] = str(p)
            if as_base64:
                import base64
                data["image_base64"] = base64.b64encode(png).decode("ascii")
                data["mime_type"] = "image/png"
            return ToolResult.success(
                content=f"Captured region {width}x{height} at ({x},{y})",
                data=data,
            )
    except Exception as e:
        return ToolResult.failure(f"Region capture failed: {e}")


@bionics_tool(
    name="list_monitors",
    category="capture",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="List Monitors",
    output_schema={
        "type": "object",
        "properties": {
            "monitors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "left": {"type": "integer"},
                        "top": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                    "required": ["index", "left", "top", "width", "height"],
                },
            },
            "count": {"type": "integer"},
        },
        "required": ["monitors", "count"],
    },
)
def list_monitors() -> ToolResult:
    """List all available display monitors and their bounds."""
    try:
        import mss
        with mss.mss() as sct:
            monitors = []
            # Skip index 0 (virtual "all monitors" bounding box)
            for i, mon in enumerate(sct.monitors[1:], start=0):
                monitors.append({
                    "index": i,
                    "left": mon["left"],
                    "top": mon["top"],
                    "width": mon["width"],
                    "height": mon["height"],
                })
            return ToolResult.success(
                content=f"Found {len(monitors)} monitors",
                data={"monitors": monitors, "count": len(monitors)},
            )
    except Exception as e:
        return ToolResult.failure(f"Monitor enumeration failed: {e}")


# ============================================================================
# VISION — Template Matching & OCR
# ============================================================================


@bionics_tool(
    name="find_image",
    category="vision",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Find Image on Screen",
)
def find_image(
    template_path: Annotated[str, "Path to template PNG to search for"],
    confidence: Annotated[float, "Match threshold 0.5-1.0"] = 0.8,
    multi_scale: bool = True,
) -> ToolResult:
    """Locate a template image on screen using OpenCV template matching."""
    try:
        import cv2  # type: ignore
        import mss
        import numpy as np

        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            return ToolResult.failure(f"Template not found or unreadable: {template_path}")

        with mss.mss() as sct:
            img = np.array(sct.grab(sct.monitors[1]))
        screen = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        scales = [0.75, 0.85, 1.0, 1.15, 1.25] if multi_scale else [1.0]
        best_match = None
        for scale in scales:
            scaled = cv2.resize(
                template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )
            if scaled.shape[0] > screen.shape[0] or scaled.shape[1] > screen.shape[1]:
                continue
            result = cv2.matchTemplate(screen, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val >= confidence and (best_match is None or max_val > best_match[0]):
                h, w = scaled.shape[:2]
                cx = max_loc[0] + w // 2
                cy = max_loc[1] + h // 2
                best_match = (max_val, cx, cy, w, h, scale)

        if best_match is None:
            return ToolResult.failure(
                f"Template not found (threshold={confidence})",
            )

        conf, cx, cy, w, h, scale = best_match
        return ToolResult.success(
            content=f"Found at ({cx},{cy}) conf={conf:.2f} scale={scale}",
            data={
                "x": cx, "y": cy, "width": w, "height": h,
                "confidence": round(conf, 3), "scale": scale,
            },
        )
    except Exception as e:
        return ToolResult.failure(f"Template match failed: {e}")


@bionics_tool(
    name="ocr_screen",
    category="vision",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="OCR Screen",
)
def ocr_screen(
    region: Annotated[list[int] | None, "[x,y,w,h] or null for full screen"] = None,
) -> ToolResult:
    """Extract text from screen via OCR (requires pytesseract + Tesseract installed)."""
    try:
        import mss
        import mss.tools
        import pytesseract  # type: ignore
        from PIL import Image

        with mss.mss() as sct:
            if region and len(region) == 4:
                target = {
                    "left": region[0], "top": region[1],
                    "width": region[2], "height": region[3],
                }
            else:
                target = sct.monitors[1]
            shot = sct.grab(target)
            pil_img = Image.frombytes("RGB", shot.size, shot.rgb)
            text = pytesseract.image_to_string(pil_img)
        return ToolResult.success(
            content=text[:500],
            data={"text": text, "region": region or "full"},
        )
    except ImportError:
        return ToolResult.failure(
            "pytesseract not installed. Run: pip install pytesseract (+ install Tesseract OCR)",
        )
    except Exception as e:
        return ToolResult.failure(f"OCR failed: {e}")


# ============================================================================
# WINDOWS — Window Management
# ============================================================================


@bionics_tool(
    name="list_windows",
    category="system",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    title="List Windows",
    output_schema={
        "type": "object",
        "properties": {
            "windows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "rect": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                        "handle": {"type": "integer"},
                    },
                    "required": ["title", "rect", "handle"],
                },
            },
            "count": {"type": "integer"},
        },
        "required": ["windows", "count"],
    },
)
def list_windows(title_filter: str = "") -> ToolResult:
    """List all visible windows (optionally filtered by title substring)."""
    try:
        from core.ui_automation import UIAutomation
        ui = UIAutomation()
        windows = ui.find_windows(title=title_filter) if title_filter else ui.find_windows()
        result = [
            {"title": w.title, "rect": list(w.rect), "handle": w.handle}
            for w in windows
        ]
        return ToolResult.success(
            content=f"Found {len(result)} windows",
            data={"windows": result, "count": len(result)},
        )
    except Exception as e:
        return ToolResult.failure(f"Window listing failed: {e}")


@bionics_tool(
    name="focus_window",
    category="system",
    safety_tier=SafetyTier.MODERATE,
    title="Focus Window",
)
def focus_window(title: Annotated[str, "Substring of window title to focus"]) -> ToolResult:
    """Bring a window to the foreground by title match."""
    try:
        from core.ui_automation import UIAutomation
        ui = UIAutomation()
        ok = ui.focus_window(title=title)
        if ok:
            return ToolResult.success(content=f"Focused: {title}", data={"title": title})
        return ToolResult.failure(f"Window not found: {title}")
    except Exception as e:
        return ToolResult.failure(f"Focus failed: {e}")


@bionics_tool(
    name="find_window",
    category="system",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Find Window",
)
def find_window(title: str) -> ToolResult:
    """Find a window by title substring and return its bounds."""
    try:
        from core.ui_automation import UIAutomation
        ui = UIAutomation()
        matches = ui.find_windows(title=title)
        if not matches:
            return ToolResult.failure(f"Window not found: {title}")
        w = matches[0]
        return ToolResult.success(
            content=f"{w.title} @ {w.rect}",
            data={"title": w.title, "rect": list(w.rect), "handle": w.handle},
        )
    except Exception as e:
        return ToolResult.failure(f"Find window failed: {e}")


# ============================================================================
# CLIPBOARD
# ============================================================================


@bionics_tool(
    name="clipboard_get",
    category="system",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get Clipboard",
)
def clipboard_get() -> ToolResult:
    """Read the current clipboard text contents."""
    try:
        import pyperclip  # type: ignore
        text = pyperclip.paste()
        return ToolResult.success(content=text[:500], data={"text": text})
    except ImportError:
        # Fallback: Windows PowerShell
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["powershell", "-command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=5,
                )
                text = result.stdout.rstrip("\r\n")
                return ToolResult.success(
                    content=text[:500], data={"text": text}
                )
            except Exception as e:
                return ToolResult.failure(f"Clipboard read failed: {e}")
        return ToolResult.failure("pyperclip not installed. Run: pip install pyperclip")
    except Exception as e:
        return ToolResult.failure(f"Clipboard read failed: {e}")


@bionics_tool(
    name="clipboard_set",
    category="system",
    safety_tier=SafetyTier.MODERATE,
    title="Set Clipboard",
)
def clipboard_set(text: str) -> ToolResult:
    """Write text to the clipboard."""
    try:
        import pyperclip  # type: ignore
        pyperclip.copy(text)
        return ToolResult.success(
            content=f"Clipboard set ({len(text)} chars)", data={"length": len(text)},
        )
    except ImportError:
        if sys.platform == "win32":
            try:
                proc = subprocess.Popen(
                    ["clip"], stdin=subprocess.PIPE, text=True, encoding="utf-8"
                )
                proc.communicate(input=text, timeout=5)
                return ToolResult.success(content="Clipboard set via clip.exe")
            except Exception as e:
                return ToolResult.failure(f"Clipboard write failed: {e}")
        return ToolResult.failure("pyperclip not installed")
    except Exception as e:
        return ToolResult.failure(f"Clipboard write failed: {e}")


# ============================================================================
# WAIT / UTILITY
# ============================================================================


@bionics_tool(
    name="wait",
    category="general",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="Wait",
)
def wait(
    seconds: Annotated[float, "Seconds to sleep (max 60)"] = 1.0,
) -> ToolResult:
    """Pause execution for N seconds (max 60)."""
    duration = max(0.0, min(seconds, 60.0))
    time.sleep(duration)
    return ToolResult.success(
        content=f"Waited {duration:.2f}s", data={"seconds": duration},
    )


@bionics_tool(
    name="wait_for_image",
    category="vision",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Wait for Image",
)
def wait_for_image(
    template_path: str,
    timeout: Annotated[float, "Max seconds to wait"] = 10.0,
    poll_interval: float = 0.5,
    confidence: float = 0.8,
) -> ToolResult:
    """Wait until a template image appears on screen or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        result = find_image(template_path, confidence, multi_scale=True)
        if result.ok:
            result.meta = result.meta or {}
            result.meta["wait_elapsed"] = round(time.time() - start, 2)
            return result
        time.sleep(poll_interval)
    return ToolResult.failure(
        f"Image did not appear within {timeout}s", elapsed=timeout,
    )


# ============================================================================
# SYSTEM INFO
# ============================================================================


@bionics_tool(
    name="system_info",
    category="system",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="System Info",
    output_schema={
        "type": "object",
        "properties": {
            "platform": {"type": "string"},
            "platform_version": {"type": "string"},
            "platform_release": {"type": "string"},
            "python_version": {"type": "string"},
            "processor": {"type": "string"},
            "architecture": {"type": "string"},
            "machine": {"type": "string"},
        },
        "required": ["platform", "python_version"],
    },
)
def system_info() -> ToolResult:
    """Return OS, Python, and Bionics version information."""
    import platform
    info = {
        "platform": platform.system(),
        "platform_version": platform.version(),
        "platform_release": platform.release(),
        "python_version": platform.python_version(),
        "processor": platform.processor(),
        "architecture": platform.architecture()[0],
        "machine": platform.machine(),
    }
    return ToolResult.success(
        content=f"{info['platform']} {info['platform_release']} / Python {info['python_version']}",
        data=info,
    )


@bionics_tool(
    name="list_processes",
    category="system",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Processes",
    output_schema={
        "type": "object",
        "properties": {
            "processes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pid": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                    "required": ["pid", "name"],
                },
            },
            "count": {"type": "integer"},
        },
        "required": ["processes", "count"],
    },
)
def list_processes(
    name_filter: Annotated[str, "Process name substring (optional)"] = "",
) -> ToolResult:
    """List running processes (name + pid), optionally filtered by name."""
    try:
        import psutil  # type: ignore
        procs = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = p.info["name"] or ""
                if name_filter and name_filter.lower() not in name.lower():
                    continue
                procs.append({"pid": p.info["pid"], "name": name})
            except Exception:
                continue
        return ToolResult.success(
            content=f"Found {len(procs)} processes",
            data={"processes": procs[:200], "count": len(procs)},
        )
    except ImportError:
        # Fallback: tasklist on Windows
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10,
                )
                procs = []
                for line in result.stdout.strip().split("\n"):
                    parts = [p.strip('"') for p in line.split('","')]
                    if len(parts) >= 2:
                        name = parts[0].lstrip('"')
                        if not name_filter or name_filter.lower() in name.lower():
                            try:
                                procs.append({"name": name, "pid": int(parts[1])})
                            except ValueError:
                                pass
                return ToolResult.success(
                    content=f"Found {len(procs)} processes (via tasklist)",
                    data={"processes": procs[:200], "count": len(procs)},
                )
            except Exception as e:
                return ToolResult.failure(f"tasklist failed: {e}")
        return ToolResult.failure("psutil not installed. Run: pip install psutil")
    except Exception as e:
        return ToolResult.failure(f"Process listing failed: {e}")
