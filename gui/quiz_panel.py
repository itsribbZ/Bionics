import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.quiz_engine import QuizQuestion, QuizResult

logger = logging.getLogger("bionics.quiz_panel")


class QuizPanel(QFrame):
    scan_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._questions: list[QuizQuestion] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel("QUIZ MODE")
        header.setObjectName("step_label")
        layout.addWidget(header)

        self._status_label = QLabel("Ready — press SCAN to capture screen")
        self._status_label.setStyleSheet("color: #888; font: bold 11px;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_scan = QPushButton("SCAN SCREEN (F7)")
        self._btn_scan.setStyleSheet(
            "background: #0a1628; color: #56B4E9; border: 1px solid #56B4E9; "
            "padding: 8px; border-radius: 4px; font: bold 11px;"
        )
        self._btn_scan.clicked.connect(self._on_scan)
        btn_row.addWidget(self._btn_scan)

        layout.addLayout(btn_row)

        q_label = QLabel("QUESTIONS")
        q_label.setObjectName("step_label")
        layout.addWidget(q_label)

        self._question_list = QListWidget()
        self._question_list.setStyleSheet(
            "background: #0a0a1a; color: #fff; font: 11px 'Segoe UI'; "
            "border: 1px solid #222;"
        )
        self._question_list.currentRowChanged.connect(self._on_question_selected)
        layout.addWidget(self._question_list, stretch=1)

        a_label = QLabel("ANSWER")
        a_label.setObjectName("step_label")
        layout.addWidget(a_label)

        self._answer_text = QTextEdit()
        self._answer_text.setReadOnly(True)
        self._answer_text.setStyleSheet(
            "background: #0a0a1a; color: #34d399; font: 12px 'Consolas'; "
            "border: 1px solid #222; padding: 8px;"
        )
        self._answer_text.setMinimumHeight(120)
        layout.addWidget(self._answer_text, stretch=1)

    def prepare_scan(self):
        self._btn_scan.setEnabled(False)
        self._status_label.setText("Scanning...")
        self._status_label.setStyleSheet("color: #56B4E9; font: bold 11px;")
        self._question_list.clear()
        self._answer_text.clear()
        self._questions.clear()

    def _on_scan(self):
        self.prepare_scan()
        self.scan_requested.emit()

    def set_result(self, result: QuizResult):
        self._questions = result.questions
        self._question_list.clear()
        self._answer_text.clear()

        for q in result.questions:
            label = f"Q{q.number}: {q.question[:80]}"
            if len(q.question) > 80:
                label += "..."
            item = QListWidgetItem(label)
            item.setForeground(QColor("#fff"))
            self._question_list.addItem(item)

        count = len(result.questions)
        self._status_label.setText(f"Found {count} question{'s' if count != 1 else ''} — click to reveal answers")
        self._status_label.setStyleSheet("color: #34d399; font: bold 11px;")
        self._btn_scan.setEnabled(True)

        if count > 0:
            self._question_list.setCurrentRow(0)

    def set_error(self, msg: str):
        self._status_label.setText(f"Error: {msg}")
        self._status_label.setStyleSheet("color: #f87171; font: bold 11px;")
        self._btn_scan.setEnabled(True)

    def _on_question_selected(self, row: int):
        if row < 0 or row >= len(self._questions):
            self._answer_text.clear()
            return

        q = self._questions[row]
        parts = []
        parts.append(f"Q{q.number}: {q.question}\n")

        if q.options:
            parts.append("Options:")
            for opt in q.options:
                parts.append(f"  {opt}")
            parts.append("")

        parts.append(f"ANSWER: {q.answer}\n")
        parts.append(f"Why: {q.explanation}")

        self._answer_text.setPlainText("\n".join(parts))

        item = self._question_list.item(row)
        if item:
            item.setForeground(QColor("#34d399"))
