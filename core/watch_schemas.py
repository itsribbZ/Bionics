"""Bionics Watch Mode Data Models — structures for analysis results and annotations."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class AnnotationType(Enum):
    SPOTLIGHT = auto()     # Circular highlight on a UI element
    ARROW = auto()         # Arrow pointing from A to B
    LABEL = auto()         # Text label at a position
    BOUNDING_BOX = auto()  # Rectangle around a region
    PROGRESS = auto()      # Step progress indicator


@dataclass
class Annotation:
    """A single visual annotation to draw on the overlay."""
    type: AnnotationType
    # Normalized 0-1 coordinates (resolution-independent)
    x: float = 0.0
    y: float = 0.0
    # For arrows: end point
    end_x: float = 0.0
    end_y: float = 0.0
    # For bounding boxes: width/height (normalized)
    width: float = 0.0
    height: float = 0.0
    # Display
    text: str = ""
    color: str = "#56B4E9"  # Sky Blue (Wong palette default)
    radius: float = 0.03    # Normalized radius for spotlights


@dataclass
class WatchAnalysis:
    """Result from Claude's analysis of a screenshot."""
    annotations: list[Annotation] = field(default_factory=list)
    narration: str = ""           # TTS text
    confidence: float = 0.0       # 0.0-1.0
    detected_context: str = ""    # What UE5 context was detected
    steps: list[str] = field(default_factory=list)  # Suggested next steps
    current_step: int = 0
    total_steps: int = 0


@dataclass
class WatchMetrics:
    """Telemetry for a single Watch Mode cycle."""
    cycle: int = 0
    capture_ms: float = 0.0
    api_latency_ms: float = 0.0
    ssim_vs_previous: float = 0.0
    annotations_count: int = 0
    confidence: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tts_spoken: str = ""
    ue5_connected: bool = False
    errors: list[str] = field(default_factory=list)


def parse_claude_response(data: dict[str, Any]) -> WatchAnalysis:
    """Parse Claude's JSON response into a WatchAnalysis."""
    annotations = []
    for ann in data.get("annotations", []):
        ann_type_str = ann.get("type", "label").upper()
        try:
            ann_type = AnnotationType[ann_type_str]
        except KeyError:
            ann_type = AnnotationType.LABEL

        annotations.append(Annotation(
            type=ann_type,
            x=float(ann.get("x", 0)),
            y=float(ann.get("y", 0)),
            end_x=float(ann.get("end_x", 0)),
            end_y=float(ann.get("end_y", 0)),
            width=float(ann.get("width", 0)),
            height=float(ann.get("height", 0)),
            text=ann.get("text", ""),
            color=ann.get("color", "#56B4E9"),
            radius=float(ann.get("radius", 0.03)),
        ))

    return WatchAnalysis(
        annotations=annotations,
        narration=data.get("narration", ""),
        confidence=float(data.get("confidence", 0.0)),
        detected_context=data.get("detected_context", ""),
        steps=data.get("steps", []),
        current_step=int(data.get("current_step", 0)),
        total_steps=int(data.get("total_steps", 0)),
    )
