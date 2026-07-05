"""Async task manager for long-running tool calls.

MCP 2025-11-25 introduced a Tasks facility: slow tools return a `taskId`
immediately, the client polls `tasks/status` / `tasks/result`, and the server
runs the work in the background. That lets agents kick off 30-second UE5 hot
reloads or multi-minute batch retargets without holding an MCP connection
open and timing out.

This module provides the plumbing:

- `TaskManager` — singleton, thread-pool backed, persists Task records in memory
- `Task` — dataclass with id + status + args + result + timing
- `TaskStatus` — PENDING | RUNNING | COMPLETED | FAILED | CANCELLED

Usage:

    mgr = get_task_manager()
    task_id = mgr.submit("ue5_live_coding", {})        # returns immediately

    status = mgr.get_status(task_id)                   # RUNNING
    # ...later...
    task = mgr.get_task(task_id)                       # full record
    if task.status == TaskStatus.COMPLETED:
        use(task.result)

Tools wired to this lifecycle live in `bionics_tools/task_tools.py`.

Concurrency: the executor uses a small fixed pool (default 4 workers). UE5
hot-reload can block on file locks, so a wide pool doesn't help. Callers
can tune via `TaskManager(max_workers=N)`.

Cancellation: only tasks that haven't started yet honor `cancel()` — running
tasks keep going (Python doesn't support preemption). Status reflects this.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum

from core.bridge import SafetyTier, ToolGate, get_registry

logger = logging.getLogger("bionics.tasks")


def _destructive_allowed() -> bool:
    """Read BIONICS_MCP_ALLOW_DESTRUCTIVE at each submit. Mirrors mcp_server.py."""
    return os.environ.get("BIONICS_MCP_ALLOW_DESTRUCTIVE", "").strip().lower() in (
        "true", "1", "yes", "on"
    )


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """One async tool invocation and its result."""
    id: str
    tool_name: str
    args: dict
    status: TaskStatus = TaskStatus.PENDING
    result: dict | None = None       # Successful ToolResult serialized
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0

    # Internal: underlying future for cancellation. Excluded from to_dict.
    _future: Future | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "args": self.args,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_ms": round(
                ((self.completed_at or time.time()) - (self.started_at or self.created_at))
                * 1000,
                2,
            ),
        }


class TaskManager:
    """Thread-pool backed task scheduler. Singleton per process."""

    _instance: TaskManager | None = None
    _init_lock = threading.Lock()

    def __new__(cls, max_workers: int = 4):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._executor = ThreadPoolExecutor(
                        max_workers=max_workers, thread_name_prefix="bionics-task"
                    )
                    inst._tasks: dict[str, Task] = {}
                    inst._tasks_lock = threading.Lock()
                    inst._gate = ToolGate()
                    inst._gate.set_bypass_safety(True)
                    # Auto-log task outcomes to BionicsMemory. Lazy-init: failure
                    # leaves _memory=None and downstream paths skip logging.
                    # Disable explicitly with BIONICS_MEMORY_AUTOLOG=false.
                    inst._memory = None
                    if os.environ.get(
                        "BIONICS_MEMORY_AUTOLOG", "true"
                    ).strip().lower() not in ("false", "0", "no", "off"):
                        try:
                            from core.memory import BionicsMemory
                            inst._memory = BionicsMemory()
                        except Exception as e:
                            logger.debug(
                                "Memory auto-log disabled (init failed: %s)", e
                            )
                    cls._instance = inst
        return cls._instance

    # Auto-evict completed tasks once `_tasks` crosses this size — protects
    # multi-hour MCP sessions where a fan-out loop submits hundreds of tasks
    # and the dict grows monotonically until process death. PENDING/RUNNING
    # tasks are never evicted — only terminal states.
    _AUTO_EVICT_THRESHOLD = 500

    def submit(self, tool_name: str, args: dict | None = None) -> str:
        """Queue an async tool call. Returns the task_id to poll.

        Raises ValueError if `tool_name` isn't registered.
        Raises PermissionError if the tool is DESTRUCTIVE and
        `BIONICS_MCP_ALLOW_DESTRUCTIVE` is not truthy — mirrors the check in
        `mcp_server.py::_make_mcp_wrapper` so the task-submit path cannot be
        used as a DESTRUCTIVE-gate bypass.
        """
        spec = get_registry().get(tool_name)
        if spec is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        if spec.safety_tier == SafetyTier.DESTRUCTIVE and not _destructive_allowed():
            raise PermissionError(
                f"Tool '{tool_name}' is DESTRUCTIVE and blocked by default. "
                f"Set BIONICS_MCP_ALLOW_DESTRUCTIVE=true to enable."
            )
        # Auto-evict before insertion if we're at the soft cap — keeps long
        # sessions bounded without requiring the caller to call clear_completed.
        if len(self._tasks) >= self._AUTO_EVICT_THRESHOLD:
            evicted = self.clear_completed()
            if evicted > 0:
                logger.info(
                    "Auto-evicted %d terminal tasks (threshold %d hit)",
                    evicted, self._AUTO_EVICT_THRESHOLD,
                )
        task_id = f"task_{uuid.uuid4().hex[:16]}"
        task = Task(id=task_id, tool_name=tool_name, args=args or {})
        # Submit BEFORE publishing the task to _tasks — other threads must not see
        # a task whose `_future` is still None. The worker thread blocks on
        # _tasks_lock until we finish the dict insert + future assignment.
        with self._tasks_lock:
            future = self._executor.submit(self._run, task)
            task._future = future
            self._tasks[task_id] = task
        return task_id

    def _run(self, task: Task) -> None:
        """Worker thread entry point — executes the tool and records results."""
        with self._tasks_lock:
            # Check for pre-start cancellation.
            if task.status == TaskStatus.CANCELLED:
                return
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

        try:
            result = self._gate.execute(task.tool_name, task.args)
            with self._tasks_lock:
                task.completed_at = time.time()
                if result.ok:
                    task.status = TaskStatus.COMPLETED
                    task.result = result.to_dict()
                else:
                    task.status = TaskStatus.FAILED
                    task.error = result.error or "tool returned ok=False"
                    task.result = result.to_dict()
        except Exception as e:
            logger.exception("Task %s crashed", task.id)
            with self._tasks_lock:
                task.completed_at = time.time()
                task.status = TaskStatus.FAILED
                task.error = f"{type(e).__name__}: {e}"

        # Auto-log task outcome to BionicsMemory (best-effort; never blocks task)
        if self._memory is not None:
            try:
                # Args keys only — values may contain paths or freeform strings
                # we don't want to fan into the memory store at this scope.
                arg_keys = sorted(task.args.keys()) if task.args else []
                outcome = {
                    "status": task.status.value,
                    "elapsed_ms": round(
                        (task.completed_at - (task.started_at or task.created_at)) * 1000, 2
                    ),
                    "error": task.error or None,
                    "arg_keys": arg_keys,
                }
                self._memory.remember(
                    scope="task_outcome",
                    topic=task.tool_name,
                    key=task.id,
                    value=outcome,
                )
            except Exception as e:
                # Memory failures must never affect task lifecycle
                logger.debug("Auto-log skipped for %s: %s", task.id, e)

    # ---------- Query API ----------

    def get_task(self, task_id: str) -> Task | None:
        with self._tasks_lock:
            return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> TaskStatus | None:
        task = self.get_task(task_id)
        return task.status if task else None

    def list_tasks(self, status_filter: TaskStatus | None = None) -> list[Task]:
        with self._tasks_lock:
            tasks = list(self._tasks.values())
        if status_filter is not None:
            tasks = [t for t in tasks if t.status == status_filter]
        # Most recent first
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def cancel(self, task_id: str) -> bool:
        """Try to cancel a task. Returns True if the task was pre-start.

        Running tasks can't be cancelled (Python has no preemption). A
        still-pending task's Future is cancelled and its status set to
        CANCELLED so polls reflect the decision.
        """
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.PENDING:
                return False
            if task._future is not None and task._future.cancel():
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                return True
            return False

    def wait(self, task_id: str, timeout: float | None = None) -> Task | None:
        """Block until a task terminates (or timeout). Useful in tests.

        Snapshots `_future` under `_tasks_lock` so a concurrent `cancel()` (which
        also holds the lock) cannot null the field mid-read. `Future.result()`
        is itself thread-safe — release the lock before blocking on it so other
        TaskManager calls aren't blocked behind this wait.
        """
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            future = task._future if task is not None else None
        if task is None or future is None:
            return task
        try:
            future.result(timeout=timeout)
        except Exception:
            pass  # Status is already recorded by _run
        return task

    def clear_completed(self) -> int:
        """Remove all non-PENDING/non-RUNNING tasks. Returns number removed."""
        with self._tasks_lock:
            before = len(self._tasks)
            self._tasks = {
                tid: t
                for tid, t in self._tasks.items()
                if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
            }
            return before - len(self._tasks)


def get_task_manager() -> TaskManager:
    """Return the process-wide TaskManager singleton."""
    return TaskManager()
