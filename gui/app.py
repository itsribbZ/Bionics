"""Bionics GUI - Bifrost-themed PyQt6 interface for the automation agent."""

import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("bionics.gui")

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.agent import AgentCore
from core.capture import ScreenCapture
from core.executor import Action, ActionExecutor, ActionResult
from core.planner import ExecutionPlan, PlanParser, PlanStep
from core.quiz_engine import QuizEngine
from core.safety import SafetyCheck, SafetyLayer, SafetyTier
from core.state import AgentState, StateMachine
from core.ue5_bridge import ConnectionStatus
from core.watch_engine import WatchEngine
from core.watch_schemas import WatchAnalysis, WatchMetrics
from core.watch_state import WatchState
from gui.overlay import AnnotationOverlay, ControlPanel
from gui.quiz_panel import QuizPanel
from gui.watch_panel import WatchModePanel


# Thread-safe signal bridge for GUI updates from agent thread
class SignalBridge(QObject):
    log_signal = pyqtSignal(str)
    action_signal = pyqtSignal(str, object, object)  # phase, Action, ActionResult
    step_signal = pyqtSignal(int, object)  # step_index, PlanStep
    error_signal = pyqtSignal(str)
    state_signal = pyqtSignal(object, object)  # old_state, new_state
    confirmation_request = pyqtSignal(object)  # SafetyCheck
    confirmation_response = pyqtSignal(bool)
    # Watch Mode signals
    watch_annotation = pyqtSignal(object)   # QImage
    watch_analysis = pyqtSignal(object)     # WatchAnalysis
    watch_metrics = pyqtSignal(object)      # WatchMetrics
    watch_narration = pyqtSignal(str)       # TTS text
    watch_log = pyqtSignal(str)
    watch_error = pyqtSignal(str)
    handoff_ready = pyqtSignal(object)  # ExecutionPlan from Watch→Auto handoff
    # Quiz Mode signals
    quiz_result = pyqtSignal(object)        # QuizResult
    quiz_error = pyqtSignal(str)
    quiz_log = pyqtSignal(str)


