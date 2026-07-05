"""Bionics Watch Mode — Standalone Desktop Application.

Launch directly:  python watch.py
Build to .exe:    python watch.py --build
Create shortcut:  python watch.py --shortcut

A lightweight, real-time UE5 guidance overlay. No Auto Mode, no plan loading —
just screen capture → Claude analysis → visual annotations + TTS narration.
"""

import argparse
import faulthandler
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

faulthandler.enable()

# --- Logging ---
LOG_DIR = Path(__file__).parent / "audit"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "watch_mode.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bionics.watch")


def check_api_key() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("\n[ERROR] ANTHROPIC_API_KEY not found.")
        print("Set it with:  setx ANTHROPIC_API_KEY \"sk-ant-your-key-here\"")
        print("Then restart your terminal.\n")
        return False
    return True


def build_exe():
    """Build standalone Watch Mode .exe via PyInstaller."""
    import subprocess
    print("Building Watch Mode .exe...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "BionicsWatch",
        "--icon", "NONE",
        "--add-data", "config.yaml;.",
        "--add-data", "gui/styles/bifrost.qss;gui/styles",
        "--hidden-import", "anthropic",
        "--hidden-import", "mss",
        "--hidden-import", "cv2",
        "--hidden-import", "PIL",
        "watch.py",
    ]
    subprocess.run(cmd, check=True)
    print("\nBuilt: dist/BionicsWatch.exe")


def create_shortcut():
    """Create a desktop shortcut for watch.py."""
    desktop = Path(os.path.expanduser("~/Desktop"))
    script_path = Path(__file__).resolve()

    # Create a .bat launcher
    bat_path = desktop / "Bionics Watch Mode.bat"
    bat_content = f"""@echo off
title Bionics Watch Mode
cd /d "{script_path.parent}"
python "{script_path}"
pause
"""
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"Shortcut created: {bat_path}")


def main():
    parser = argparse.ArgumentParser(description="Bionics Watch Mode")
    parser.add_argument("--build", action="store_true", help="Build standalone .exe")
    parser.add_argument("--shortcut", action="store_true", help="Create desktop shortcut")
    args = parser.parse_args()

    if args.build:
        build_exe()
        return
    if args.shortcut:
        create_shortcut()
        return

    if not check_api_key():
        sys.exit(1)

    # --- Launch GUI ---
    import traceback

    from PyQt6.QtWidgets import QApplication

    def exception_hook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error(f"Uncaught exception:\n{msg}")
        print(f"\n[CRASH]\n{msg}", file=sys.stderr)
    sys.excepthook = exception_hook

    logger.info("=" * 50)
    logger.info("BIONICS WATCH MODE — Standalone v0.3.0")
    logger.info("=" * 50)

    app = QApplication(sys.argv)
    app.setApplicationName("Bionics Watch Mode")
    app.setFont(QFont("Segoe UI", 10))

    window = WatchWindow()
    window.show()

    logger.info("Watch Mode GUI launched")
    sys.exit(app.exec())


# ---------------------------------------------------------------------------
# Standalone Watch Window
# ---------------------------------------------------------------------------

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.capture import ScreenCapture
from core.ue5_bridge import ConnectionStatus, UE5Bridge
from core.watch_engine import WatchEngine
from core.watch_schemas import WatchAnalysis, WatchMetrics
from gui.overlay import AnnotationOverlay, ControlPanel


class WatchSignals(QObject):
    """Thread-safe signals for Watch Mode."""
    annotation = pyqtSignal(object)   # QImage
    analysis = pyqtSignal(object)     # WatchAnalysis
    metrics = pyqtSignal(object)      # WatchMetrics
    narration = pyqtSignal(str)
    log = pyqtSignal(str)
    error = pyqtSignal(str)


