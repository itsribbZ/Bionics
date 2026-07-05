"""Bionics Screen Capture Engine - Fast screen capture with audit trail."""

import base64
import io
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import mss
from PIL import Image

logger = logging.getLogger("bionics.capture")


class ScreenCapture:
    """Captures screen content for Claude API vision and audit trail."""

    def __init__(
        self,
        monitor: int = 0,
        compression_quality: int = 75,
        audit_quality: int = 90,
        max_width: int = 1920,
        audit_dir: str = "audit",
    ):
        self._monitor = monitor
        self._compression_quality = compression_quality
        self._audit_quality = audit_quality
        self._max_width = max_width
        self._audit_dir = Path(audit_dir)
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._last_capture_time: float = 0
        self._capture_count: int = 0
        self._sct = None  # Lazy init to avoid COM conflicts with Qt
        self._lock = threading.Lock()  # Thread-safe for Watch Mode
        # Coordinate scaling: LLM sees resized image → returns coords in that space.
        # Native click requires multiplying back up. Track both sizes per capture.
        self._last_native_size: tuple[int, int] = (0, 0)
        self._last_captured_size: tuple[int, int] = (0, 0)

    def _get_sct(self):
        """Lazy-init mss to avoid DXGI/COM conflicts during Qt startup."""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def capture(self) -> Image.Image:
        """Capture the screen and return as PIL Image. Thread-safe."""
        with self._lock:
            sct = self._get_sct()
            monitors = sct.monitors
            if self._monitor == 0:
                mon = monitors[0]  # All monitors combined
            else:
                idx = min(self._monitor, len(monitors) - 1)
                mon = monitors[idx]

            raw = sct.grab(mon)
            img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)
            self._last_native_size = (img.width, img.height)

            # Resize if too wide (saves API tokens)
            if img.width > self._max_width:
                ratio = self._max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize((self._max_width, new_height), Image.LANCZOS)

            self._last_captured_size = (img.width, img.height)
            self._last_capture_time = time.time()
            self._capture_count += 1
            return img

    def capture_base64(self) -> str:
        """Capture screen and return as base64-encoded JPEG for Claude API."""
        img = self.capture()
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=self._compression_quality)
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    def image_to_base64(self, img: Image.Image) -> str:
        """Encode an already-captured PIL Image as base64 JPEG (avoids re-capture)."""
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=self._compression_quality)
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    def capture_region(self, x: int, y: int, width: int, height: int) -> Image.Image:
        """Capture a specific region of the screen."""
        with self._lock:
            region = {"left": x, "top": y, "width": width, "height": height}
            raw = self._get_sct().grab(region)
            return Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    def capture_region_base64(self, x: int, y: int, width: int, height: int) -> str:
        """Capture a specific region and return as base64 JPEG."""
        img = self.capture_region(x, y, width, height)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=self._compression_quality)
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    def save_audit(self, label: str = "", img: Image.Image | None = None) -> Path:
        """Save a screenshot to the audit trail."""
        if img is None:
            img = self.capture()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_label = label.replace(" ", "_").replace("/", "-")[:50] if label else "capture"
        filename = f"{timestamp}_{safe_label}.jpg"

        # Organize by date
        date_dir = self._audit_dir / datetime.now().strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)

        filepath = date_dir / filename
        img.save(str(filepath), format="JPEG", quality=self._audit_quality)
        logger.debug(f"Audit screenshot saved: {filepath}")
        return filepath

    def cleanup_audit(self, max_size_mb: int = 500):
        """Remove oldest audit files if total size exceeds limit."""
        total_size = sum(
            f.stat().st_size for f in self._audit_dir.rglob("*.jpg") if f.is_file()
        )
        max_bytes = max_size_mb * 1024 * 1024

        if total_size <= max_bytes:
            return

        # Sort by modification time, oldest first
        files = sorted(
            self._audit_dir.rglob("*.jpg"),
            key=lambda f: f.stat().st_mtime,
        )

        while total_size > max_bytes and files:
            f = files.pop(0)
            total_size -= f.stat().st_size
            f.unlink()
            logger.debug(f"Cleaned up audit file: {f}")

    @property
    def capture_count(self) -> int:
        return self._capture_count

    @property
    def scale_factor(self) -> float:
        """Ratio native_width / captured_width applied during last capture().

        Returns 1.0 until the first capture. When the screen is wider than
        max_width the captured image is downsampled — click coordinates from
        the LLM come back in the downsampled space and must be multiplied by
        this factor before being passed to pyautogui.
        """
        cw = self._last_captured_size[0]
        if cw <= 0:
            return 1.0
        nw = self._last_native_size[0]
        return nw / cw if cw else 1.0

    def unscale_coords(self, x: int, y: int) -> tuple[int, int]:
        """Convert LLM-space click coords back to native screen coords.

        Mirrors Anthropic's Computer Use scale-factor pattern: the model sees a
        downsampled screenshot (max_width) and returns coordinates in that
        space. Multiply by the native:captured ratio for pyautogui. No-op when
        the capture was already at native resolution.
        """
        if self._last_captured_size == (0, 0):
            return (int(x), int(y))
        cw, ch = self._last_captured_size
        nw, nh = self._last_native_size
        if (cw, ch) == (nw, nh):
            return (int(x), int(y))
        sx = nw / cw if cw else 1.0
        sy = nh / ch if ch else 1.0
        return (int(round(x * sx)), int(round(y * sy)))

    def close(self):
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass  # mss may not have fully initialized
