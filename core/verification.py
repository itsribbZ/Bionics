"""Bionics Verification Layer - Confirms each action succeeded.

After every action, the agent takes a screenshot and verifies the result:
- Structural Similarity Index (SSIM) for change detection
- Element presence/absence checks
- Region-of-interest comparison
- Claude vision verification for complex state
"""

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

import cv2
import numpy as np
from PIL import Image

from core.precision import ElementDetector, pil_to_cv2

logger = logging.getLogger("bionics.verification")


class VerifyResult(Enum):
    PASS = auto()
    FAIL = auto()
    UNCERTAIN = auto()


@dataclass
class VerificationReport:
    """Report from a verification check."""
    result: VerifyResult
    confidence: float  # 0.0-1.0
    method: str
    details: str = ""
    before_hash: str = ""
    after_hash: str = ""
    change_score: float = 0.0  # How much the screen changed (0=identical, 1=completely different)


class ActionVerifier:
    """Verifies that actions produced the expected result."""

    def __init__(self, detector: ElementDetector | None = None):
        self._detector = detector or ElementDetector()
        self._history: deque[VerificationReport] = deque(maxlen=500)

    def verify_semantic(
        self,
        after: "Image.Image",
        expected_description: str,
        anthropic_client=None,
        model: str = "claude-sonnet-4-6",
    ) -> VerificationReport:
        """Semantic post-action vision verification via Claude — Phase 4 (2026-04-16).

        Where SSIM tells you PIXELS CHANGED but not WHETHER THE RIGHT THING HAPPENED,
        this method asks Claude vision a yes/no question grounded in the expected state.

        Example:
            verifier.verify_semantic(
                after=screenshot,
                expected_description="The 'Save' dialog should be CLOSED and the file "
                                     "name should show '*' indicating unsaved changes",
                anthropic_client=self._client,
            )

        Returns PASS if Claude confirms the expected state, FAIL if it contradicts,
        UNCERTAIN on API errors or ambiguous responses. Safe fallback — never raises.

        Args:
            after: PIL Image of the post-action screen
            expected_description: Natural language description of what should be true
            anthropic_client: Pre-initialized Anthropic() client (lazy-init if None)
            model: Claude model to use for vision verify (Sonnet 4.5 default)
        """
        import base64
        import io
        try:
            if anthropic_client is None:
                try:
                    from core.anthropic_client import get_shared_client
                    anthropic_client = get_shared_client()
                except Exception as e:
                    return VerificationReport(
                        result=VerifyResult.UNCERTAIN, confidence=0.0,
                        method="semantic_vision",
                        details=f"Anthropic client unavailable: {e}",
                    )

            # Normalize to PIL Image
            if isinstance(after, np.ndarray):
                after = Image.fromarray(after)

            # Encode image as base64 JPEG (smaller than PNG for API)
            buf = io.BytesIO()
            after.save(buf, format="JPEG", quality=75)
            img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            question = (
                "Look at this screenshot. Answer with exactly one word on the "
                "first line: YES, NO, or UNCERTAIN. On the second line, in one "
                "short sentence, explain why.\n\n"
                f"Expected state: {expected_description}\n\n"
                "Does the screenshot match the expected state?"
            )

            resp = anthropic_client.messages.create(
                model=model,
                max_tokens=200,
                temperature=0.0,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        }},
                        {"type": "text", "text": question},
                    ],
                }],
            )
            # Extract text (may have thinking blocks, filter to first text block)
            text = ""
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text = block.text.strip()
                    break
            first_line = text.split("\n", 1)[0].strip().upper() if text else ""
            explain = text.split("\n", 1)[1].strip() if "\n" in text else text

            if first_line.startswith("YES"):
                report = VerificationReport(
                    result=VerifyResult.PASS, confidence=0.9,
                    method="semantic_vision", details=f"YES — {explain[:200]}",
                )
            elif first_line.startswith("NO"):
                report = VerificationReport(
                    result=VerifyResult.FAIL, confidence=0.9,
                    method="semantic_vision", details=f"NO — {explain[:200]}",
                )
            else:
                report = VerificationReport(
                    result=VerifyResult.UNCERTAIN, confidence=0.5,
                    method="semantic_vision", details=f"UNCERTAIN — {explain[:200]}",
                )

            self._history.append(report)
            return report
        except Exception as e:
            logger.warning(f"verify_semantic failed: {e}")
            return VerificationReport(
                result=VerifyResult.UNCERTAIN, confidence=0.0,
                method="semantic_vision", details=f"Vision verify error: {e}",
            )

    def verify_screen_changed(
        self,
        before: Image.Image | np.ndarray,
        after: Image.Image | np.ndarray,
        min_change: float = 0.001,
        max_change: float = 0.95,
    ) -> VerificationReport:
        """Verify that the screen changed (but not too drastically).

        Uses SSIM to measure structural similarity.
        A small change confirms the action did something.
        A huge change might mean something went wrong.
        """
        before_cv = pil_to_cv2(before)
        after_cv = pil_to_cv2(after)

        # Resize to match if needed
        if before_cv.shape != after_cv.shape:
            after_cv = cv2.resize(after_cv, (before_cv.shape[1], before_cv.shape[0]))

        # Convert to grayscale for SSIM
        before_gray = cv2.cvtColor(before_cv, cv2.COLOR_BGR2GRAY)
        after_gray = cv2.cvtColor(after_cv, cv2.COLOR_BGR2GRAY)

        # Compute SSIM
        ssim_score = self._compute_ssim(before_gray, after_gray)
        change_score = min(1.0, max(0.0, 1.0 - ssim_score))

        if change_score < min_change:
            report = VerificationReport(
                result=VerifyResult.FAIL,
                confidence=0.8,
                method="ssim_change",
                details=f"Screen did not change (SSIM={ssim_score:.4f}, change={change_score:.4f})",
                change_score=change_score,
            )
        elif change_score > max_change:
            report = VerificationReport(
                result=VerifyResult.UNCERTAIN,
                confidence=0.5,
                method="ssim_change",
                details=f"Screen changed drastically (SSIM={ssim_score:.4f}, change={change_score:.4f})",
                change_score=change_score,
            )
        else:
            report = VerificationReport(
                result=VerifyResult.PASS,
                confidence=min(0.95, 0.5 + change_score * 2),
                method="ssim_change",
                details=f"Screen changed as expected (SSIM={ssim_score:.4f}, change={change_score:.4f})",
                change_score=change_score,
            )

        self._history.append(report)
        logger.info(f"Verify screen_changed: {report.result.name} - {report.details}")
        return report

    def verify_region_changed(
        self,
        before: Image.Image | np.ndarray,
        after: Image.Image | np.ndarray,
        x: int, y: int, width: int, height: int,
        min_change: float = 0.005,
    ) -> VerificationReport:
        """Verify that a specific screen region changed."""
        before_cv = pil_to_cv2(before)
        after_cv = pil_to_cv2(after)

        # Crop to region
        before_roi = before_cv[y:y + height, x:x + width]
        after_roi = after_cv[y:y + height, x:x + width]

        if before_roi.size == 0 or after_roi.size == 0:
            return VerificationReport(
                result=VerifyResult.UNCERTAIN,
                confidence=0.0,
                method="region_change",
                details="Invalid region coordinates",
            )

        # Resize if resolutions differ
        if before_roi.shape != after_roi.shape:
            after_roi = cv2.resize(after_roi, (before_roi.shape[1], before_roi.shape[0]))

        before_gray = cv2.cvtColor(before_roi, cv2.COLOR_BGR2GRAY)
        after_gray = cv2.cvtColor(after_roi, cv2.COLOR_BGR2GRAY)

        ssim_score = self._compute_ssim(before_gray, after_gray)
        change_score = min(1.0, max(0.0, 1.0 - ssim_score))

        result = VerifyResult.PASS if change_score >= min_change else VerifyResult.FAIL
        report = VerificationReport(
            result=result,
            confidence=0.85 if result == VerifyResult.PASS else 0.7,
            method="region_change",
            details=f"Region ({x},{y} {width}x{height}) change={change_score:.4f}",
            change_score=change_score,
        )

        self._history.append(report)
        logger.info(f"Verify region_changed: {report.result.name} - {report.details}")
        return report

    def verify_element_present(
        self,
        screenshot: Image.Image | np.ndarray,
        template_name: str,
        threshold: float = 0.8,
    ) -> VerificationReport:
        """Verify that a specific UI element is visible on screen."""
        match = self._detector.find_element(screenshot, template_name, threshold)

        if match:
            report = VerificationReport(
                result=VerifyResult.PASS,
                confidence=match.confidence,
                method="element_present",
                details=f"'{template_name}' found at ({match.x},{match.y}) conf={match.confidence:.3f}",
            )
        else:
            report = VerificationReport(
                result=VerifyResult.FAIL,
                confidence=0.8,
                method="element_present",
                details=f"'{template_name}' not found (threshold={threshold})",
            )

        self._history.append(report)
        logger.info(f"Verify element_present: {report.result.name} - {report.details}")
        return report

    def verify_element_absent(
        self,
        screenshot: Image.Image | np.ndarray,
        template_name: str,
        threshold: float = 0.8,
    ) -> VerificationReport:
        """Verify that a UI element is NOT visible (e.g., dialog closed)."""
        match = self._detector.find_element(screenshot, template_name, threshold)

        if match is None:
            report = VerificationReport(
                result=VerifyResult.PASS,
                confidence=0.85,
                method="element_absent",
                details=f"'{template_name}' correctly absent",
            )
        else:
            report = VerificationReport(
                result=VerifyResult.FAIL,
                confidence=match.confidence,
                method="element_absent",
                details=f"'{template_name}' still present at ({match.x},{match.y})",
            )

        self._history.append(report)
        logger.info(f"Verify element_absent: {report.result.name} - {report.details}")
        return report

    def verify_pixel_color(
        self,
        screenshot: Image.Image | np.ndarray,
        x: int, y: int,
        expected_bgr: tuple[int, int, int],
        tolerance: int = 30,
    ) -> VerificationReport:
        """Verify the color of a specific pixel (e.g., connection wire color)."""
        screen = pil_to_cv2(screenshot)
        if y >= screen.shape[0] or x >= screen.shape[1]:
            return VerificationReport(
                result=VerifyResult.UNCERTAIN,
                confidence=0.0,
                method="pixel_color",
                details=f"Coordinates ({x},{y}) out of bounds",
            )

        actual = screen[y, x]
        diff = sum(abs(int(a) - int(e)) for a, e in zip(actual, expected_bgr))

        if diff <= tolerance * 3:
            report = VerificationReport(
                result=VerifyResult.PASS,
                confidence=max(0.5, 1.0 - diff / (tolerance * 3 * 2)),
                method="pixel_color",
                details=f"Pixel ({x},{y}) color matches (diff={diff})",
            )
        else:
            report = VerificationReport(
                result=VerifyResult.FAIL,
                confidence=0.8,
                method="pixel_color",
                details=f"Pixel ({x},{y}) color mismatch: expected {expected_bgr}, got {tuple(actual)}, diff={diff}",
            )

        self._history.append(report)
        return report

    def compute_difference_map(
        self,
        before: Image.Image | np.ndarray,
        after: Image.Image | np.ndarray,
        threshold: int = 30,
    ) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
        """Compute a visual difference map and bounding boxes of changed regions.

        Returns (diff_image, list_of_change_bboxes).
        Useful for understanding where exactly the screen changed.
        """
        before_cv = pil_to_cv2(before)
        after_cv = pil_to_cv2(after)

        if before_cv.shape != after_cv.shape:
            after_cv = cv2.resize(after_cv, (before_cv.shape[1], before_cv.shape[0]))

        diff = cv2.absdiff(before_cv, after_cv)
        gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray_diff, threshold, 255, cv2.THRESH_BINARY)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        bboxes = []
        for c in contours:
            area = cv2.contourArea(c)
            if area > 50:
                bboxes.append(cv2.boundingRect(c))

        return diff, bboxes

    @staticmethod
    def _compute_ssim(img1: np.ndarray, img2: np.ndarray) -> float:
        """Compute Structural Similarity Index between two grayscale images."""
        C1 = 6.5025  # (0.01 * 255)^2
        C2 = 58.5225  # (0.03 * 255)^2

        img1 = img1.astype(np.float64)
        img2 = img2.astype(np.float64)

        mu1 = cv2.GaussianBlur(img1, (11, 11), 1.5)
        mu2 = cv2.GaussianBlur(img2, (11, 11), 1.5)

        mu1_sq = mu1 ** 2
        mu2_sq = mu2 ** 2
        mu1_mu2 = mu1 * mu2

        # Clamp variance to avoid negative values from floating-point subtraction
        sigma1_sq = np.maximum(cv2.GaussianBlur(img1 ** 2, (11, 11), 1.5) - mu1_sq, 0)
        sigma2_sq = np.maximum(cv2.GaussianBlur(img2 ** 2, (11, 11), 1.5) - mu2_sq, 0)
        sigma12 = cv2.GaussianBlur(img1 * img2, (11, 11), 1.5) - mu1_mu2

        ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / \
                   ((mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2))

        return float(np.clip(ssim_map.mean(), -1.0, 1.0))

    @property
    def history(self) -> list[VerificationReport]:
        return list(self._history)

    def pass_rate(self) -> float:
        if not self._history:
            return 0.0
        passed = sum(1 for r in self._history if r.result == VerifyResult.PASS)
        return passed / len(self._history)
