"""Bionics AnimGraph Reference Capture — Screenshot + Template Builder.

Run this with UE5 open and an AnimBP loaded to capture:
1. Reference screenshots of every key UI state
2. Cropped element templates for OpenCV matching
3. Validation that all templates detect correctly

Usage (from Bionics root):
    python -m ue5_modules.animgraph.capture_references

Or import and use programmatically:
    from ue5_modules.animgraph.capture_references import AnimGraphCapture
    cap = AnimGraphCapture()
    cap.capture_all_states()
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("bionics.animgraph.capture")

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageGrab
    HAS_CAPTURE_DEPS = True
except ImportError:
    HAS_CAPTURE_DEPS = False
    logger.warning("Capture dependencies missing: pip install opencv-python pillow")

from ue5_modules.animgraph.element_templates import (
    COLOR_SIGNATURES,
    SCREENSHOT_STATES,
    TEMPLATE_DIR,
    TEMPLATE_SPECS,
    TemplateSpec,
)

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CaptureResult:
    """Result of a capture operation."""
    success: bool
    filepath: str = ""
    width: int = 0
    height: int = 0
    error: str = ""


class AnimGraphCapture:
    """Captures reference screenshots and element templates from UE5."""

    def __init__(self, template_dir: Path | None = None, screenshot_dir: Path | None = None):
        self._template_dir = template_dir or TEMPLATE_DIR
        self._screenshot_dir = screenshot_dir or SCREENSHOT_DIR
        self._template_dir.mkdir(parents=True, exist_ok=True)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def capture_screen(self) -> np.ndarray | None:
        """Capture the entire screen as an OpenCV image."""
        if not HAS_CAPTURE_DEPS:
            logger.error("Cannot capture: missing PIL/cv2")
            return None

        screenshot = ImageGrab.grab()
        return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    def capture_region(self, x: int, y: int, width: int, height: int) -> np.ndarray | None:
        """Capture a specific screen region."""
        if not HAS_CAPTURE_DEPS:
            return None

        screenshot = ImageGrab.grab(bbox=(x, y, x + width, y + height))
        return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    def save_screenshot(self, image: np.ndarray, filename: str) -> CaptureResult:
        """Save a screenshot to the screenshots directory."""
        filepath = self._screenshot_dir / filename
        success = cv2.imwrite(str(filepath), image)
        if success:
            h, w = image.shape[:2]
            return CaptureResult(True, str(filepath), w, h)
        return CaptureResult(False, error=f"Failed to write {filepath}")

    def save_template(self, image: np.ndarray, filename: str) -> CaptureResult:
        """Save a template image for OpenCV matching."""
        filepath = self._template_dir / filename
        success = cv2.imwrite(str(filepath), image)
        if success:
            h, w = image.shape[:2]
            return CaptureResult(True, str(filepath), w, h)
        return CaptureResult(False, error=f"Failed to write {filepath}")

    # ---- Interactive Capture Methods ----

    def capture_current_state(self, state_name: str) -> CaptureResult:
        """Capture the current screen as a named reference state.

        Call this when the editor is already in the desired state.
        """
        screen = self.capture_screen()
        if screen is None:
            return CaptureResult(False, error="Screen capture failed")

        # Find the matching state definition
        state = None
        for s in SCREENSHOT_STATES:
            if s.name == state_name:
                state = s
                break

        filename = state.filename if state else f"state_{state_name}.png"
        result = self.save_screenshot(screen, filename)
        if result.success:
            logger.info(f"Captured state '{state_name}': {result.filepath}")
        return result

    def capture_element_template(
        self,
        template_name: str,
        x: int, y: int, width: int, height: int,
    ) -> CaptureResult:
        """Capture a specific UI element as a template for matching.

        Provide the bounding box of the element on screen.
        """
        spec = TEMPLATE_SPECS.get(template_name)
        if not spec:
            return CaptureResult(False, error=f"Unknown template: {template_name}")

        region = self.capture_region(x, y, width, height)
        if region is None:
            return CaptureResult(False, error="Region capture failed")

        result = self.save_template(region, spec.filename)
        if result.success:
            logger.info(f"Captured template '{template_name}': {result.filepath}")
        return result

    def auto_crop_template(
        self,
        screenshot: np.ndarray,
        template_name: str,
        center_x: int, center_y: int,
    ) -> CaptureResult:
        """Auto-crop a template from a screenshot given center coordinates."""
        spec = TEMPLATE_SPECS.get(template_name)
        if not spec:
            return CaptureResult(False, error=f"Unknown template: {template_name}")

        w, h = spec.expected_size
        half_w, half_h = w // 2, h // 2

        # Add padding for multi-scale matching
        pad = 4
        x1 = max(0, center_x - half_w - pad)
        y1 = max(0, center_y - half_h - pad)
        x2 = min(screenshot.shape[1], center_x + half_w + pad)
        y2 = min(screenshot.shape[0], center_y + half_h + pad)

        crop = screenshot[y1:y2, x1:x2]
        if crop.size == 0:
            return CaptureResult(False, error="Crop produced empty image")

        return self.save_template(crop, spec.filename)

    # ---- Guided Capture Workflow ----

    def guided_capture_all(self, executor_fn=None, wait_fn=None):
        """Walk through all screenshot states and templates interactively.

        If executor_fn is provided, it can automate the setup steps.
        If wait_fn is provided, it waits for user confirmation between states.
        Otherwise, prints instructions and waits for Enter.
        """
        results = {"screenshots": [], "templates": [], "skipped": [], "errors": []}

        print("\n" + "=" * 60)
        print("  BIONICS AnimGraph Reference Capture")
        print("  " + "-" * 40)
        print("  Ensure UE5 is open with an AnimBP loaded.")
        print("  Follow the prompts to set up each state.")
        print("=" * 60 + "\n")

        # Phase 1: Capture screenshot states
        for state in SCREENSHOT_STATES:
            print(f"\n--- STATE: {state.name} ---")
            print(f"  Description: {state.description}")
            print("  Setup steps:")
            for i, step in enumerate(state.setup_steps, 1):
                print(f"    {i}. {step}")
            print(f"  Elements that should be visible: {state.elements_visible}")

            if wait_fn:
                ready = wait_fn(f"Set up '{state.name}' and press Enter when ready (s to skip): ")
            else:
                ready = input("Press Enter when ready (s to skip): ").strip().lower()

            if ready == "s":
                results["skipped"].append(state.name)
                print("  Skipped.")
                continue

            result = self.capture_current_state(state.name)
            if result.success:
                results["screenshots"].append({"state": state.name, "path": result.filepath})
                print(f"  Captured: {result.filepath} ({result.width}x{result.height})")

                # Try to auto-extract templates from this screenshot
                screen = cv2.imread(result.filepath)
                if screen is not None:
                    for elem in state.elements_visible:
                        if elem in TEMPLATE_SPECS and not (self._template_dir / TEMPLATE_SPECS[elem].filename).exists():
                            print(f"  Need template '{elem}' — provide center coordinates:")
                            try:
                                cx = int(input("    Center X: ").strip())
                                cy = int(input("    Center Y: ").strip())
                                t_result = self.auto_crop_template(screen, elem, cx, cy)
                                if t_result.success:
                                    results["templates"].append({"template": elem, "path": t_result.filepath})
                                    print(f"    Template saved: {t_result.filepath}")
                                else:
                                    results["errors"].append(f"{elem}: {t_result.error}")
                            except (ValueError, EOFError):
                                results["skipped"].append(f"template:{elem}")
                                print(f"    Skipped template '{elem}'")
            else:
                results["errors"].append(f"{state.name}: {result.error}")
                print(f"  ERROR: {result.error}")

        # Summary
        print("\n" + "=" * 60)
        print("  CAPTURE SUMMARY")
        print("=" * 60)
        print(f"  Screenshots: {len(results['screenshots'])}/{len(SCREENSHOT_STATES)}")
        print(f"  Templates:   {len(results['templates'])}/{len(TEMPLATE_SPECS)}")
        print(f"  Skipped:     {len(results['skipped'])}")
        print(f"  Errors:      {len(results['errors'])}")

        missing = self.get_missing_templates()
        if missing:
            print(f"\n  Missing templates ({len(missing)}):")
            for spec in missing:
                print(f"    - {spec.name}: {spec.description}")

        return results

    def get_missing_templates(self) -> list[TemplateSpec]:
        """Return template specs whose images haven't been captured yet."""
        return [
            spec for spec in TEMPLATE_SPECS.values()
            if not (self._template_dir / spec.filename).exists()
        ]

    def get_capture_status(self) -> dict:
        """Return current status of all captures."""
        screenshots_captured = [
            s.name for s in SCREENSHOT_STATES
            if (self._screenshot_dir / s.filename).exists()
        ]
        templates_captured = [
            name for name, spec in TEMPLATE_SPECS.items()
            if (self._template_dir / spec.filename).exists()
        ]

        return {
            "screenshots": {
                "captured": len(screenshots_captured),
                "total": len(SCREENSHOT_STATES),
                "names": screenshots_captured,
            },
            "templates": {
                "captured": len(templates_captured),
                "total": len(TEMPLATE_SPECS),
                "names": templates_captured,
            },
            "ready": (
                len(screenshots_captured) >= 3 and
                len(templates_captured) >= 5
            ),
        }

    # ---- Color-Based Auto-Detection ----

    def detect_elements_by_color(self, screenshot: np.ndarray) -> dict[str, list]:
        """Detect AnimGraph elements by color signature.

        Returns detected regions grouped by element type.
        Useful for auto-detecting pins, nodes, and buttons
        without template images.
        """
        if not HAS_CAPTURE_DEPS:
            return {}

        hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)
        detections = {}

        for name, sig in COLOR_SIGNATURES.items():
            lower = np.array(sig.hsv_lower)
            upper = np.array(sig.hsv_upper)
            mask = cv2.inRange(hsv, lower, upper)

            # Find contours
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            regions = []
            for contour in contours:
                area = cv2.contourArea(contour)
                if area >= sig.min_area:
                    x, y, w, h = cv2.boundingRect(contour)
                    cx, cy = x + w // 2, y + h // 2
                    regions.append({
                        "x": cx, "y": cy, "w": w, "h": h,
                        "area": area,
                    })

            if regions:
                detections[name] = regions

        return detections


# ---- CLI Entry Point ----

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    cap = AnimGraphCapture()

    if "--status" in sys.argv:
        status = cap.get_capture_status()
        print(f"Screenshots: {status['screenshots']['captured']}/{status['screenshots']['total']}")
        print(f"Templates: {status['templates']['captured']}/{status['templates']['total']}")
        print(f"Ready: {status['ready']}")
        if status['screenshots']['names']:
            print(f"Captured states: {', '.join(status['screenshots']['names'])}")
        if status['templates']['names']:
            print(f"Captured templates: {', '.join(status['templates']['names'])}")
    elif "--quick" in sys.argv:
        # Quick capture: just grab the current screen
        result = cap.capture_current_state("manual_capture")
        print(f"Captured: {result.filepath}" if result.success else f"Failed: {result.error}")
    elif "--detect" in sys.argv:
        # Color-based detection test
        screen = cap.capture_screen()
        if screen is not None:
            detections = cap.detect_elements_by_color(screen)
            for elem, regions in detections.items():
                print(f"{elem}: {len(regions)} detected")
                for r in regions[:3]:
                    print(f"  ({r['x']}, {r['y']}) {r['w']}x{r['h']} area={r['area']}")
    else:
        # Full guided capture
        cap.guided_capture_all()
