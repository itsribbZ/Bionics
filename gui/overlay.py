"""Bionics Watch Mode Overlay — Transparent annotation window with Win32 click-through.

Architecture (from Blueprint v3):
- WS_EX_LAYERED + WS_EX_TRANSPARENT for per-pixel click-through
- QPainter on QImage in background thread (thread-safe)
- GUI thread draws pre-rendered QImage in paintEvent
- Self-capture avoidance: hide → sleep(50ms) → capture → show
- Re-apply Win32 click-through after every show()
- Normalized 0-1 coordinates from Claude → Qt logical pixels
"""

import ctypes
import ctypes.wintypes
import logging
import math

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from core.watch_schemas import Annotation, AnnotationType, WatchAnalysis

logger = logging.getLogger("bionics.overlay")

# Win32 constants
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

# Win32 functions
user32 = ctypes.windll.user32
SetWindowLongPtrW = user32.SetWindowLongPtrW
GetWindowLongPtrW = user32.GetWindowLongPtrW
SetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
SetWindowLongPtrW.restype = ctypes.c_long
GetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = ctypes.c_long

# Wong color-blind-safe palette
COLORS = {
    "sky_blue":   QColor(86, 180, 233),     # #56B4E9 — primary, spotlights
    "orange":     QColor(230, 159, 0),       # #E69F00 — warnings
    "green":      QColor(0, 158, 115),       # #009E73 — success, completed
    "vermillion": QColor(213, 94, 0),        # #D55E00 — errors, danger
    "yellow":     QColor(240, 228, 66),      # #F0E442 — caution
    "blue":       QColor(0, 114, 178),       # #0072B2 — info, links
    "purple":     QColor(204, 121, 167),     # #CC79A7 — experimental
    "white":      QColor(255, 255, 255),     # #FFFFFF — text
}

COLOR_MAP = {
    "#56B4E9": COLORS["sky_blue"],
    "#E69F00": COLORS["orange"],
    "#009E73": COLORS["green"],
    "#D55E00": COLORS["vermillion"],
    "#F0E442": COLORS["yellow"],
    "#0072B2": COLORS["blue"],
    "#CC79A7": COLORS["purple"],
    "#FFFFFF": COLORS["white"],
}


def _resolve_color(hex_str: str) -> QColor:
    if hex_str in COLOR_MAP:
        return COLOR_MAP[hex_str]
    return QColor(hex_str)


