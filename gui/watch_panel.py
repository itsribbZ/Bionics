"""Bionics Watch Mode Panel — Sidebar widget integrated into BionicsWindow."""

import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

logger = logging.getLogger("bionics.watch_panel")


class WatchModePanel(QFrame):
    """Sidebar panel for Watch Mode controls and status, embedded in BionicsWindow."""

    watch_start = pyqtSignal(str)   # task description
    watch_stop = pyqtSignal()
    watch_pause = pyqtSignal()
    watch_resume = pyqtSignal()
    just_do_it = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._paused = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        header = QLabel("WATCH MODE")
        header.setObjectName("step_label")
        layout.addWidget(header)

        self._status_label = QLabel("OFF")
        self._status_label.setStyleSheet("color: #888; font: bold 12px;")
        layout.addWidget(self._status_label)

        # Task input
        task_label = QLabel("Task (what are you doing?):")
        task_label.setStyleSheet("color: #aaa; font: 10px;")
        layout.addWidget(task_label)

        self._task_input = QLineEdit()
        self._task_input.setPlaceholderText("e.g. Wire AnimBP locomotion blend")
        self._task_input.setStyleSheet(
            "background: #1a1a2e; color: #fff; border: 1px solid #333; "
            "padding: 6px; border-radius: 4px;"
        )
        layout.addWidget(self._task_input)

        # Controls
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_start = QPushButton("START (F8)")
        self._btn_start.setObjectName("btn_start")
        self._btn_start.clicked.connect(self._on_start)
        btn_row.addWidget(self._btn_start)

        self._btn_pause = QPushButton("PAUSE")
        self._btn_pause.setObjectName("btn_pause")
        self._btn_pause.setEnabled(False)
        self._btn_pause.clicked.connect(self._on_pause_toggle)
        btn_row.addWidget(self._btn_pause)

        self._btn_stop = QPushButton("STOP")
        self._btn_stop.setObjectName("btn_stop")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(self._btn_stop)

        layout.addLayout(btn_row)

        # Just Do It button
        self._btn_jdi = QPushButton("JUST DO IT — Hand off to Auto Mode")
        self._btn_jdi.setStyleSheet(
            "background: #0a1628; color: #56B4E9; border: 1px solid #56B4E9; "
            "padding: 8px; border-radius: 4px; font: bold 11px;"
        )
        self._btn_jdi.setEnabled(False)
        self._btn_jdi.clicked.connect(self.just_do_it.emit)
        layout.addWidget(self._btn_jdi)

        # Metrics display
        metrics_label = QLabel("METRICS")
        metrics_label.setObjectName("step_label")
        layout.addWidget(metrics_label)

        self._metrics_text = QTextEdit()
        self._metrics_text.setReadOnly(True)
        self._metrics_text.setMaximumHeight(120)
        self._metrics_text.setStyleSheet(
            "background: #0a0a1a; color: #56B4E9; font: 10px 'Consolas'; "
            "border: 1px solid #222;"
        )
        layout.addWidget(self._metrics_text)

        # Narration display
        narr_label = QLabel("NARRATION")
        narr_label.setObjectName("step_label")
        layout.addWidget(narr_label)

        self._narration_text = QTextEdit()
        self._narration_text.setReadOnly(True)
        self._narration_text.setMaximumHeight(80)
        self._narration_text.setStyleSheet(
            "background: #0a0a1a; color: #fff; font: 11px 'Segoe UI'; "
            "border: 1px solid #222;"
        )
        layout.addWidget(self._narration_text)

        layout.addStretch()

    def _on_start(self):
        task = self._task_input.text().strip()
        self.watch_start.emit(task)
        self._set_running(True)

    def _on_stop(self):
        self.watch_stop.emit()
        self._set_running(False)

    def _on_pause_toggle(self):
        if self._paused:
            self.watch_resume.emit()
            self._btn_pause.setText("PAUSE")
            self._status_label.setText("WATCHING")
            self._status_label.setStyleSheet("color: #009E73; font: bold 12px;")
        else:
            self.watch_pause.emit()
            self._btn_pause.setText("RESUME")
            self._status_label.setText("PAUSED")
            self._status_label.setStyleSheet("color: #F0E442; font: bold 12px;")
        self._paused = not self._paused

    def _set_running(self, running: bool):
        self._btn_start.setEnabled(not running)
        self._btn_pause.setEnabled(running)
        self._btn_stop.setEnabled(running)
        self._btn_jdi.setEnabled(running)
        self._task_input.setEnabled(not running)
        self._paused = False
        self._btn_pause.setText("PAUSE")
        if running:
            self._status_label.setText("WATCHING")
            self._status_label.setStyleSheet("color: #009E73; font: bold 12px;")
        else:
            self._status_label.setText("OFF")
            self._status_label.setStyleSheet("color: #888; font: bold 12px;")
            self._btn_jdi.setEnabled(False)

    def update_metrics(self, cycle, capture_ms, api_ms, ssim, tokens_in, tokens_out, annotations):
        self._metrics_text.setPlainText(
            f"Cycle: {cycle}\n"
            f"Capture: {capture_ms:.0f}ms  API: {api_ms:.0f}ms\n"
            f"SSIM change: {ssim:.4f}\n"
            f"Tokens: {tokens_in}in + {tokens_out}out\n"
            f"Annotations: {annotations}"
        )

    def update_narration(self, text: str):
        self._narration_text.setPlainText(text)

    def set_status(self, status: str, color: str = "#009E73"):
        self._status_label.setText(status)
        self._status_label.setStyleSheet(f"color: {color}; font: bold 12px;")