# Bifrost dark theme for the standalone window
DARK_STYLE = """
QMainWindow { background: #0a0a1a; }
QLabel { color: #c0c0d0; }
QLabel#title {
    color: #56B4E9; font: bold 20px 'Segoe UI';
    padding: 4px 0;
}
QLabel#subtitle { color: #888; font: 11px; }
QLabel#status_on { color: #009E73; font: bold 14px; }
QLabel#status_off { color: #555; font: bold 14px; }
QLabel#status_paused { color: #F0E442; font: bold 14px; }
QLabel#status_error { color: #D55E00; font: bold 14px; }
QLineEdit {
    background: #12122a; color: #fff; border: 1px solid #333;
    padding: 8px 12px; border-radius: 6px; font: 12px;
}
QLineEdit:focus { border: 1px solid #56B4E9; }
QTextEdit {
    background: #08081a; color: #56B4E9; border: 1px solid #1a1a3a;
    font: 10px 'Consolas'; border-radius: 4px;
}
QPushButton {
    background: #12122a; color: #c0c0d0; border: 1px solid #333;
    padding: 8px 16px; border-radius: 6px; font: bold 11px;
}
QPushButton:hover { border: 1px solid #56B4E9; color: #56B4E9; }
QPushButton:pressed { background: #1a1a3a; }
QPushButton:disabled { color: #444; border-color: #222; }
QPushButton#btn_start {
    background: #0a2a1a; color: #009E73; border: 1px solid #009E73;
    font: bold 13px; padding: 10px 20px;
}
QPushButton#btn_start:hover { background: #0a3a2a; }
QPushButton#btn_stop {
    background: #2a0a0a; color: #D55E00; border: 1px solid #D55E00;
}
QPushButton#btn_stop:hover { background: #3a1a1a; }
QFrame#panel {
    background: #0e0e20; border: 1px solid #1a1a3a; border-radius: 8px;
}
QFrame#divider { background: #1a1a3a; max-height: 1px; }
QComboBox {
    background: #12122a; color: #c0c0d0; border: 1px solid #333;
    padding: 6px 10px; border-radius: 4px;
}
QComboBox QAbstractItemView { background: #12122a; color: #c0c0d0; }
"""


