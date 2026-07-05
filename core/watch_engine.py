"""Bionics WatchEngine — Core poll loop for Watch Mode.

Pipeline (from Blueprint v3):
    1. health_check()              — verify mss, API, TTS
    2. prev_frame = current_frame
    3. overlay.hide()              — self-capture avoidance
    4. QApplication.processEvents()
    5. time.sleep(0.05)            — DWM compositor delay
    6. current_frame = capture()   — mss physical pixels (thread-safe)
    7. overlay.show() + apply_click_through()
    8. ssim = compute_ssim(prev, current)
    9. if ssim_change < threshold: continue  — SSIM gate
   10. diff_bboxes = compute_diff(prev, current)
   11. ue5_context = query_ue5_bridge()      — UE5 augmentation
   12. analysis = claude_stream(scaled, ue5_context)  — streaming API
   13. coords = map_to_logical(analysis)
   14. qimage = render_to_qimage(coords)     — thread-safe rendering
   15. emit annotation_signal(qimage)         — cross-thread signal
   16. tts.say(analysis.narration)            — QTextToSpeech (GUI thread)
   17. audit_log(analysis, metrics)           — JSON audit trail
"""

import base64
import io
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from core.capture import ScreenCapture
from core.resilience import CircuitBreaker, RetryConfig
from core.ue5_bridge import ConnectionStatus, UE5Bridge
from core.watch_schemas import (
    WatchAnalysis,
    WatchMetrics,
    parse_claude_response,
)
from core.watch_state import WatchState, WatchStateMachine

logger = logging.getLogger("bionics.watch_engine")

WATCH_SYSTEM_PROMPT = """You are Bionics Watch Mode — a UE5 Editor analysis system that guides users through complex Unreal Engine workflows by analyzing screenshots and returning structured annotation data.

<ue5_visual_conventions>
Blueprint Pin Colors: WHITE=execution, RED=bool, CYAN=int, GREEN=float, PINK=string, GOLD=vector, PURPLE=rotator, BLUE=object
Node Headers: BLUE=function, RED=event, GREEN=pure, GRAY=flow control
Panels: Viewport(center), Details(right), Outliner(upper-right), Content Browser(bottom), My Blueprint(left in BP editor)
Toolbar: Compile(left,checkmark), Save(near compile), Play(center,triangle)
</ue5_visual_conventions>

<output_format>
Return JSON with annotations using NORMALIZED 0-1 coordinates.
{"annotations":[...], "narration":"...", "confidence":0.0-1.0,
 "steps":[...], "current_step": 0, "total_steps": 0, "detected_context":"..."}
</output_format>

<annotation_types>
- SPOTLIGHT: {"type":"SPOTLIGHT","x":0.5,"y":0.3,"radius":0.04,"text":"Click here","color":"#56B4E9"}
- ARROW: {"type":"ARROW","x":0.2,"y":0.3,"end_x":0.5,"end_y":0.6,"text":"Drag to here","color":"#E69F00"}
- LABEL: {"type":"LABEL","x":0.5,"y":0.1,"text":"This is the Details panel","color":"#FFFFFF"}
- BOUNDING_BOX: {"type":"BOUNDING_BOX","x":0.1,"y":0.1,"width":0.3,"height":0.4,"text":"Focus area","color":"#0072B2"}
</annotation_types>

<rules>
1. Use normalized 0-1 coordinates (top-left = 0,0)
2. Maximum 6 annotations per response
3. Reference loaded knowledge when recommending approaches
4. If confidence < 0.7, say so and ask user to clarify
5. Narration: one natural sentence for text-to-speech
</rules>"""


