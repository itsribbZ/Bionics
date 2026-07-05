"""Bionics UI Automation - Windows Accessibility API integration via pywinauto.

Provides reliable window/control management that doesn't depend on pixel coordinates:
- Find windows by title, class, or process
- Enumerate child controls
- Click controls by name/automation_id (not coordinates)
- Get precise control bounding rectangles
- Focus/activate/minimize/maximize windows
- Graceful fallback when controls aren't accessible

Note: UE5 Editor uses Slate (custom UI framework) which has LIMITED
UI Automation exposure. Use this for:
- Window management (find, focus, resize UE5 window)
- Standard Windows dialogs (save, open, file picker)
- Menu items in some cases
For Slate-rendered content (Blueprint editor, AnimGraph), fall back to
vision-based detection (precision.py).
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("bionics.ui_automation")

# Lazy import: pywinauto uses comtypes which initializes COM on import.
# Importing at module level conflicts with Qt's COM usage (file dialogs, etc.)
# causing Windows fatal exception 0x80010108.
PYWINAUTO_AVAILABLE = None  # Determined on first use

def _ensure_pywinauto():
    """Lazy-import pywinauto to avoid COM conflicts with Qt at startup."""
    global PYWINAUTO_AVAILABLE
    if PYWINAUTO_AVAILABLE is not None:
        return PYWINAUTO_AVAILABLE
    try:
        import pywinauto  # noqa: F401
        PYWINAUTO_AVAILABLE = True
    except ImportError:
        PYWINAUTO_AVAILABLE = False
        logger.warning("pywinauto not available — UI Automation disabled")
    return PYWINAUTO_AVAILABLE


@dataclass
class WindowInfo:
    """Information about a detected window."""
    title: str
    class_name: str
    handle: int
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    visible: bool
    enabled: bool
    process_id: int = 0

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    @property
    def center(self) -> tuple[int, int]:
        return (
            (self.rect[0] + self.rect[2]) // 2,
            (self.rect[1] + self.rect[3]) // 2,
        )


@dataclass
class ControlInfo:
    """Information about a UI control element."""
    name: str
    control_type: str
    automation_id: str
    rect: tuple[int, int, int, int]
    visible: bool
    enabled: bool

    @property
    def center(self) -> tuple[int, int]:
        return (
            (self.rect[0] + self.rect[2]) // 2,
            (self.rect[1] + self.rect[3]) // 2,
        )


class UIAutomation:
    """Windows UI Automation wrapper for reliable window/control interaction."""

    def __init__(self):
        # Don't import pywinauto here — defer to first use
        self._initialized = False

    def _ensure_init(self) -> bool:
        if not self._initialized:
            self._initialized = True
            _ensure_pywinauto()
        return PYWINAUTO_AVAILABLE

    @property
    def available(self) -> bool:
        return self._ensure_init()

    def find_windows(self, title: str = "", class_name: str = "") -> list[WindowInfo]:
        """Find all windows matching criteria."""
        if not self._ensure_init():
            return []

        try:
            # Use win32 backend for enumeration (much faster than uia for broad searches)
            from pywinauto import Desktop
            desktop = Desktop(backend="win32")
            windows = desktop.windows()

            results = []
            for win in windows:
                try:
                    win_title = win.window_text()
                    win_class = win.class_name()

                    if title and title.lower() not in win_title.lower():
                        continue
                    if class_name and class_name.lower() not in win_class.lower():
                        continue

                    rect = win.rectangle()
                    results.append(WindowInfo(
                        title=win_title,
                        class_name=win_class,
                        handle=win.handle,
                        rect=(rect.left, rect.top, rect.right, rect.bottom),
                        visible=win.is_visible(),
                        enabled=win.is_enabled(),
                        process_id=win.process_id(),
                    ))
                except Exception:
                    continue

            return results
        except Exception as e:
            logger.error(f"Window search failed: {e}")
            return []

    def find_ue5_window(self) -> WindowInfo | None:
        """Find the Unreal Engine 5 Editor window."""
        candidates = self.find_windows(title="Unreal Editor")
        if not candidates:
            candidates = self.find_windows(title="Unreal Engine")
        if not candidates:
            candidates = self.find_windows(class_name="UnrealWindow")

        if candidates:
            # Return the largest window (main editor, not sub-windows)
            candidates.sort(key=lambda w: w.width * w.height, reverse=True)
            return candidates[0]
        return None

    def focus_window(self, title: str = "", handle: int = 0) -> bool:
        """Bring a window to the foreground."""
        if not self._ensure_init():
            return False

        try:
            if handle:
                from pywinauto import Application
                app = Application(backend="uia").connect(handle=handle)
                app.top_window().set_focus()
            elif title:
                from pywinauto import Application
                app = Application(backend="uia").connect(title_re=f".*{title}.*")
                app.top_window().set_focus()
            else:
                return False
            return True
        except Exception as e:
            logger.error(f"Focus window failed: {e}")
            return False

    def get_window_rect(self, title: str = "", handle: int = 0) -> tuple[int, int, int, int] | None:
        """Get the bounding rectangle of a window."""
        if not self._ensure_init():
            return None

        try:
            if handle:
                from pywinauto import Application
                app = Application(backend="uia").connect(handle=handle)
            elif title:
                from pywinauto import Application
                app = Application(backend="uia").connect(title_re=f".*{title}.*")
            else:
                return None

            rect = app.top_window().rectangle()
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception as e:
            logger.error(f"Get window rect failed: {e}")
            return None

    def find_control(
        self,
        window_title: str,
        control_name: str = "",
        control_type: str = "",
        automation_id: str = "",
    ) -> ControlInfo | None:
        """Find a specific control within a window."""
        if not self._ensure_init():
            return None

        try:
            app = Application(backend="uia").connect(title_re=f".*{window_title}.*")
            dlg = app.top_window()

            criteria = {}
            if control_name:
                criteria["title"] = control_name
            if control_type:
                criteria["control_type"] = control_type
            if automation_id:
                criteria["auto_id"] = automation_id

            if not criteria:
                return None

            ctrl = dlg.child_window(**criteria)
            if ctrl.exists():
                rect = ctrl.rectangle()
                return ControlInfo(
                    name=ctrl.window_text(),
                    control_type=ctrl.friendly_class_name(),
                    automation_id=getattr(getattr(ctrl, 'element_info', None), 'automation_id', '') or '',
                    rect=(rect.left, rect.top, rect.right, rect.bottom),
                    visible=ctrl.is_visible(),
                    enabled=ctrl.is_enabled(),
                )
        except Exception as e:
            logger.debug(f"Control search failed: {e}")
        return None

    def list_controls(self, window_title: str, max_depth: int = 3) -> list[ControlInfo]:
        """List all accessible controls in a window (for debugging/discovery)."""
        if not self._ensure_init():
            return []

        try:
            app = Application(backend="uia").connect(title_re=f".*{window_title}.*")
            dlg = app.top_window()

            controls = []
            self._walk_controls(dlg, controls, 0, max_depth)
            return controls
        except Exception as e:
            logger.error(f"List controls failed: {e}")
            return []

    def _walk_controls(self, element, results: list, depth: int, max_depth: int):
        """Recursively walk the UI Automation tree."""
        if depth > max_depth:
            return

        try:
            for child in element.children():
                try:
                    rect = child.rectangle()
                    results.append(ControlInfo(
                        name=child.window_text()[:100],
                        control_type=child.friendly_class_name(),
                        automation_id=getattr(child, 'automation_id', lambda: '')(),
                        rect=(rect.left, rect.top, rect.right, rect.bottom),
                        visible=child.is_visible(),
                        enabled=child.is_enabled(),
                    ))
                    self._walk_controls(child, results, depth + 1, max_depth)
                except Exception:
                    continue
        except Exception:
            pass

    def click_control(
        self,
        window_title: str,
        control_name: str = "",
        automation_id: str = "",
    ) -> bool:
        """Click a control by its name or automation ID (not coordinates)."""
        if not self._ensure_init():
            return False
        if not control_name and not automation_id:
            logger.error("click_control requires control_name or automation_id")
            return False

        try:
            app = Application(backend="uia").connect(title_re=f".*{window_title}.*")
            dlg = app.top_window()

            criteria = {}
            if control_name:
                criteria["title"] = control_name
            if automation_id:
                criteria["auto_id"] = automation_id

            ctrl = dlg.child_window(**criteria)
            ctrl.click_input()
            logger.info(f"Clicked control: {control_name or automation_id}")
            return True
        except Exception as e:
            logger.error(f"Click control failed: {e}")
            return False

    def type_in_control(
        self,
        window_title: str,
        text: str,
        control_name: str = "",
        automation_id: str = "",
    ) -> bool:
        """Type text into a specific control."""
        if not self._ensure_init():
            return False

        try:
            app = Application(backend="uia").connect(title_re=f".*{window_title}.*")
            dlg = app.top_window()

            criteria = {}
            if control_name:
                criteria["title"] = control_name
            if automation_id:
                criteria["auto_id"] = automation_id

            ctrl = dlg.child_window(**criteria)
            ctrl.set_edit_text(text)
            logger.info(f"Typed into control: {control_name or automation_id}")
            return True
        except Exception as e:
            logger.error(f"Type in control failed: {e}")
            return False

    def wait_for_window(self, title: str, timeout: float = 30.0) -> WindowInfo | None:
        """Wait for a window to appear."""
        start = time.time()
        while time.time() - start < timeout:
            windows = self.find_windows(title=title)
            if windows:
                return windows[0]
            time.sleep(0.5)
        return None
