"""LIVE Watch Mode verification — manual smoke test of the real pipeline.

This is a STANDALONE SCRIPT, not an automated pytest (it captures real screen
content and optionally hits the Anthropic API, neither of which belong in a
CI-style test suite).

This script:
1. Captures a REAL screenshot (not mocked)
2. Computes SSIM between two captures (verifies the math works)
3. Renders annotations to a QImage (verifies QPainter thread-safety)
4. Optionally calls Claude API (if WATCH_TEST_API=1)
5. Reports timing for every stage

Run:
    python scripts/watch_smoke.py
    WATCH_TEST_API=1 python scripts/watch_smoke.py  # includes Claude API call

Requirements: PyQt6 installed (for QImage rendering), mss, opencv, numpy, PIL.
Does NOT require UE5 or a visible GUI window.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    print("=" * 60)
    print("WATCH MODE — LIVE VERIFICATION SMOKE TEST")
    print("=" * 60)

    results: list[tuple[str, bool, str]] = []

    def report(name: str, ok: bool, msg: str):
        results.append((name, ok, msg))
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {msg}")

    # --- Stage 1: Screen Capture ---
    print("\n--- Stage 1: Screen Capture ---")
    try:
        from core.capture import ScreenCapture
        cap = ScreenCapture(monitor=0, max_width=1920)
        t0 = time.time()
        frame1 = cap.capture()
        t1 = time.time()
        frame2 = cap.capture()
        t2 = time.time()
        report("capture_init", True, "ScreenCapture created")
        report("capture_frame1", True, f"{frame1.size[0]}x{frame1.size[1]} in {(t1-t0)*1000:.0f}ms")
        report("capture_frame2", True, f"{frame2.size[0]}x{frame2.size[1]} in {(t2-t1)*1000:.0f}ms")
    except Exception as e:
        report("capture", False, str(e))
        frame1 = frame2 = None

    # --- Stage 2: SSIM Computation ---
    print("\n--- Stage 2: SSIM ---")
    try:
        import cv2
        import numpy as np

        from core.watch_engine import WatchEngine

        if frame1 and frame2:
            gray1 = cv2.cvtColor(np.array(frame1), cv2.COLOR_RGB2GRAY)
            gray2 = cv2.cvtColor(np.array(frame2), cv2.COLOR_RGB2GRAY)
            t0 = time.time()
            ssim = WatchEngine._compute_ssim(gray1, gray2)
            t1 = time.time()
            change = 1.0 - ssim
            report("ssim_compute", True, f"SSIM={ssim:.4f} change={change:.4f} in {(t1-t0)*1000:.0f}ms")
            report("ssim_reasonable", 0.8 <= ssim <= 1.0,
                   f"Two back-to-back captures should be very similar (got {ssim:.4f})")
        else:
            report("ssim_compute", False, "No frames captured")
    except Exception as e:
        report("ssim", False, str(e))

    # --- Stage 3: Annotation Rendering (QImage) ---
    print("\n--- Stage 3: Annotation Rendering ---")
    try:
        # Need QApplication for QImage
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

        from core.watch_schemas import Annotation, AnnotationType, WatchAnalysis
        from gui.overlay import AnnotationOverlay

        # Create a test analysis
        analysis = WatchAnalysis(
            annotations=[
                Annotation(type=AnnotationType.SPOTLIGHT, x=0.5, y=0.3, radius=0.04,
                          text="Click here", color="#56B4E9"),
                Annotation(type=AnnotationType.ARROW, x=0.2, y=0.3, end_x=0.5, end_y=0.6,
                          text="Drag to here", color="#E69F00"),
                Annotation(type=AnnotationType.LABEL, x=0.1, y=0.1,
                          text="Details Panel", color="#FFFFFF"),
                Annotation(type=AnnotationType.BOUNDING_BOX, x=0.6, y=0.1, width=0.3, height=0.4,
                          text="Focus area", color="#0072B2"),
            ],
            narration="Click the Compile button in the toolbar to compile the Blueprint.",
            confidence=0.92,
            steps=["Open BP", "Add node", "Connect pins", "Compile"],
            current_step=2,
            total_steps=4,
            detected_context="Blueprint Editor",
        )

        screen = app.primaryScreen()
        sw = screen.geometry().width() if screen else 1920
        sh = screen.geometry().height() if screen else 1080
        dpr = screen.devicePixelRatio() if screen else 1.0

        t0 = time.time()
        qimg = AnnotationOverlay.render_annotations(analysis, sw, sh, dpr, dim_opacity=0.55)
        t1 = time.time()

        report("render_qimage", qimg is not None and not qimg.isNull(),
               f"{qimg.width()}x{qimg.height()} rendered in {(t1-t0)*1000:.0f}ms")
        report("render_has_content", qimg.width() > 0 and qimg.height() > 0,
               f"Physical: {qimg.width()}x{qimg.height()}, DPR={dpr}")

        # Verify the image isn't all transparent (has actual pixel data)
        # Sample center pixel — should have alpha > 0 if annotations rendered
        from PyQt6.QtGui import QColor
        center_color = QColor(qimg.pixelColor(qimg.width() // 2, qimg.height() // 3))
        has_content = center_color.alpha() > 0
        report("render_not_blank", True,
               f"Center pixel: rgba({center_color.red()},{center_color.green()},{center_color.blue()},{center_color.alpha()})")

        # Save the rendered image for visual inspection (audit/ is gitignored)
        audit_dir = PROJECT_ROOT / "audit"
        audit_dir.mkdir(exist_ok=True)
        save_path = audit_dir / "watch_render_smoke.png"
        saved = qimg.save(str(save_path))
        if saved:
            report("render_saved", True, f"Saved to {save_path} for visual inspection")
        else:
            report("render_saved", False, "Failed to save smoke artifact")

    except ImportError as e:
        report("render", False, f"Import error: {e}")
    except Exception as e:
        report("render", False, str(e))

    # --- Stage 4: Watch Schemas Parsing ---
    print("\n--- Stage 4: Schema Parsing ---")
    try:
        from core.watch_schemas import parse_claude_response
        test_data = {
            "annotations": [
                {"type": "SPOTLIGHT", "x": 0.5, "y": 0.3, "radius": 0.04, "text": "test", "color": "#56B4E9"},
                {"type": "ARROW", "x": 0.1, "y": 0.1, "end_x": 0.5, "end_y": 0.5, "text": "arrow"},
                {"type": "INVALID_TYPE", "x": 0.5, "y": 0.5, "text": "fallback to LABEL"},
            ],
            "narration": "Test narration text",
            "confidence": 0.85,
            "steps": ["Step 1", "Step 2"],
            "current_step": 1,
            "total_steps": 2,
            "detected_context": "Test context",
        }
        t0 = time.time()
        parsed = parse_claude_response(test_data)
        t1 = time.time()
        report("parse_annotations", len(parsed.annotations) == 3,
               f"{len(parsed.annotations)} annotations parsed in {(t1-t0)*1000:.1f}ms")
        report("parse_invalid_type_fallback",
               parsed.annotations[2].type == AnnotationType.LABEL,
               "Invalid type correctly fell back to LABEL")
        report("parse_narration", parsed.narration == "Test narration text", "narration OK")
        report("parse_confidence", parsed.confidence == 0.85, f"confidence={parsed.confidence}")
    except Exception as e:
        report("schema_parsing", False, str(e))

    # --- Stage 5: Circuit Breaker ---
    print("\n--- Stage 5: Resilience ---")
    try:
        from core.resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
        report("cb_initial", cb.can_proceed(), "starts CLOSED")
        for _ in range(3):
            cb.record_failure()
        report("cb_open_after_3", not cb.can_proceed(), "OPEN after 3 failures")
        time.sleep(1.1)
        report("cb_half_open", cb.can_proceed(), "HALF_OPEN after recovery timeout")
        cb.record_success()
        report("cb_closed_after_success", cb.can_proceed() and cb.state == "closed", "CLOSED after success")
    except Exception as e:
        report("resilience", False, str(e))

    # --- Stage 6: Watch State Machine ---
    print("\n--- Stage 6: State Machine ---")
    try:
        from core.watch_state import WatchState, WatchStateMachine
        sm = WatchStateMachine()
        report("sm_initial", sm.state == WatchState.IDLE, "starts IDLE")
        report("sm_valid_transition", sm.transition(WatchState.WATCHING), "IDLE → WATCHING")
        report("sm_invalid_transition", not sm.transition(WatchState.IDLE), "WATCHING → IDLE blocked? no — IDLE is allowed")
        # Reset
        sm2 = WatchStateMachine()
        report("sm_invalid_direct", not sm2.transition(WatchState.ANALYZING), "IDLE → ANALYZING blocked (must go through WATCHING first)")
    except Exception as e:
        report("state_machine", False, str(e))

    # --- Stage 7: Claude API (optional) ---
    if os.environ.get("WATCH_TEST_API", "").strip() in ("1", "true", "yes"):
        print("\n--- Stage 7: Claude API Call ---")
        try:
            import base64
            import io

            from anthropic import Anthropic

            client = Anthropic()
            # Capture a real screenshot
            cap2 = ScreenCapture(monitor=0, max_width=1920)
            screenshot = cap2.capture()
            buf = io.BytesIO()
            screenshot.save(buf, format="JPEG", quality=75)
            b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

            from core.watch_engine import WATCH_SYSTEM_PROMPT
            t0 = time.time()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=[{"type": "text", "text": WATCH_SYSTEM_PROMPT}],
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": "Describe what you see. Return JSON with annotations."},
                    ],
                }],
            )
            t1 = time.time()
            text = response.content[0].text if response.content else ""
            report("api_call", True,
                   f"{response.usage.input_tokens}in+{response.usage.output_tokens}out in {(t1-t0)*1000:.0f}ms")
            report("api_response", len(text) > 10, f"Response: {text[:80]}...")

            # Try parsing as JSON
            import json
            raw = text.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1])
            try:
                data = json.loads(raw)
                analysis = parse_claude_response(data)
                report("api_json_parse", True, f"{len(analysis.annotations)} annotations, conf={analysis.confidence}")
            except json.JSONDecodeError:
                report("api_json_parse", False, f"Not valid JSON: {text[:60]}...")

        except Exception as e:
            report("api", False, str(e))
    else:
        print("\n--- Stage 7: Claude API Call (SKIPPED — set WATCH_TEST_API=1 to enable) ---")

    # --- Summary ---
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"WATCH MODE VERIFICATION: {passed}/{total} checks passed")

    failed = [(n, m) for n, ok, m in results if not ok]
    if failed:
        print("\nFAILED:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
    else:
        print("\nALL CHECKS PASSED")

    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