class WatchEngine:
    """Background engine that captures, analyzes, and produces annotations."""

    def __init__(
        self,
        capture: ScreenCapture,
        ue5_bridge: UE5Bridge,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        poll_interval_ms: int = 1000,
        ssim_threshold: float = 0.05,
        max_tokens: int = 1024,
    ):
        self._capture = capture
        self._ue5 = ue5_bridge
        self._model = model
        self._poll_interval = poll_interval_ms / 1000.0
        self._ssim_threshold = ssim_threshold
        self._max_tokens = max_tokens

        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        from core.anthropic_client import get_shared_client
        self._client = get_shared_client(api_key)

        self._state = WatchStateMachine()
        self._circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        self._retry_config = RetryConfig(max_retries=3, base_delay=1.0)

        # Auto-register with the cross-process watch registry so that
        # CLI/MCP tools can discover and control this engine instance.
        try:
            from core.watch_registry import get_watch_registry
            self._watch_registry = get_watch_registry()
            self._watch_registry.register_engine(self)
            # Listener syncs state transitions to the registry for tools to observe
            def _sync_state(old, new):
                try:
                    self._watch_registry.update_status(new.name.lower())
                except Exception as e:
                    logger.warning(f"watch_engine: registry sync failed on {old.name}→{new.name} ({e})")
            self._state.add_listener(_sync_state)
        except ImportError:
            self._watch_registry = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._capture_lock = threading.Lock()

        # Previous frame for SSIM comparison
        self._prev_frame: np.ndarray | None = None
        self._cycle_count = 0

        # Task context (set by user or auto-detected)
        self._task_description: str = ""
        self._completed_steps: list[str] = []
        self._knowledge_context: str = ""

        # Audit
        self._audit_dir = Path(__file__).parent.parent / "audit" / "watch_sessions"
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Callbacks (connected to GUI signals)
        self._on_annotation: Callable | None = None  # (QImage) -> None
        self._on_analysis: Callable | None = None     # (WatchAnalysis) -> None
        self._on_metrics: Callable | None = None      # (WatchMetrics) -> None
        self._on_log: Callable | None = None          # (str) -> None
        self._on_error: Callable | None = None        # (str) -> None
        self._on_narration: Callable | None = None    # (str) -> None

        # Screen geometry (set before start)
        self._screen_width = 1920
        self._screen_height = 1080
        self._dpr = 1.0

        # Overlay hide/show callbacks (set by GUI)
        self._hide_overlay: Callable | None = None
        self._show_overlay: Callable | None = None

    @property
    def state(self) -> WatchStateMachine:
        return self._state

    def set_callbacks(
        self,
        on_annotation=None, on_analysis=None, on_metrics=None,
        on_log=None, on_error=None, on_narration=None,
    ):
        self._on_annotation = on_annotation
        self._on_analysis = on_analysis
        self._on_metrics = on_metrics
        self._on_log = on_log
        self._on_error = on_error
        self._on_narration = on_narration

    def set_overlay_callbacks(self, hide_fn, show_fn):
        self._hide_overlay = hide_fn
        self._show_overlay = show_fn

    def set_screen_geometry(self, width: int, height: int, dpr: float):
        self._screen_width = width
        self._screen_height = height
        self._dpr = dpr

    def set_task(self, description: str):
        self._task_description = description
        self._completed_steps = []
        self._log(f"Task set: {description}")

    def set_knowledge_context(self, context: str):
        self._knowledge_context = context

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("WatchEngine already running")
            return
        self._stop_event.clear()
        self._prev_frame = None
        self._cycle_count = 0
        self._state.transition(WatchState.WATCHING)
        self._thread = threading.Thread(
            target=self._run_loop, name="WatchEngine", daemon=True
        )
        self._thread.start()
        self._log("Watch Mode started")

    def stop(self):
        self._stop_event.set()
        self._state.transition(WatchState.IDLE)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._prev_frame = None
        self._log("Watch Mode stopped")

    def pause(self):
        if self._state.state in {WatchState.WATCHING, WatchState.ANALYZING, WatchState.ANNOTATING}:
            self._state.transition(WatchState.PAUSED)
            self._log("Watch Mode paused")

    def resume(self):
        if self._state.state == WatchState.PAUSED:
            self._state.transition(WatchState.WATCHING)
            self._log("Watch Mode resumed")

    # --- Main Loop ---

    def _run_loop(self):
        logger.info("WatchEngine loop started")
        while not self._stop_event.is_set():
            try:
                if self._state.state == WatchState.PAUSED:
                    time.sleep(0.1)
                    continue

                if self._state.state not in {WatchState.WATCHING, WatchState.ANNOTATING}:
                    time.sleep(0.1)
                    continue

                self._state.transition(WatchState.WATCHING)
                metrics = WatchMetrics(cycle=self._cycle_count)

                # --- Capture with self-capture avoidance ---
                t0 = time.time()

                if self._hide_overlay:
                    self._hide_overlay()
                time.sleep(0.05)  # DWM compositor delay

                with self._capture_lock:
                    frame = self._capture.capture()

                if self._show_overlay:
                    self._show_overlay()

                metrics.capture_ms = (time.time() - t0) * 1000

                # --- SSIM gate ---
                frame_cv = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
                frame_gray = cv2.cvtColor(frame_cv, cv2.COLOR_BGR2GRAY)

                if self._prev_frame is not None:
                    ssim_score = self._compute_ssim(self._prev_frame, frame_gray)
                    change = 1.0 - ssim_score
                    metrics.ssim_vs_previous = change

                    if change < self._ssim_threshold:
                        # Screen hasn't changed enough — skip API call
                        self._prev_frame = frame_gray
                        self._cycle_count += 1
                        time.sleep(self._poll_interval)
                        continue

                self._prev_frame = frame_gray

                # --- Circuit breaker check ---
                if not self._circuit.can_proceed():
                    self._log("Circuit breaker OPEN — waiting for recovery")
                    time.sleep(self._circuit.recovery_timeout / 2)
                    continue

                # --- Claude API call ---
                self._state.transition(WatchState.ANALYZING)
                t1 = time.time()

                analysis = self._analyze_frame(frame, metrics)
                metrics.api_latency_ms = (time.time() - t1) * 1000

                if analysis is None:
                    self._state.transition(WatchState.WATCHING)
                    time.sleep(self._poll_interval)
                    continue

                self._circuit.record_success()
                metrics.annotations_count = len(analysis.annotations)
                metrics.confidence = analysis.confidence

                # --- Render annotations to QImage ---
                self._state.transition(WatchState.ANNOTATING)

                # Guard PyQt6 import so WatchEngine can run headless (MCP / CI).
                # If GUI stack is unavailable, skip rendering — analysis still fires callbacks.
                try:
                    from gui.overlay import AnnotationOverlay
                    qimage = AnnotationOverlay.render_annotations(
                        analysis,
                        self._screen_width,
                        self._screen_height,
                        self._dpr,
                    )
                except ImportError:
                    self.logger.info(
                        "PyQt6/gui.overlay unavailable — running headless, skipping annotation render"
                    )
                    qimage = None

                if self._on_annotation:
                    self._on_annotation(qimage)

                if self._on_analysis:
                    self._on_analysis(analysis)

                # --- TTS narration ---
                if analysis.narration and self._on_narration:
                    metrics.tts_spoken = analysis.narration
                    self._on_narration(analysis.narration)

                # --- Track completed steps ---
                if analysis.steps and analysis.current_step > len(self._completed_steps):
                    for i in range(len(self._completed_steps), analysis.current_step):
                        if i < len(analysis.steps):
                            self._completed_steps.append(analysis.steps[i])

                # --- Audit ---
                self._audit_cycle(metrics)
                if self._on_metrics:
                    self._on_metrics(metrics)

                self._cycle_count += 1
                time.sleep(self._poll_interval)

            except Exception as e:
                logger.error(f"WatchEngine error: {e}", exc_info=True)
                self._circuit.record_failure()
                if self._on_error:
                    self._on_error(str(e))
                self._state.transition(WatchState.ERROR)
                time.sleep(2.0)
                if self._state.state == WatchState.ERROR:
                    self._state.transition(WatchState.WATCHING)

        logger.info("WatchEngine loop exited")

    # --- Claude API ---

    def _analyze_frame(
        self, frame: Image.Image, metrics: WatchMetrics
    ) -> WatchAnalysis | None:
        """Send screenshot to Claude and parse structured annotation response."""
        # Encode screenshot
        buf = io.BytesIO()
        frame.save(buf, format="JPEG", quality=75)
        screenshot_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

        # Build UE5 context
        ue5_context = ""
        try:
            if self._ue5.status == ConnectionStatus.CONNECTED:
                metrics.ue5_connected = True
                ue5_data = self._ue5.get_selected_actors()
                if ue5_data:
                    ue5_context = f"\n<ue5_state>{json.dumps(ue5_data)}</ue5_state>"
        except Exception as e:
            logger.debug(f"UE5 bridge query failed: {e}")

        # Build user message: image FIRST (Anthropic recommendation)
        task_section = ""
        if self._task_description:
            task_section = f"\n<task>{self._task_description}</task>"

        knowledge_section = ""
        if self._knowledge_context:
            knowledge_section = f"\n<knowledge>{self._knowledge_context}</knowledge>"

        steps_section = ""
        if self._completed_steps:
            steps_section = f"\n<prior_steps>{json.dumps(self._completed_steps)}</prior_steps>"

        text_content = (
            f"{ue5_context}{knowledge_section}{task_section}{steps_section}\n"
            f"What should the user do next?"
        )

        # System message with prompt caching — 1h TTL matches Bionics' long
        # overnight sessions (default 5min burns cache mid-session on low cadence).
        system_blocks = [
            {"type": "text", "text": WATCH_SYSTEM_PROMPT,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ]
        if self._knowledge_context:
            system_blocks.append({
                "type": "text",
                "text": f"<knowledge>{self._knowledge_context}</knowledge>",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            })

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_blocks,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": screenshot_b64,
                            },
                        },
                        {"type": "text", "text": text_content},
                    ],
                }],
            )

            metrics.tokens_in = response.usage.input_tokens
            metrics.tokens_out = response.usage.output_tokens

            # Parse JSON from response
            if not response.content or not hasattr(response.content[0], 'text'):
                self._log("[Watch] Empty response from Claude — skipping cycle")
                return None
            raw_text = response.content[0].text.strip()

            # Handle markdown code blocks
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                raw_text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

            data = json.loads(raw_text)
            analysis = parse_claude_response(data)
            self._log(
                f"[Cycle {self._cycle_count}] "
                f"{len(analysis.annotations)} annotations, "
                f"confidence={analysis.confidence:.2f}, "
                f"tokens={metrics.tokens_in}+{metrics.tokens_out}"
            )
            return analysis

        except json.JSONDecodeError as e:
            logger.warning(f"Claude returned invalid JSON: {e}")
            self._circuit.record_failure()
            metrics.errors.append(f"JSON parse error: {e}")
            return None
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            self._circuit.record_failure()
            metrics.errors.append(str(e))
            return None

    # --- SSIM ---

    @staticmethod
    def _compute_ssim(img1: np.ndarray, img2: np.ndarray) -> float:
        """Compute Structural Similarity Index between two grayscale images."""
        if img1.shape != img2.shape:
            img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

        C1 = (0.01 * 255) ** 2
        C2 = (0.03 * 255) ** 2

        img1_f = img1.astype(np.float64)
        img2_f = img2.astype(np.float64)

        mu1 = cv2.GaussianBlur(img1_f, (11, 11), 1.5)
        mu2 = cv2.GaussianBlur(img2_f, (11, 11), 1.5)

        mu1_sq = mu1 ** 2
        mu2_sq = mu2 ** 2
        mu1_mu2 = mu1 * mu2

        sigma1_sq = cv2.GaussianBlur(img1_f ** 2, (11, 11), 1.5) - mu1_sq
        sigma2_sq = cv2.GaussianBlur(img2_f ** 2, (11, 11), 1.5) - mu2_sq
        sigma12 = cv2.GaussianBlur(img1_f * img2_f, (11, 11), 1.5) - mu1_mu2

        numerator = (2 * mu1_mu2 + C1) * (2 * sigma12 + C2)
        denominator = (mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2)

        ssim_map = numerator / denominator
        return float(ssim_map.mean())

    # --- Audit ---

    def _audit_cycle(self, metrics: WatchMetrics):
        """Write one JSONL line per cycle to the audit trail."""
        try:
            log_file = self._audit_dir / f"{self._session_id}.jsonl"
            entry = {
                "session_id": self._session_id,
                "timestamp": datetime.now().isoformat(),
                "cycle": metrics.cycle,
                "capture_ms": round(metrics.capture_ms, 1),
                "api_latency_ms": round(metrics.api_latency_ms, 1),
                "model": self._model,
                "tokens_in": metrics.tokens_in,
                "tokens_out": metrics.tokens_out,
                "ssim_vs_previous": round(metrics.ssim_vs_previous, 4),
                "annotations_count": metrics.annotations_count,
                "confidence": round(metrics.confidence, 2),
                "tts_spoken": metrics.tts_spoken,
                "ue5_connected": metrics.ue5_connected,
                "errors": metrics.errors,
            }
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"Audit write failed: {e}")

    # --- Helpers ---

    def _log(self, msg: str):
        logger.info(msg)
        if self._on_log:
            self._on_log(msg)

    def get_handoff_context(self) -> dict:
        """Get current Watch Mode context for 'Just Do It' handoff to Auto Mode.

        Returns a dict that can be fed to AutoPlanner or AgentCore.
        """
        return {
            "task": self._task_description,
            "completed_steps": self._completed_steps,
            "knowledge": self._knowledge_context,
            "cycle_count": self._cycle_count,
        }
