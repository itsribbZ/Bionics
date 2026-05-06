"""Bionics Action Executor v2 - Registry-based actions with element detection.

Upgrades from v1:
- Plugin registry for action handlers (extensible)
- Element-based actions (click_element, drag_to_element)
- Coordinate verification before clicking
- Template execution support
- Adapter pattern for application-specific handlers
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pyautogui
from pynput.keyboard import Controller as KeyboardController

from core.precision import CoordinateAnchor, ElementDetector, ElementMatch
from core.ui_automation import UIAutomation

logger = logging.getLogger("bionics.executor")

# Safety: pyautogui failsafe - move mouse to corner to abort
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


class FailureType(Enum):
    """Classification of why an action failed — informs retry strategy."""
    NONE = "none"                        # No failure
    ELEMENT_NOT_FOUND = "element_not_found"  # Template matching found nothing
    VERIFICATION_FAILED = "verification_failed"  # Post-action SSIM check failed
    TIMEOUT = "timeout"                  # Action timed out
    PYAUTOGUI_ERROR = "pyautogui_error"  # Mouse/keyboard operation failed
    UE5_DISCONNECTED = "ue5_disconnected"  # UE5 bridge lost connection
    SAFETY_DENIED = "safety_denied"      # Safety layer blocked the action
    UNKNOWN = "unknown"                  # Unclassified error


@dataclass
class ActionResult:
    """Result of executing an action."""
    action: str
    params: dict[str, Any]
    success: bool
    error: str = ""
    failure_type: FailureType = field(default_factory=lambda: FailureType.NONE)
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    element_match: ElementMatch | None = None


@dataclass
class Action:
    """An action to be executed."""
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    step_index: int = -1
    template_name: str = ""  # If this action is from a template
    verify_after: bool = True  # Whether to verify after execution


class ActionExecutor:
    """Executes input actions on the user's PC.

    v2: Registry-based with element detection support.
    """

    def __init__(self, detector: ElementDetector | None = None):
        self._kb = KeyboardController()
        self._detector = detector or ElementDetector()
        self._ui_auto = UIAutomation()
        self._action_log: deque[ActionResult] = deque(maxlen=1000)
        self._capture_fn: Callable | None = None  # Set by agent for element verification
        self._capture_ref: Any = None  # ScreenCapture — used for LLM-coord unscaling

        # Action handler registry
        self._handlers: dict[str, Callable] = {}
        self._register_defaults()

    def _register_defaults(self):
        """Register all built-in action handlers."""
        defaults = {
            # Basic input
            "mouse_move": self._mouse_move,
            "click": self._click,
            "double_click": self._double_click,
            "right_click": self._right_click,
            "drag": self._drag,
            "scroll": self._scroll,
            "type_text": self._type_text,
            "hotkey": self._hotkey,
            "wait": self._wait,
            "screenshot": self._screenshot,
            "read_screen": self._read_screen,
            "switch_window": self._switch_window,

            # Element-based actions (v2)
            "click_element": self._click_element,
            "double_click_element": self._double_click_element,
            "drag_to_element": self._drag_to_element,
            "wait_for_element": self._wait_for_element,

            # Precision actions (v2)
            "click_anchor": self._click_anchor,
            "verified_click": self._verified_click,

            # UI Automation actions (v2)
            "focus_window": self._focus_window,
            "find_window": self._find_window,
            "click_control": self._click_control,
        }
        for name, handler in defaults.items():
            self._handlers[name] = handler

    def register_handler(self, name: str, handler: Callable):
        """Register a custom action handler (plugin system)."""
        self._handlers[name] = handler
        logger.info(f"Registered custom handler: {name}")

    def set_capture_function(self, fn: Callable):
        """Set the screen capture function (provided by agent/capture module)."""
        self._capture_fn = fn

    def set_capture_ref(self, capture: Any):
        """Share the ScreenCapture instance so executor can unscale LLM-space coords.

        LLMs see a downsampled screenshot (max_width) and return click coords
        in that space. Before pyautogui sees them we scale back up to native
        using the capture's last-observed ratio. Matches Anthropic Computer
        Use's get_scale_factor() contract.
        """
        self._capture_ref = capture

    def execute(self, action: Action) -> ActionResult:
        """Execute a single action. Returns the result."""
        start = time.time()

        handler = self._handlers.get(action.name)
        if handler is None:
            result = ActionResult(
                action=action.name,
                params=action.params,
                success=False,
                error=f"Unknown action: {action.name}. Available: {list(self._handlers.keys())}",
            )
            self._action_log.append(result)
            return result

        try:
            ret = handler(**action.params)
            elapsed = (time.time() - start) * 1000
            result = ActionResult(
                action=action.name,
                params=action.params,
                success=True,
                duration_ms=elapsed,
                element_match=ret if isinstance(ret, ElementMatch) else None,
            )
            logger.info(f"EXECUTED: {action.name} {action.params} ({elapsed:.0f}ms)")
        except ValueError as e:
            # ValueError = element not found (from _click_element, _drag_to_element, etc.)
            elapsed = (time.time() - start) * 1000
            result = ActionResult(
                action=action.name,
                params=action.params,
                success=False,
                error=str(e),
                failure_type=FailureType.ELEMENT_NOT_FOUND,
                duration_ms=elapsed,
            )
            logger.error(f"FAILED (element_not_found): {action.name} - {e}")
        except TimeoutError as e:
            elapsed = (time.time() - start) * 1000
            result = ActionResult(
                action=action.name,
                params=action.params,
                success=False,
                error=str(e),
                failure_type=FailureType.TIMEOUT,
                duration_ms=elapsed,
            )
            logger.error(f"FAILED (timeout): {action.name} - {e}")
        except pyautogui.FailSafeException as e:
            elapsed = (time.time() - start) * 1000
            result = ActionResult(
                action=action.name,
                params=action.params,
                success=False,
                error=str(e),
                failure_type=FailureType.PYAUTOGUI_ERROR,
                duration_ms=elapsed,
            )
            logger.error(f"FAILED (failsafe): {action.name} - {e}")
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            result = ActionResult(
                action=action.name,
                params=action.params,
                success=False,
                error=str(e),
                failure_type=FailureType.UNKNOWN,
                duration_ms=elapsed,
            )
            logger.error(f"FAILED (unknown): {action.name} {action.params} - {e}")

        self._action_log.append(result)
        return result

    def execute_simple(self, action_name: str, params: dict) -> ActionResult:
        """Convenience method for executing an action by name and params dict."""
        return self.execute(Action(name=action_name, params=params))

    # --- Helpers ---

    def _clamp_to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Unscale LLM-space coords to native, then clamp to screen bounds."""
        if self._capture_ref is not None:
            try:
                x, y = self._capture_ref.unscale_coords(x, y)
            except Exception:
                pass  # Fall through to raw coords if capture ref is misconfigured
        w, h = self.get_screen_size()
        return (max(1, min(int(x), w - 2)), max(1, min(int(y), h - 2)))

    # --- Basic Action Handlers ---

    def _mouse_move(self, x: int, y: int, duration: float = 0.3):
        x, y = self._clamp_to_screen(x, y)
        pyautogui.moveTo(x, y, duration=duration)

    def _click(self, x: int | None = None, y: int | None = None, button: str = "left"):
        if x is not None and y is not None:
            x, y = self._clamp_to_screen(x, y)
            pyautogui.click(x, y, button=button)
        else:
            pyautogui.click(button=button)

    def _double_click(self, x: int | None = None, y: int | None = None):
        if x is not None and y is not None:
            x, y = self._clamp_to_screen(x, y)
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.doubleClick()

    def _right_click(self, x: int | None = None, y: int | None = None):
        if x is not None and y is not None:
            x, y = self._clamp_to_screen(x, y)
            pyautogui.rightClick(x, y)
        else:
            pyautogui.rightClick()

    def _drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5):
        start_x, start_y = self._clamp_to_screen(start_x, start_y)
        end_x, end_y = self._clamp_to_screen(end_x, end_y)
        pyautogui.moveTo(start_x, start_y, duration=0.2)
        pyautogui.mouseDown()
        try:
            pyautogui.moveTo(end_x, end_y, duration=duration)
        finally:
            pyautogui.mouseUp()

    def _scroll(self, clicks: int, x: int | None = None, y: int | None = None):
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x, y)
        else:
            pyautogui.scroll(clicks)

    def _type_text(self, text: str, interval: float = 0.02):
        # Use pynput for full Unicode support (pyautogui.write drops non-ASCII)
        for char in text:
            self._kb.type(char)
            time.sleep(interval)

    def _hotkey(self, keys: list[str]):
        pyautogui.hotkey(*keys)

    def _wait(self, seconds: float = 1.0):
        time.sleep(seconds)

    def _screenshot(self):
        if self._capture_fn:
            return self._capture_fn()
        logger.warning("screenshot: no capture function set")

    def _read_screen(self):
        if self._capture_fn:
            return self._capture_fn()
        logger.warning("read_screen: no capture function set")

    def _switch_window(self, title: str = ""):
        if title:
            success = self._ui_auto.focus_window(title=title)
            if not success:
                pyautogui.hotkey("alt", "tab")
                time.sleep(0.3)
        else:
            pyautogui.hotkey("alt", "tab")

    # --- Element-Based Actions (v2) ---

    def _click_element(
        self,
        template_name: str,
        button: str = "left",
        threshold: float = 0.8,
        scales: list[float] | None = None,
        offset_x: int = 0,
        offset_y: int = 0,
    ) -> ElementMatch | None:
        """Click on a detected UI element by template name."""
        if self._capture_fn is None:
            raise RuntimeError("No capture function set. Cannot detect elements.")

        screenshot = self._capture_fn()
        match = self._detector.find_element(screenshot, template_name, threshold, scales)

        if match is None:
            raise ValueError(f"Element '{template_name}' not found on screen (threshold={threshold})")

        target_x = match.x + offset_x
        target_y = match.y + offset_y
        target_x, target_y = self._clamp_to_screen(target_x, target_y)
        pyautogui.click(target_x, target_y, button=button)
        logger.info(f"Clicked element '{template_name}' at ({target_x},{target_y})")
        return match

    def _double_click_element(
        self,
        template_name: str,
        threshold: float = 0.8,
        offset_x: int = 0,
        offset_y: int = 0,
    ) -> ElementMatch | None:
        """Double-click on a detected UI element."""
        if self._capture_fn is None:
            raise RuntimeError("No capture function set.")

        screenshot = self._capture_fn()
        match = self._detector.find_element(screenshot, template_name, threshold)
        if match is None:
            raise ValueError(f"Element '{template_name}' not found")

        dx, dy = self._clamp_to_screen(match.x + offset_x, match.y + offset_y)
        pyautogui.doubleClick(dx, dy)
        return match

    def _drag_to_element(
        self,
        source_template: str,
        target_template: str,
        threshold: float = 0.8,
        duration: float = 0.5,
        source_offset_x: int = 0,
        source_offset_y: int = 0,
        target_offset_x: int = 0,
        target_offset_y: int = 0,
    ) -> tuple[ElementMatch, ElementMatch] | None:
        """Drag from one detected element to another."""
        if self._capture_fn is None:
            raise RuntimeError("No capture function set.")

        screenshot = self._capture_fn()
        source = self._detector.find_element(screenshot, source_template, threshold)
        target = self._detector.find_element(screenshot, target_template, threshold)

        if source is None:
            raise ValueError(f"Source element '{source_template}' not found")
        if target is None:
            raise ValueError(f"Target element '{target_template}' not found")

        sx, sy = self._clamp_to_screen(source.x + source_offset_x, source.y + source_offset_y)
        tx, ty = self._clamp_to_screen(target.x + target_offset_x, target.y + target_offset_y)

        pyautogui.moveTo(sx, sy, duration=0.2)
        pyautogui.mouseDown()
        try:
            pyautogui.moveTo(tx, ty, duration=duration)
        finally:
            pyautogui.mouseUp()

        logger.info(f"Dragged '{source_template}' ({sx},{sy}) -> '{target_template}' ({tx},{ty})")
        return (source, target)

    def _wait_for_element(
        self,
        template_name: str,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
        threshold: float = 0.8,
    ) -> ElementMatch | None:
        """Wait until a UI element appears on screen."""
        if self._capture_fn is None:
            raise RuntimeError("No capture function set.")

        start = time.time()
        while time.time() - start < timeout:
            screenshot = self._capture_fn()
            match = self._detector.find_element(screenshot, template_name, threshold)
            if match:
                logger.info(f"Element '{template_name}' appeared after {time.time()-start:.1f}s")
                return match
            time.sleep(poll_interval)

        raise TimeoutError(f"Element '{template_name}' did not appear within {timeout}s")

    # --- Precision Actions (v2) ---

    def _click_anchor(
        self,
        reference_template: str,
        offset_x: int = 0,
        offset_y: int = 0,
        button: str = "left",
        scales: list[float] | None = None,
    ) -> ElementMatch | None:
        """Click at an anchored position relative to a reference element."""
        if self._capture_fn is None:
            raise RuntimeError("No capture function set.")

        anchor = CoordinateAnchor(reference_template, offset_x, offset_y)
        screenshot = self._capture_fn()
        coords = anchor.resolve(self._detector, screenshot, scales)

        if coords is None:
            raise ValueError(f"Anchor reference '{reference_template}' not found")

        pyautogui.click(coords[0], coords[1], button=button)
        logger.info(f"Anchor click: ref='{reference_template}' + ({offset_x},{offset_y}) -> ({coords[0]},{coords[1]})")

        # Return a synthetic match for logging
        return ElementMatch(
            x=coords[0], y=coords[1], width=1, height=1,
            confidence=1.0, template_name=f"anchor:{reference_template}",
        )

    def _verified_click(
        self,
        x: int, y: int,
        verify_template: str = "",
        verify_absent: bool = False,
        button: str = "left",
        retries: int = 2,
    ):
        """Click and verify the result. Retries if verification fails."""
        for attempt in range(retries + 1):
            pyautogui.click(x, y, button=button)

            if not verify_template or self._capture_fn is None:
                return  # No verification requested

            time.sleep(0.3)
            screenshot = self._capture_fn()
            match = self._detector.find_element(screenshot, verify_template)

            if verify_absent:
                if match is None:
                    return  # Element is gone - success
            else:
                if match is not None:
                    return  # Element appeared - success

            if attempt < retries:
                logger.warning(f"Verified click failed (attempt {attempt+1}), retrying...")
                time.sleep(0.3)

        raise RuntimeError(
            f"Verified click at ({x},{y}) failed after {retries+1} attempts. "
            f"verify_template='{verify_template}', verify_absent={verify_absent}"
        )

    # --- UI Automation Actions (v2) ---

    def _focus_window(self, title: str) -> bool:
        """Focus/activate a window by title using UI Automation."""
        success = self._ui_auto.focus_window(title=title)
        if not success:
            # Fallback: Alt+Tab approach
            pyautogui.hotkey("alt", "tab")
            time.sleep(0.3)
        return success

    def _find_window(self, title: str) -> dict | None:
        """Find a window and return its info."""
        windows = self._ui_auto.find_windows(title=title)
        if windows:
            w = windows[0]
            return {"title": w.title, "rect": w.rect, "handle": w.handle}
        return None

    def _click_control(
        self,
        window_title: str,
        control_name: str = "",
        automation_id: str = "",
    ) -> bool:
        """Click a UI control by name/ID (not coordinates)."""
        return self._ui_auto.click_control(window_title, control_name, automation_id)

    # --- Utility ---

    def get_mouse_position(self) -> tuple[int, int]:
        pos = pyautogui.position()
        return (pos.x, pos.y)

    def get_screen_size(self) -> tuple[int, int]:
        size = pyautogui.size()
        return (size.width, size.height)

    @property
    def available_actions(self) -> list[str]:
        return sorted(self._handlers.keys())

    @property
    def action_log(self) -> list[ActionResult]:
        return list(self._action_log)

    def clear_log(self):
        self._action_log.clear()