class WatchWindow(QMainWindow):
    """Standalone Watch Mode window — lightweight, focused, modifiable."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BIONICS — Watch Mode")
        self.setMinimumSize(480, 600)
        self.setGeometry(100, 100, 500, 700)
        self.setStyleSheet(DARK_STYLE)

        # Core components
        self._capture = ScreenCapture(
            audit_dir=str(Path(__file__).parent / "audit")
        )
        self._ue5 = UE5Bridge()
        self._overlay = AnnotationOverlay()
        self._control_panel = ControlPanel()
        self._engine = WatchEngine(
            capture=self._capture,
            ue5_bridge=self._ue5,
        )

        # Screen geometry
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self._engine.set_screen_geometry(
                geo.width(), geo.height(), screen.devicePixelRatio()
            )

        # Signals
        self._signals = WatchSignals()
        self._signals.annotation.connect(self._on_annotation)
        self._signals.analysis.connect(self._on_analysis)
        self._signals.metrics.connect(self._on_metrics)
        self._signals.narration.connect(self._on_narration)
        self._signals.log.connect(self._append_log)
        self._signals.error.connect(lambda msg: self._append_log(f"[ERROR] {msg}"))

        self._engine.set_callbacks(
            on_annotation=lambda img: self._signals.annotation.emit(img),
            on_analysis=lambda a: self._signals.analysis.emit(a),
            on_metrics=lambda m: self._signals.metrics.emit(m),
            on_log=lambda msg: self._signals.log.emit(msg),
            on_error=lambda msg: self._signals.error.emit(msg),
            on_narration=lambda txt: self._signals.narration.emit(txt),
        )
        self._engine.set_overlay_callbacks(
            hide_fn=lambda: self._overlay.hide_for_capture(),
            show_fn=lambda: self._overlay.show_after_capture(),
        )

        # ControlPanel signals
        self._control_panel.pause_clicked.connect(self._pause_toggle)
        self._control_panel.stop_clicked.connect(self._stop)
        self._control_panel.just_do_it_clicked.connect(self._just_do_it)

        # TTS
        self._tts = None
        self._paused = False

        self._build_ui()

        # Hotkeys
        QShortcut(QKeySequence("F8"), self, self._toggle)
        QShortcut(QKeySequence("Escape"), self, self._stop)

        # Status timer
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header
        title = QLabel("BIONICS WATCH MODE")
        title.setObjectName("title")
        layout.addWidget(title)

        sub = QLabel("Real-time UE5 guidance overlay  |  F8 Toggle  |  ESC Stop")
        sub.setObjectName("subtitle")
        layout.addWidget(sub)

        # Status
        status_row = QHBoxLayout()
        self._status = QLabel("OFF")
        self._status.setObjectName("status_off")
        status_row.addWidget(self._status)

        self._ue5_label = QLabel("UE5: —")
        self._ue5_label.setStyleSheet("color: #555; font: 10px;")
        status_row.addStretch()
        status_row.addWidget(self._ue5_label)
        layout.addLayout(status_row)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        layout.addWidget(div)

        # Task input
        task_label = QLabel("What are you working on?")
        task_label.setStyleSheet("color: #aaa; font: 11px;")
        layout.addWidget(task_label)

        self._task_input = QLineEdit()
        self._task_input.setPlaceholderText("e.g.  Wire AnimBP locomotion blend to final pose")
        self._task_input.returnPressed.connect(self._start)
        layout.addWidget(self._task_input)

        # Optional knowledge context
        ctx_label = QLabel("Knowledge context (optional — paste Bible notes, steps, etc.):")
        ctx_label.setStyleSheet("color: #666; font: 10px;")
        layout.addWidget(ctx_label)

        self._context_input = QTextEdit()
        self._context_input.setMaximumHeight(80)
        self._context_input.setPlaceholderText("Paste relevant context here for Claude to reference...")
        self._context_input.setStyleSheet(
            "background: #12122a; color: #aaa; border: 1px solid #222; "
            "font: 10px; border-radius: 4px; padding: 6px;"
        )
        layout.addWidget(self._context_input)

        # SSIM threshold control
        ssim_row = QHBoxLayout()
        ssim_label = QLabel("Sensitivity:")
        ssim_label.setStyleSheet("color: #666; font: 10px;")
        ssim_row.addWidget(ssim_label)

        self._sensitivity = QComboBox()
        self._sensitivity.addItems(["Low (cheaper)", "Medium", "High (responsive)"])
        self._sensitivity.setCurrentIndex(1)
        self._sensitivity.currentIndexChanged.connect(self._on_sensitivity_change)
        ssim_row.addWidget(self._sensitivity)
        ssim_row.addStretch()
        layout.addLayout(ssim_row)

        # Controls
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_start = QPushButton("START  (F8)")
        self._btn_start.setObjectName("btn_start")
        self._btn_start.clicked.connect(self._start)
        btn_row.addWidget(self._btn_start)

        self._btn_stop = QPushButton("STOP")
        self._btn_stop.setObjectName("btn_stop")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop)
        btn_row.addWidget(self._btn_stop)

        layout.addLayout(btn_row)

        # Divider
        div2 = QFrame()
        div2.setObjectName("divider")
        layout.addWidget(div2)

        # Narration display
        narr_label = QLabel("NARRATION")
        narr_label.setStyleSheet("color: #56B4E9; font: bold 11px;")
        layout.addWidget(narr_label)

        self._narration = QLabel("—")
        self._narration.setWordWrap(True)
        self._narration.setStyleSheet(
            "color: #fff; font: 12px; background: #0e0e20; "
            "padding: 10px; border-radius: 6px; border: 1px solid #1a1a3a;"
        )
        layout.addWidget(self._narration)

        # Metrics
        metrics_label = QLabel("METRICS")
        metrics_label.setStyleSheet("color: #56B4E9; font: bold 11px;")
        layout.addWidget(metrics_label)

        self._metrics = QTextEdit()
        self._metrics.setReadOnly(True)
        self._metrics.setMaximumHeight(100)
        layout.addWidget(self._metrics)

        # Log
        log_label = QLabel("LOG")
        log_label.setStyleSheet("color: #56B4E9; font: bold 11px;")
        layout.addWidget(log_label)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log, stretch=1)

    # === Actions ===

    def _toggle(self):
        if self._engine.state.is_active:
            self._stop()
        else:
            self._start()

    def _start(self):
        task = self._task_input.text().strip()
        if task:
            self._engine.set_task(task)

        ctx = self._context_input.toPlainText().strip()
        if ctx:
            self._engine.set_knowledge_context(ctx)

        self._engine.start()
        self._overlay.show()
        self._control_panel.show()
        self._control_panel.set_status("WATCHING", "#009E73")
        self._set_running(True)
        self._append_log(f"Started{': ' + task if task else ''}")

        # Check UE5
        try:
            status = self._ue5.check_connection()
            if status == ConnectionStatus.CONNECTED:
                self._ue5_label.setText("UE5: CONNECTED")
                self._ue5_label.setStyleSheet("color: #009E73; font: 10px;")
            else:
                self._ue5_label.setText(f"UE5: {status.name}")
                self._ue5_label.setStyleSheet("color: #555; font: 10px;")
        except Exception:
            self._ue5_label.setText("UE5: —")

    def _stop(self):
        self._engine.stop()
        self._overlay.clear()
        self._overlay.hide()
        self._control_panel.hide()
        self._set_running(False)
        self._paused = False
        self._append_log("Stopped")

    def _pause_toggle(self):
        if self._paused:
            self._engine.resume()
            self._control_panel.set_status("WATCHING", "#009E73")
            self._control_panel.set_paused(False)
            self._status.setText("WATCHING")
            self._status.setObjectName("status_on")
        else:
            self._engine.pause()
            self._control_panel.set_status("PAUSED", "#F0E442")
            self._control_panel.set_paused(True)
            self._status.setText("PAUSED")
            self._status.setObjectName("status_paused")
        self._paused = not self._paused
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _just_do_it(self):
        context = self._engine.get_handoff_context()
        self._stop()
        self._append_log(f"[HANDOFF] Context saved — {len(context.get('completed_steps', []))} steps done")
        self._append_log(f"  Task: {context.get('task', '—')}")
        QMessageBox.information(
            self, "Handoff Ready",
            f"Watch Mode context saved.\n\n"
            f"Task: {context.get('task', '—')}\n"
            f"Steps completed: {len(context.get('completed_steps', []))}\n\n"
            f"Open the full Bionics app to execute via Auto Mode."
        )

    def _on_sensitivity_change(self, idx):
        thresholds = [0.05, 0.02, 0.008]
        self._engine._ssim_threshold = thresholds[idx]
        labels = ["Low", "Medium", "High"]
        self._append_log(f"Sensitivity: {labels[idx]} (SSIM threshold={thresholds[idx]})")

    def _set_running(self, running: bool):
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        self._task_input.setEnabled(not running)
        self._context_input.setEnabled(not running)
        self._sensitivity.setEnabled(not running)
        if running:
            self._status.setText("WATCHING")
            self._status.setObjectName("status_on")
        else:
            self._status.setText("OFF")
            self._status.setObjectName("status_off")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    # === Signal Handlers ===

    def _on_annotation(self, qimage):
        self._overlay.set_annotation_image(qimage)

    def _on_analysis(self, analysis: WatchAnalysis):
        if analysis.narration:
            self._narration.setText(analysis.narration)
        if analysis.total_steps > 0:
            self._control_panel.set_status(
                f"Step {analysis.current_step}/{analysis.total_steps}", "#009E73"
            )

    def _on_metrics(self, m: WatchMetrics):
        self._metrics.setPlainText(
            f"Cycle {m.cycle}  |  Capture: {m.capture_ms:.0f}ms  |  "
            f"API: {m.api_latency_ms:.0f}ms\n"
            f"SSIM change: {m.ssim_vs_previous:.4f}  |  "
            f"Tokens: {m.tokens_in}+{m.tokens_out}  |  "
            f"Annotations: {m.annotations_count}"
        )

    def _on_narration(self, text: str):
        try:
            if self._tts is None:
                from PyQt6.QtTextToSpeech import QTextToSpeech
                self._tts = QTextToSpeech(self)
            self._tts.say(text)
        except ImportError:
            pass
        except Exception:
            pass

    def _append_log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refresh(self):
        pass  # Reserved for periodic updates

    def closeEvent(self, event):
        self._engine.stop()
        self._overlay.hide()
        self._overlay.close()
        self._control_panel.hide()
        self._control_panel.close()
        if self._tts:
            try:
                self._tts.stop()
            except Exception:
                pass
        self._capture.close()
        self._ue5.close()
        event.accept()


if __name__ == "__main__":
    main()
