"""Bionics Precision Engine - Element detection, coordinate anchoring, template matching.

Replaces pure vision-based coordinate guessing with:
- OpenCV template matching (multi-scale)
- Color-based element detection
- Coordinate anchoring relative to reference elements
- Text region detection via morphological analysis
- Runtime template capture from screen
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger("bionics.precision")


@dataclass
class ElementMatch:
    """A detected UI element with precise coordinates."""
    x: int
    y: int
    width: int
    height: int
    confidence: float
    template_name: str = ""
    scale: float = 1.0

    @property
    def top_left(self) -> tuple[int, int]:
        return (self.x - self.width // 2, self.y - self.height // 2)

    @property
    def bottom_right(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x, self.y)


def pil_to_cv2(image: Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(image, Image.Image):
        return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    return image


class ElementDetector:
    """Detects UI elements using OpenCV template matching and structural analysis."""

    def __init__(self, template_dir: str | Path | None = None):
        self._template_dir = Path(template_dir) if template_dir else Path("templates/ui")
        self._template_dir.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, np.ndarray] = {}
        self._load_templates()

    def _load_templates(self):
        for f in self._template_dir.glob("*.png"):
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is not None:
                self._templates[f.stem] = img
                logger.info(f"Loaded template: {f.stem} ({img.shape[1]}x{img.shape[0]})")

    def find_element(
        self,
        screenshot: Image.Image | np.ndarray,
        template_name: str,
        threshold: float = 0.8,
        scales: list[float] | None = None,
    ) -> ElementMatch | None:
        """Find a single UI element via multi-scale template matching.

        Returns the best match above threshold, or None.
        """
        if template_name not in self._templates:
            logger.warning(f"Template not found: {template_name}")
            return None

        template = self._templates[template_name]
        screen = pil_to_cv2(screenshot)

        if scales is None:
            scales = [1.0]

        best_match: ElementMatch | None = None
        best_val = 0.0

        for scale in scales:
            if scale != 1.0:
                tw = int(template.shape[1] * scale)
                th = int(template.shape[0] * scale)
                if tw < 5 or th < 5:
                    continue
                scaled = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
            else:
                scaled = template

            if scaled.shape[0] > screen.shape[0] or scaled.shape[1] > screen.shape[1]:
                continue

            result = cv2.matchTemplate(screen, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > threshold and max_val > best_val:
                best_val = max_val
                th_px, tw_px = scaled.shape[:2]
                best_match = ElementMatch(
                    x=max_loc[0] + tw_px // 2,
                    y=max_loc[1] + th_px // 2,
                    width=tw_px,
                    height=th_px,
                    confidence=float(max_val),
                    template_name=template_name,
                    scale=scale,
                )

        if best_match:
            logger.info(
                f"Found '{template_name}' at ({best_match.x},{best_match.y}) "
                f"conf={best_match.confidence:.3f} scale={best_match.scale}"
            )
        return best_match

    @property
    def template_names(self) -> list[str]:
        return list(self._templates.keys())


class CoordinateAnchor:
    """Stores coordinates relative to a reference element, not absolute screen position.

    This makes coordinates survive window moves, resizes, and partial scrolls.
    """

    def __init__(
        self,
        reference_template: str,
        offset_x: int = 0,
        offset_y: int = 0,
        description: str = "",
        threshold: float = 0.8,
    ):
        self.reference_template = reference_template
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.description = description
        self.threshold = threshold

    def resolve(
        self,
        detector: ElementDetector,
        screenshot: Image.Image | np.ndarray,
        scales: list[float] | None = None,
    ) -> tuple[int, int] | None:
        """Resolve to absolute coordinates using current screen state."""
        match = detector.find_element(
            screenshot, self.reference_template,
            threshold=self.threshold, scales=scales,
        )
        if match is None:
            logger.warning(
                f"Anchor '{self.description}' failed: reference '{self.reference_template}' not found"
            )
            return None
        return (match.x + self.offset_x, match.y + self.offset_y)

    def __repr__(self) -> str:
        return (
            f"Anchor(ref='{self.reference_template}', "
            f"offset=({self.offset_x},{self.offset_y}), "
            f"desc='{self.description}')"
        )