class BionicsWindow(QMainWindow):
    """Main Bionics application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BIONICS - AI Desktop Automation")
        self.setMinimumSize(1100, 750)
        self.setGeometry(100, 100, 1200, 800)

        # Core components
        self._state = StateMachine()
        self._safety = SafetyLayer()
        self._capture = ScreenCapture(
            audit_dir=str(Path(__file__).parent.parent / "audit")
        )
        self._executor = ActionExecutor()
        self._planner = PlanParser()
        self._agent = AgentCore(
            state_machine=self._state,
            safety=self._safety,
            capture=self._capture,
            executor=self._executor,
        )
        self._plan: ExecutionPlan | None = None

        # Signal bridge for thread-safe GUI updates
        self._signals = SignalBridge()
        self._signals.log_signal.connect(self._append_log)
        self._signals.action_signal.connect(self._on_action_update)
        self._signals.step_signal.connect(self._on_step_update)
        self._signals.error_signal.connect(self._on_error)
        self._signals.state_signal.connect(self._on_state_change)
        self._signals.confirmation_request.connect(self._handle_confirmation)

        # Wire callbacks
        self._agent.set_callbacks(
            on_action=lambda phase, action, result: self._signals.action_signal.emit(phase, action, result),
            on_step_change=lambda idx, step: self._signals.step_signal.emit(idx, step),
            on_error=lambda msg: self._signals.error_signal.emit(msg),
            on_log=lambda msg: self._signals.log_signal.emit(msg),
        )
        self._state.add_listener(
            lambda old, new: self._signals.state_signal.emit(old, new)
        )
        self._safety.set_confirmation_callback(self._safety_confirmation_sync)

        # Pending confirmation state (thread-safe via Event)
        self._confirm_event = threading.Event()
        self._confirm_result: bool = False

        # --- Watch Mode ---
        self._overlay = AnnotationOverlay()
        self._control_panel = ControlPanel()
        self._watch_engine = WatchEngine(
            capture=self._capture,
            ue5_bridge=self._agent.ue5_bridge,
        )

        # Set screen geometry for Watch Mode
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self._watch_engine.set_screen_geometry(
                geo.width(), geo.height(), screen.devicePixelRatio()
            )

        # Watch Mode signal wiring
        self._signals.watch_annotation.connect(self._on_watch_annotation)
        self._signals.watch_analysis.connect(self._on_watch_analysis)
        self._signals.watch_metrics.connect(self._on_watch_metrics)
        self._signals.watch_narration.connect(self._on_watch_narration)
        self._signals.watch_log.connect(self._append_log)
        self._signals.watch_error.connect(self._on_error)
        self._signals.handoff_ready.connect(self._apply_handoff_plan)

        self._watch_engine.set_callbacks(
            on_annotation=lambda img: self._signals.watch_annotation.emit(img),
            on_analysis=lambda a: self._signals.watch_analysis.emit(a),
            on_metrics=lambda m: self._signals.watch_metrics.emit(m),
            on_log=lambda msg: self._signals.watch_log.emit(msg),
            on_error=lambda msg: self._signals.watch_error.emit(msg),
            on_narration=lambda txt: self._signals.watch_narration.emit(txt),
        )

        self._watch_engine.set_overlay_callbacks(
            hide_fn=lambda: self._overlay.hide_for_capture(),
            show_fn=lambda: self._overlay.show_after_capture(),
        )

        # ControlPanel signals
        self._control_panel.pause_clicked.connect(self._watch_pause_toggle)
        self._control_panel.stop_clicked.connect(self._watch_stop)
        self._control_panel.just_do_it_clicked.connect(self._watch_just_do_it)

        # --- Quiz Mode ---
        self._quiz_engine = QuizEngine()
        self._signals.quiz_result.connect(self._on_quiz_result)
        self._signals.quiz_error.connect(self._on_quiz_error)
        self._signals.quiz_log.connect(self._append_log)
        self._quiz_engine.set_callbacks(
            on_log=lambda msg: self._signals.quiz_log.emit(msg),
            on_result=lambda r: self._signals.quiz_result.emit(r),
            on_error=lambda msg: self._signals.quiz_error.emit(msg),
        )

        # TTS engine (lazy init)
        self._tts = None

        # Build UI
        self._build_ui()
        self._load_stylesheet()
        self._setup_hotkeys()
        self._update_button_states()

        # Status refresh timer
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(250)

    def _load_stylesheet(self):
        qss_path = Path(__file__).parent / "styles" / "bifrost.qss"
        if qss_path.exists():
            self.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(12)

        # === Header ===
        header = QHBoxLayout()
        title = QLabel("BIONICS")
        title.setObjectName("title")
        header.addWidget(title)

        self._status_label = QLabel("IDLE")
        self._status_label.setObjectName("status_idle")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self._status_label)
        main_layout.addLayout(header)

        subtitle = QLabel("AI Desktop Automation Agent  |  F9 Pause  |  F10 Stop  |  F11 Resume  |  Ctrl+F10 Emergency Stop")
        subtitle.setObjectName("subtitle")
        main_layout.addWidget(subtitle)

        # === UE5 Connection Status ===
        ue5_bar = QHBoxLayout()
        self._ue5_status_label = QLabel("UE5: Not Checked")
        self._ue5_status_label.setObjectName("subtitle")
        ue5_bar.addWidget(self._ue5_status_label)

        self._btn_ue5_connect = QPushButton("Check UE5 Connection")
        self._btn_ue5_connect.setObjectName("btn_load")
        self._btn_ue5_connect.setMaximumWidth(200)
        self._btn_ue5_connect.clicked.connect(self._check_ue5)
        ue5_bar.addWidget(self._btn_ue5_connect)

        self._verification_label = QLabel("Verification: ON")
        self._verification_label.setObjectName("subtitle")
        ue5_bar.addWidget(self._verification_label)
        main_layout.addLayout(ue5_bar)

        # === Progress Bar ===
        self._progress = QProgressBar()
        self._progress.setMinimum(0)
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setFormat("No plan loaded")
        main_layout.addWidget(self._progress)

        # === Main Content Splitter ===
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel: Plan & Steps
        left_panel = QFrame()
        left_panel.setObjectName("panel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(8)

        plan_header = QHBoxLayout()
        plan_label = QLabel("EXECUTION PLAN")
        plan_label.setObjectName("step_label")
        plan_header.addWidget(plan_label)

        self._btn_load = QPushButton("Load Blueprint")
        self._btn_load.setObjectName("btn_load")
        self._btn_load.clicked.connect(self._load_blueprint)
        plan_header.addWidget(self._btn_load)
        left_layout.addLayout(plan_header)

        self._plan_name_label = QLabel("No plan loaded")
        self._plan_name_label.setWordWrap(True)
        left_layout.addWidget(self._plan_name_label)

        self._step_list = QListWidget()
        left_layout.addWidget(self._step_list, stretch=1)

        # Auto-approve checkbox
        self._auto_approve_cb = QCheckBox("Auto-approve moderate actions (batch mode)")
        self._auto_approve_cb.stateChanged.connect(
            lambda state: self._safety.set_auto_approve_moderate(state == Qt.CheckState.Checked.value)
        )
        left_layout.addWidget(self._auto_approve_cb)

        splitter.addWidget(left_panel)

        # Right panel: Action Log
        right_panel = QFrame()
        right_panel.setObjectName("panel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(8)

        log_label = QLabel("ACTION LOG")
        log_label.setObjectName("step_label")
        right_layout.addWidget(log_label)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        right_layout.addWidget(self._log_text, stretch=1)

        splitter.addWidget(right_panel)

        # Watch Mode panel (right side)
        self._watch_panel = WatchModePanel()
        self._watch_panel.watch_start.connect(self._watch_start)
        self._watch_panel.watch_stop.connect(self._watch_stop)
        self._watch_panel.watch_pause.connect(self._watch_engine.pause)
        self._watch_panel.watch_resume.connect(self._watch_engine.resume)
        self._watch_panel.just_do_it.connect(self._watch_just_do_it)
        splitter.addWidget(self._watch_panel)

        # Quiz Mode panel
        self._quiz_panel = QuizPanel()
        self._quiz_panel.scan_requested.connect(self._quiz_scan)
        splitter.addWidget(self._quiz_panel)

        splitter.setSizes([300, 400, 250, 250])
        main_layout.addWidget(splitter, stretch=1)

        # === Control Buttons ===
        controls = QHBoxLayout()
        controls.setSpacing(12)

        self._btn_start = QPushButton("START")
        self._btn_start.setObjectName("btn_start")
        self._btn_start.clicked.connect(self._start_agent)
        controls.addWidget(self._btn_start)

        self._btn_pause = QPushButton("PAUSE (F9)")
        self._btn_pause.setObjectName("btn_pause")
        self._btn_pause.clicked.connect(self._pause_agent)
        controls.addWidget(self._btn_pause)

        self._btn_resume = QPushButton("RESUME (F11)")
        self._btn_resume.setObjectName("btn_resume")
        self._btn_resume.clicked.connect(self._resume_agent)
        controls.addWidget(self._btn_resume)

        self._btn_stop = QPushButton("STOP (F10)")
        self._btn_stop.setObjectName("btn_stop")
        self._btn_stop.clicked.connect(self._stop_agent)
        controls.addWidget(self._btn_stop)

        self._btn_emergency = QPushButton("EMERGENCY STOP")
        self._btn_emergency.setObjectName("btn_stop")
        self._btn_emergency.clicked.connect(self._emergency_stop)
        self._btn_emergency.setToolTip("Ctrl+F10 - Immediately halt ALL agent activity")
        controls.addWidget(self._btn_emergency)

        # Undo controls
        self._btn_undo = QPushButton("UNDO LAST (Ctrl+Z)")
        self._btn_undo.setObjectName("btn_pause")
        self._btn_undo.clicked.connect(self._undo_last)
        self._btn_undo.setToolTip("Undo the last action (sends Ctrl+Z to the active app)")
        controls.addWidget(self._btn_undo)

        self._btn_undo_step = QPushButton("UNDO STEP")
        self._btn_undo_step.setObjectName("btn_pause")
        self._btn_undo_step.clicked.connect(self._undo_step)
        self._btn_undo_step.setToolTip("Undo all actions from the current step")
        controls.addWidget(self._btn_undo_step)

        main_layout.addLayout(controls)

    def _setup_hotkeys(self):
        """Register global hotkeys via keyboard shortcuts."""
        # In-app shortcuts (work when window is focused)
        QShortcut(QKeySequence("F9"), self, self._pause_agent)
        QShortcut(QKeySequence("F10"), self, self._stop_agent)
        QShortcut(QKeySequence("F11"), self, self._resume_agent)
        QShortcut(QKeySequence("Ctrl+F10"), self, self._emergency_stop)
        QShortcut(QKeySequence("Ctrl+Z"), self, self._undo_last)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._undo_step)
        QShortcut(QKeySequence("F7"), self, self._quiz_scan)
        QShortcut(QKeySequence("F8"), self, self._watch_toggle)

        self._append_log("Hotkeys: F7=Quiz, F8=Watch, F9=Pause, F10=Stop, F11=Resume, Ctrl+F10=Emergency (in-app only)")

    # === Actions ===

    def _check_ue5(self):
        status = self._agent.check_ue5_connection()
        status_map = {
            ConnectionStatus.CONNECTED: ("UE5: CONNECTED (API mode)", "#34d399"),
            ConnectionStatus.PLUGIN_MISSING: ("UE5: Plugin Missing", "#fbbf24"),
            ConnectionStatus.EDITOR_NOT_RUNNING: ("UE5: Not Running (vision mode)", "#94a3b8"),
            ConnectionStatus.DISCONNECTED: ("UE5: Disconnected", "#f87171"),
        }
        text, color = status_map.get(status, ("UE5: Unknown", "#94a3b8"))
        self._ue5_status_label.setText(text)
        self._ue5_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _load_blueprint(self):
        import traceback
        try:
            filepath, _ = QFileDialog.getOpenFileName(
                self,
                "Load Blueprint",
                str(Path(__file__).parent.parent / "plans"),
                "All Supported (*.pdf *.md *.txt *.json *.yaml *.yml);;PDF (*.pdf);;Markdown (*.md);;Text (*.txt);;JSON (*.json);;YAML (*.yaml *.yml)",
                options=QFileDialog.Option.DontUseNativeDialog,
            )
            if not filepath:
                return

            self._append_log(f"Loading blueprint: {filepath}")
            self._state.transition(AgentState.PLANNING)

            suffix = Path(filepath).suffix.lower()
            if suffix == ".json":
                self._plan = self._planner.load_plan(filepath)
                self._append_log("Loaded pre-parsed plan from JSON")
            else:
                self._append_log("Sending to Claude for step extraction...")
                self._plan = self._planner.parse_blueprint(filepath)

            self._agent.load_plan(self._plan)
            self._populate_step_list()
            self._state.transition(AgentState.REVIEWING)
            self._plan_name_label.setText(f"{self._plan.name}\n{self._plan.description}")
            self._update_progress()
            self._append_log(f"Plan ready: {self._plan.total_steps} steps to review")

            for w in self._plan.warnings:
                self._append_log(f"WARNING: {w}")

            save_path = Path(filepath).with_suffix(".bionics.json")
            self._planner.save_plan(self._plan, save_path)
            self._append_log(f"Plan saved to: {save_path}")

        except Exception as e:
            tb = traceback.format_exc()
            print(f"\n[LOAD BLUEPRINT CRASH]\n{tb}", flush=True)
            logger.error(f"Blueprint load error:\n{tb}")
            try:
                self._append_log(f"ERROR: {e}")
                self._state.transition(AgentState.ERROR, str(e))
            except Exception:
                pass

    def _populate_step_list(self):
        self._step_list.clear()
        if self._plan is None:
            return
        for step in self._plan.steps:
            icon = "[ ]"
            if step.is_destructive:
                icon = "[!]"
            item = QListWidgetItem(f"{icon} Step {step.index}: {step.description}")
            if step.is_destructive:
                item.setForeground(QColor("#f87171"))
            self._step_list.addItem(item)

    def _start_agent(self):
        if self._plan is None:
            QMessageBox.warning(self, "No Plan", "Load a blueprint first.")
            return

        if self._state.state == AgentState.REVIEWING:
            reply = QMessageBox.question(
                self,
                "Start Execution",
                f"Start executing plan '{self._plan.name}' ({self._plan.total_steps} steps)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._agent.start()

    def _pause_agent(self):
        self._agent.pause()

    def _resume_agent(self):
        self._agent.resume()

    def _stop_agent(self):
        self._agent.stop()

    def _emergency_stop(self):
        self._agent.emergency_stop()
        self._signals.log_signal.emit("*** EMERGENCY STOP ***")

    def _undo_last(self):
        """Undo the last action."""
        if self._state.state == AgentState.RUNNING:
            self._agent.pause()
        self._agent.undo.set_log_callback(lambda msg: self._append_log(msg))
        if self._agent.undo.undo_last():
            self._append_log(f"Undo stack: {self._agent.undo.undo_count} remaining")
        else:
            self._append_log("Nothing to undo")

    def _undo_step(self):
        """Undo all actions from the current/last step."""
        if self._state.state == AgentState.RUNNING:
            self._agent.pause()
        self._agent.undo.set_log_callback(lambda msg: self._append_log(msg))
        step_idx = self._state.current_step
        count = self._agent.undo.undo_step(step_idx)
        self._append_log(f"Undid {count} actions from step {step_idx + 1}")

    # === Safety Confirmation ===

    def _safety_confirmation_sync(self, check: SafetyCheck) -> bool:
        """Handle safety confirmation requests from the agent thread.

        This runs on the agent thread. Uses threading.Event for thread-safe handshake.
        """
        self._confirm_event.clear()
        self._signals.confirmation_request.emit(check)

        # Block until GUI thread responds via _confirm_event
        while not self._confirm_event.wait(timeout=0.1):
            if self._state.state in (AgentState.STOPPED, AgentState.ERROR):
                return False

        return self._confirm_result

    def _handle_confirmation(self, check: SafetyCheck):
        """Show confirmation dialog on the GUI thread."""
        tier_name = check.tier.name
        confirm_num = check.confirmations_received + 1
        total_confirms = check.confirmations_needed

        result = False
        if check.tier == SafetyTier.DESTRUCTIVE:
            if confirm_num == 1:
                reply = QMessageBox.warning(
                    self,
                    f"DESTRUCTIVE ACTION ({confirm_num}/{total_confirms})",
                    f"The agent wants to perform a DESTRUCTIVE action:\n\n"
                    f"Action: {check.action_name}\n"
                    f"Safety Tier: {tier_name}\n\n"
                    f"This action cannot be easily undone.\n"
                    f"Confirmation {confirm_num} of {total_confirms} required.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                result = (reply == QMessageBox.StandardButton.Yes)
            else:
                text, ok = QInputDialog.getText(
                    self,
                    f"FINAL CONFIRMATION ({confirm_num}/{total_confirms})",
                    f"Action: {check.action_name}\n\n"
                    f"Type DELETE to confirm this destructive action:",
                )
                result = (ok and text.strip().upper() == "DELETE")
        else:
            reply = QMessageBox.question(
                self,
                "Confirm Action",
                f"The agent wants to perform:\n\n"
                f"Action: {check.action_name}\n\n"
                f"Allow this action?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            result = (reply == QMessageBox.StandardButton.Yes)

        self._confirm_result = result
        self._confirm_event.set()  # Unblock the agent thread

    # === GUI Updates ===

    def _append_log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log_text.append(f"[{timestamp}] {msg}")
        # Auto-scroll to bottom
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_action_update(self, phase: str, action: Action, result: ActionResult | None):
        if phase == "executing":
            self._append_log(f"  > {action.name} {action.params}")
        elif phase == "completed" and result:
            status = "OK" if result.success else f"FAIL: {result.error}"
            self._append_log(f"  < {status} ({result.duration_ms:.0f}ms)")

    def _on_step_update(self, idx: int, step: PlanStep):
        if self._plan is None:
            return
        self._append_log(f"\n--- Step {step.index}/{self._plan.total_steps}: {step.description} ---")
        if idx < self._step_list.count():
            item = self._step_list.item(idx)
            item.setText(f"[>] Step {step.index}: {step.description}")
            item.setForeground(QColor("#818cf8"))
            self._step_list.setCurrentRow(idx)

        # Mark previous step complete in the list
        if idx > 0:
            prev_item = self._step_list.item(idx - 1)
            prev_step = self._plan.steps[idx - 1]
            prev_item.setText(f"[+] Step {prev_step.index}: {prev_step.description}")
            prev_item.setForeground(QColor("#34d399"))

        self._update_progress()

    def _on_error(self, msg: str):
        self._append_log(f"ERROR: {msg}")

    def _on_state_change(self, old_state: AgentState, new_state: AgentState):
        self._append_log(f"State: {old_state.name} -> {new_state.name}")
        self._update_status_label(new_state)
        self._update_button_states()

    def _update_status_label(self, state: AgentState):
        name_map = {
            AgentState.IDLE: ("IDLE", "status_idle"),
            AgentState.PLANNING: ("PARSING BLUEPRINT...", "status_running"),
            AgentState.REVIEWING: ("REVIEW PLAN", "status_paused"),
            AgentState.RUNNING: ("RUNNING", "status_running"),
            AgentState.PAUSED: ("PAUSED", "status_paused"),
            AgentState.STOPPED: ("STOPPED", "status_stopped"),
            AgentState.ERROR: ("ERROR", "status_error"),
        }
        text, obj_name = name_map.get(state, ("UNKNOWN", "status_idle"))
        self._status_label.setText(text)
        self._status_label.setObjectName(obj_name)
        # Force style refresh
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

    def _update_button_states(self):
        state = self._state.state
        self._btn_load.setEnabled(state in (AgentState.IDLE, AgentState.STOPPED, AgentState.ERROR))
        self._btn_start.setEnabled(state in (AgentState.REVIEWING, AgentState.STOPPED))
        self._btn_pause.setEnabled(state == AgentState.RUNNING)
        self._btn_resume.setEnabled(state == AgentState.PAUSED)
        self._btn_stop.setEnabled(state in (AgentState.RUNNING, AgentState.PAUSED))
        self._btn_emergency.setEnabled(state not in (AgentState.IDLE,))

    def _update_progress(self):
        if self._plan is None:
            self._progress.setValue(0)
            self._progress.setFormat("No plan loaded")
            return

        total = self._plan.total_steps
        completed = self._plan.completed_steps
        pct = int((completed / total) * 100) if total > 0 else 0
        self._progress.setValue(pct)
        self._progress.setFormat(f"Step {completed}/{total} ({pct}%)")

    def _refresh_status(self):
        """Periodic status refresh."""
        self._update_button_states()
        if self._plan and self._state.state == AgentState.RUNNING:
            self._update_progress()

    # === Watch Mode Actions ===

    def _watch_toggle(self):
        """Toggle Watch Mode on/off (F8)."""
        if self._watch_engine.state.is_active:
            self._watch_stop()
        else:
            task = self._watch_panel._task_input.text().strip()
            self._watch_start(task)

    def _watch_start(self, task: str = ""):
        """Start Watch Mode."""
        if task:
            self._watch_engine.set_task(task)
        self._watch_engine.start()
        self._overlay.show()
        self._control_panel.show()
        self._control_panel.set_status("WATCHING", "#009E73")
        self._watch_panel._set_running(True)
        self._append_log(f"[WATCH] Started{': ' + task if task else ''}")

    def _watch_stop(self):
        """Stop Watch Mode."""
        self._watch_engine.stop()
        self._overlay.clear()
        self._overlay.hide()
        self._control_panel.hide()
        self._watch_panel._set_running(False)
        self._append_log("[WATCH] Stopped")

    def _watch_pause_toggle(self):
        """Toggle Watch Mode pause from ControlPanel."""
        if self._watch_engine.state.state == WatchState.PAUSED:
            self._watch_engine.resume()
            self._control_panel.set_status("WATCHING", "#009E73")
            self._control_panel.set_paused(False)
        else:
            self._watch_engine.pause()
            self._control_panel.set_status("PAUSED", "#F0E442")
            self._control_panel.set_paused(True)

    def _watch_just_do_it(self):
        """Hand off current Watch Mode context to Auto Mode.

        Stops Watch Mode, generates an execution plan from the accumulated
        context, and loads it into Auto Mode for execution.
        """
        context = self._watch_engine.get_handoff_context()
        self._watch_stop()

        task = context.get("task", "")
        if not task:
            self._append_log("[WATCH→AUTO] No task set — nothing to hand off")
            self._append_log("  Tip: set a task via watch_task tool before pressing 'Just Do It'")
            return

        completed = context.get("completed_steps", [])
        knowledge = context.get("knowledge", "")

        self._append_log(f"[WATCH→AUTO] Handing off: {task}")
        if completed:
            self._append_log(f"  Completed: {', '.join(completed)}")
        self._append_log("  Generating Auto Mode plan...")

        # Build prompt enriched with Watch context
        prompt_parts = [f"Task: {task}"]
        if completed:
            prompt_parts.append(f"Steps already completed (skip these): {', '.join(completed)}")
        if knowledge:
            prompt_parts.append(f"Observed context from screen analysis:\n{knowledge}")
        prompt_parts.append("Execute the remaining work. Pick up where observation left off.")
        prompt = "\n\n".join(prompt_parts)

        # Generate plan in background thread (don't freeze GUI)
        def _generate():
            try:
                from core.auto_planner import AutoPlanner
                from core.paths import get_ue5_project
                proj = get_ue5_project()
                planner = AutoPlanner(
                    ue5_project_path=str(proj) if proj else "",
                )
                result = planner.generate_plan(prompt, divine=True)
                plan_data = result.get("plan", {})

                steps = []
                for s in plan_data.get("steps", []):
                    steps.append(PlanStep(
                        index=s.get("index", len(steps) + 1),
                        description=s.get("description", ""),
                        detailed_instructions=s.get("detailed_instructions", ""),
                        verification=s.get("verification", ""),
                        is_destructive=s.get("is_destructive", False),
                        requires_app=s.get("requires_app", ""),
                        category=s.get("category", "navigation"),
                    ))
                plan = ExecutionPlan(
                    name=plan_data.get("name", "Watch Handoff"),
                    description=plan_data.get("description", task),
                    steps=steps,
                    warnings=plan_data.get("warnings", []),
                )
                self._signals.handoff_ready.emit(plan)
            except Exception as e:
                logger.error(f"Handoff plan generation failed: {e}")
                self._signals.log_signal.emit(f"[WATCH→AUTO] Plan generation failed: {e}")

        threading.Thread(target=_generate, daemon=True).start()

    def _apply_handoff_plan(self, plan: ExecutionPlan):
        """Apply a handoff plan from Watch→Auto (runs on GUI thread via signal)."""
        self._plan = plan
        self._agent.load_plan(plan)
        self._populate_step_list()
        self._state.transition(AgentState.PLANNING)
        self._state.transition(AgentState.REVIEWING)
        self._plan_name_label.setText(f"{plan.name}\n{plan.description}")
        self._update_progress()
        self._append_log(f"[WATCH→AUTO] Plan ready: {len(plan.steps)} steps — review and start")
        for w in plan.warnings:
            self._append_log(f"  WARNING: {w}")

    # --- Watch Mode GUI Callbacks ---

    def _on_watch_annotation(self, qimage):
        """Receive rendered QImage from WatchEngine (via signal, on GUI thread)."""
        self._overlay.set_annotation_image(qimage)

    def _on_watch_analysis(self, analysis: WatchAnalysis):
        """Update watch panel with analysis results."""
        if analysis.narration:
            self._watch_panel.update_narration(analysis.narration)
        step_info = ""
        if analysis.total_steps > 0:
            step_info = f" (step {analysis.current_step}/{analysis.total_steps})"
        self._control_panel.set_status(
            f"WATCHING{step_info}", "#009E73"
        )

    def _on_watch_metrics(self, metrics: WatchMetrics):
        """Update watch panel metrics display."""
        self._watch_panel.update_metrics(
            metrics.cycle, metrics.capture_ms, metrics.api_latency_ms,
            metrics.ssim_vs_previous, metrics.tokens_in, metrics.tokens_out,
            metrics.annotations_count,
        )

    def _on_watch_narration(self, text: str):
        """Speak narration via QTextToSpeech (GUI thread)."""
        try:
            if self._tts is None:
                from PyQt6.QtTextToSpeech import QTextToSpeech
                self._tts = QTextToSpeech(self)
            self._tts.say(text)
        except ImportError:
            logger.debug("QTextToSpeech not available — narration skipped")
        except Exception as e:
            logger.debug(f"TTS error: {e}")

    # === Quiz Mode Actions ===

    def _quiz_scan(self):
        self._quiz_panel.prepare_scan()
        self._quiz_engine.scan()

    def _on_quiz_result(self, result):
        self._quiz_panel.set_result(result)

    def _on_quiz_error(self, msg: str):
        self._quiz_panel.set_error(msg)
        self._append_log(f"QUIZ ERROR: {msg}")

    def closeEvent(self, event):
        """Clean shutdown."""
        if self._state.state in (AgentState.RUNNING, AgentState.PAUSED):
            reply = QMessageBox.question(
                self,
                "Agent Running",
                "The agent is still running. Stop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        # Unblock any pending confirmation dialog first
        self._confirm_event.set()

        # Stop timer before widget destruction
        self._timer.stop()

        # Stop Watch Mode
        self._watch_engine.stop()
        self._overlay.hide()
        self._overlay.close()
        self._control_panel.hide()
        self._control_panel.close()
        if self._tts:
            try:
                self._tts.stop()
            except Exception:
                pass

        # Stop agent and wait for thread to exit
        self._agent.emergency_stop()
        if self._agent._thread and self._agent._thread.is_alive():
            self._agent._thread.join(timeout=3.0)

        self._capture.close()
        self._agent.ue5_bridge.close()
        event.accept()