class AnnotationOverlay(QWidget):
    """Full-screen transparent overlay for Watch Mode annotations.

    Key properties:
    - Covers entire primary monitor
    - Click-through (Win32 WS_EX_TRANSPARENT) — all clicks pass to apps below
    - HWND_TOPMOST — always on top
    - Renders annotations from pre-built QImage (thread-safe)
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Frameless, transparent, always-on-top, no taskbar entry
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # No taskbar entry
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        # Cover primary screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.setGeometry(geo)
            self._screen_width = geo.width()
            self._screen_height = geo.height()
            self._dpr = screen.devicePixelRatio()
        else:
            self._screen_width = 1920
            self._screen_height = 1080
            self._dpr = 1.0

        # Current annotation image (rendered in bg thread, drawn in paintEvent)
        self._current_frame: QImage | None = None
        self._visible = False

        # Dim mask opacity (0.0 = no dim, 0.55 = standard)
        self._dim_opacity = 0.55

        logger.info(
            f"Overlay created: {self._screen_width}x{self._screen_height} "
            f"dpr={self._dpr}"
        )

    def show(self):
        """Show overlay and apply Win32 click-through flags."""
        super().show()
        self._apply_click_through()
        self._visible = True

    def _apply_click_through(self):
        """Apply Win32 extended styles for click-through transparency."""
        hwnd = int(self.winId())
        try:
            style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
            logger.debug("Win32 click-through applied")
        except Exception as e:
            logger.error(f"Failed to apply Win32 click-through: {e}")

    def hide_for_capture(self):
        """Hide overlay before screen capture (self-capture avoidance)."""
        if self._visible:
            self.hide()
            self._visible = False

    def show_after_capture(self):
        """Show overlay after screen capture, re-applying click-through."""
        if not self._visible:
            self.show()

    def set_annotation_image(self, img: QImage):
        """Set pre-rendered annotation image (called from GUI thread via signal)."""
        self._current_frame = img
        self.update()  # Schedule repaint

    def clear(self):
        """Clear all annotations."""
        self._current_frame = None
        self.update()

    def paintEvent(self, event):
        """Draw the pre-rendered annotation image. MUST run on GUI thread."""
        if self._current_frame is None:
            return
        painter = QPainter(self)
        painter.drawImage(0, 0, self._current_frame)
        painter.end()

    # --- Static rendering (called from WatchEngine bg thread on QImage) ---

    @staticmethod
    def render_annotations(
        analysis: WatchAnalysis,
        screen_width: int,
        screen_height: int,
        dpr: float = 1.0,
        dim_opacity: float = 0.55,
    ) -> QImage:
        """Render annotations to a QImage. Thread-safe (QPainter on QImage, not QWidget).

        Args:
            analysis: WatchAnalysis with annotations + step info
            screen_width: Logical screen width
            screen_height: Logical screen height
            dpr: Device pixel ratio
            dim_opacity: Background dim (0=none, 0.55=standard)

        Returns:
            QImage ready to be drawn in paintEvent
        """
        # Create image at physical resolution
        phys_w = int(screen_width * dpr)
        phys_h = int(screen_height * dpr)
        img = QImage(phys_w, phys_h, QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(0, 0, 0, 0))  # Fully transparent

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Draw dim mask with spotlight cutouts
        spotlights = [a for a in analysis.annotations if a.type == AnnotationType.SPOTLIGHT]
        if spotlights and dim_opacity > 0:
            _draw_dim_mask(painter, screen_width, screen_height, spotlights, dim_opacity)

        # Draw each annotation
        for ann in analysis.annotations:
            if ann.type == AnnotationType.SPOTLIGHT:
                _draw_spotlight(painter, ann, screen_width, screen_height)
            elif ann.type == AnnotationType.ARROW:
                _draw_arrow(painter, ann, screen_width, screen_height)
            elif ann.type == AnnotationType.LABEL:
                _draw_label(painter, ann, screen_width, screen_height)
            elif ann.type == AnnotationType.BOUNDING_BOX:
                _draw_bounding_box(painter, ann, screen_width, screen_height)
            elif ann.type == AnnotationType.PROGRESS:
                _draw_progress(painter, ann, analysis, screen_width, screen_height)

        # Draw step breadcrumb (top-left)
        if analysis.total_steps > 0:
            _draw_breadcrumb(painter, analysis, screen_width)

        # Draw narration bubble (bottom-center)
        if analysis.narration:
            _draw_narration(painter, analysis.narration, screen_width, screen_height)

        painter.end()
        return img


# ---------------------------------------------------------------------------
# Renderer functions (all operate on QPainter → QImage, thread-safe)
# ---------------------------------------------------------------------------

def _norm_to_px(nx: float, ny: float, w: int, h: int) -> tuple[float, float]:
    """Convert normalized 0-1 coordinates to pixel coordinates."""
    return nx * w, ny * h


def _draw_dim_mask(
    painter: QPainter, w: int, h: int,
    spotlights: list[Annotation], opacity: float,
):
    """Draw semi-transparent dim with circular cutouts for spotlights."""
    path = QPainterPath()
    path.addRect(QRectF(0, 0, w, h))

    for spot in spotlights:
        cx, cy = _norm_to_px(spot.x, spot.y, w, h)
        r = spot.radius * max(w, h)
        cutout = QPainterPath()
        cutout.addEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        path = path.subtracted(cutout)

    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, int(255 * opacity)))
    painter.drawPath(path)
    painter.restore()


def _draw_spotlight(painter: QPainter, ann: Annotation, w: int, h: int):
    """Draw a glowing spotlight ring around a point."""
    cx, cy = _norm_to_px(ann.x, ann.y, w, h)
    r = ann.radius * max(w, h)
    color = _resolve_color(ann.color)

    # Outer glow
    painter.save()
    glow_pen = QPen(QColor(color.red(), color.green(), color.blue(), 80))
    glow_pen.setWidthF(4.0)
    painter.setPen(glow_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(cx - r - 4, cy - r - 4, (r + 4) * 2, (r + 4) * 2))

    # Main ring
    ring_pen = QPen(color)
    ring_pen.setWidthF(2.5)
    painter.setPen(ring_pen)
    painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    painter.restore()

    # Label below spotlight
    if ann.text:
        _draw_text_bubble(painter, ann.text, cx, cy + r + 18, color, w)


def _draw_arrow(painter: QPainter, ann: Annotation, w: int, h: int):
    """Draw an arrow from (x,y) to (end_x, end_y) with an arrowhead."""
    sx, sy = _norm_to_px(ann.x, ann.y, w, h)
    ex, ey = _norm_to_px(ann.end_x, ann.end_y, w, h)
    color = _resolve_color(ann.color)

    painter.save()
    pen = QPen(color)
    pen.setWidthF(2.5)
    painter.setPen(pen)

    # Shaft
    painter.drawLine(QPoint(int(sx), int(sy)), QPoint(int(ex), int(ey)))

    # Arrowhead
    angle = math.atan2(ey - sy, ex - sx)
    arrow_size = 14.0
    p1_x = ex - arrow_size * math.cos(angle - math.pi / 6)
    p1_y = ey - arrow_size * math.sin(angle - math.pi / 6)
    p2_x = ex - arrow_size * math.cos(angle + math.pi / 6)
    p2_y = ey - arrow_size * math.sin(angle + math.pi / 6)

    path = QPainterPath()
    path.moveTo(ex, ey)
    path.lineTo(p1_x, p1_y)
    path.lineTo(p2_x, p2_y)
    path.closeSubpath()
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(path)
    painter.restore()

    if ann.text:
        mid_x = (sx + ex) / 2
        mid_y = (sy + ey) / 2
        _draw_text_bubble(painter, ann.text, mid_x, mid_y - 20, color, w)


def _draw_label(painter: QPainter, ann: Annotation, w: int, h: int):
    """Draw a text label at (x, y)."""
    px, py = _norm_to_px(ann.x, ann.y, w, h)
    color = _resolve_color(ann.color)
    _draw_text_bubble(painter, ann.text, px, py, color, w)


def _draw_bounding_box(painter: QPainter, ann: Annotation, w: int, h: int):
    """Draw a rectangle around a region."""
    px, py = _norm_to_px(ann.x, ann.y, w, h)
    bw = ann.width * w
    bh = ann.height * h
    color = _resolve_color(ann.color)

    painter.save()
    pen = QPen(color)
    pen.setWidthF(2.0)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(px, py, bw, bh))
    painter.restore()

    if ann.text:
        _draw_text_bubble(painter, ann.text, px + bw / 2, py - 20, color, w)


def _draw_progress(
    painter: QPainter, ann: Annotation,
    analysis: WatchAnalysis, w: int, h: int,
):
    """Draw a step progress indicator."""
    px, py = _norm_to_px(ann.x, ann.y, w, h)
    text = f"Step {analysis.current_step}/{analysis.total_steps}"
    if ann.text:
        text += f": {ann.text}"
    _draw_text_bubble(painter, text, px, py, COLORS["green"], w)


def _draw_breadcrumb(painter: QPainter, analysis: WatchAnalysis, w: int):
    """Draw step breadcrumb in top-left corner."""
    text = f"Step {analysis.current_step} / {analysis.total_steps}"
    margin = 16
    padding_h, padding_v = 12, 6

    painter.save()
    font = QFont("Segoe UI", 11, QFont.Weight.Bold)
    painter.setFont(font)
    fm = QFontMetrics(font)
    text_rect = fm.boundingRect(text)
    box_w = text_rect.width() + padding_h * 2
    box_h = text_rect.height() + padding_v * 2

    # Background pill
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 200))
    painter.drawRoundedRect(QRectF(margin, margin, box_w, box_h), 6, 6)

    # Text
    painter.setPen(COLORS["green"])
    painter.drawText(
        QRectF(margin, margin, box_w, box_h),
        Qt.AlignmentFlag.AlignCenter, text,
    )
    painter.restore()


def _draw_narration(painter: QPainter, text: str, w: int, h: int):
    """Draw narration text bubble at bottom-center."""
    max_text_width = int(w * 0.7)
    margin_bottom = 60
    padding_h, padding_v = 16, 10

    painter.save()
    font = QFont("Segoe UI", 12)
    painter.setFont(font)
    fm = QFontMetrics(font)

    # Word-wrap
    text_rect = fm.boundingRect(
        QRect(0, 0, max_text_width, 0),
        Qt.TextFlag.TextWordWrap, text,
    )
    box_w = min(text_rect.width() + padding_h * 2, max_text_width + padding_h * 2)
    box_h = text_rect.height() + padding_v * 2
    box_x = (w - box_w) / 2
    box_y = h - margin_bottom - box_h

    # Background
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 220))
    painter.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), 8, 8)

    # Border
    border_pen = QPen(COLORS["sky_blue"])
    border_pen.setWidthF(1.5)
    painter.setPen(border_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), 8, 8)

    # Text
    painter.setPen(COLORS["white"])
    painter.drawText(
        QRectF(box_x + padding_h, box_y + padding_v,
               box_w - padding_h * 2, box_h - padding_v * 2),
        Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter, text,
    )
    painter.restore()


def _draw_text_bubble(
    painter: QPainter, text: str, cx: float, cy: float,
    color: QColor, screen_width: int,
):
    """Draw a small text label with dark background at the given center position."""
    padding_h, padding_v = 8, 4

    painter.save()
    font = QFont("Segoe UI", 10)
    painter.setFont(font)
    fm = QFontMetrics(font)
    text_rect = fm.boundingRect(text)
    box_w = text_rect.width() + padding_h * 2
    box_h = text_rect.height() + padding_v * 2

    # Clamp to screen edges
    box_x = max(4, min(cx - box_w / 2, screen_width - box_w - 4))
    box_y = cy - box_h / 2

    # Background
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 200))
    painter.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), 4, 4)

    # Text
    painter.setPen(color)
    painter.drawText(
        QRectF(box_x, box_y, box_w, box_h),
        Qt.AlignmentFlag.AlignCenter, text,
    )
    painter.restore()


class ControlPanel(QWidget):
    """Small floating control panel for Watch Mode.

    Stays in corner, draggable, NOT click-through (user interacts with it).
    """

    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    just_do_it_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(280, 50)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Status indicator
        self._status = QLabel("WATCHING")
        self._status.setStyleSheet(
            "color: #009E73; font: bold 11px 'Segoe UI'; "
            "background: rgba(0,0,0,200); padding: 4px 8px; border-radius: 4px;"
        )
        layout.addWidget(self._status)

        # Pause/Resume
        self._btn_pause = QPushButton("||")
        self._btn_pause.setFixedSize(32, 32)
        self._btn_pause.setStyleSheet(
            "background: rgba(0,0,0,200); color: #F0E442; font: bold 14px; "
            "border: 1px solid #F0E442; border-radius: 4px;"
        )
        self._btn_pause.clicked.connect(self.pause_clicked.emit)
        layout.addWidget(self._btn_pause)

        # Stop
        btn_stop = QPushButton("X")
        btn_stop.setFixedSize(32, 32)
        btn_stop.setStyleSheet(
            "background: rgba(0,0,0,200); color: #D55E00; font: bold 14px; "
            "border: 1px solid #D55E00; border-radius: 4px;"
        )
        btn_stop.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(btn_stop)

        # Just Do It (Watch → Auto handoff)
        btn_jdi = QPushButton("DO IT")
        btn_jdi.setFixedSize(52, 32)
        btn_jdi.setStyleSheet(
            "background: rgba(0,0,0,200); color: #56B4E9; font: bold 10px; "
            "border: 1px solid #56B4E9; border-radius: 4px;"
        )
        btn_jdi.setToolTip("Hand off current step to Auto Mode")
        btn_jdi.clicked.connect(self.just_do_it_clicked.emit)
        layout.addWidget(btn_jdi)

        # Position: bottom-right of primary screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move(geo.width() - self.width() - 20, geo.height() - self.height() - 100)

        # Dragging state
        self._drag_pos = None

    def set_status(self, text: str, color: str = "#009E73"):
        self._status.setText(text)
        self._status.setStyleSheet(
            f"color: {color}; font: bold 11px 'Segoe UI'; "
            f"background: rgba(0,0,0,200); padding: 4px 8px; border-radius: 4px;"
        )

    def set_paused(self, paused: bool):
        self._btn_pause.setText(">" if paused else "||")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
