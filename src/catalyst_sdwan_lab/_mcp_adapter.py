"""
Adapter layer for running CLI task functions in MCP context.

Handles:
- Catching typer.Exit (used for flow control in task modules) and converting
  to a textual error result instead of terminating the process.
- Capturing Rich console output so it can be returned as tool result text.
- Streaming log messages as MCP notifications in real-time via Context.
- Running long tasks as background jobs whose buffered events can be pulled
  into the agent's context via repeated job_status polls.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Empty, SimpleQueue
from typing import Any

import typer
from mcp.server.fastmcp import Context
from rich.console import Console


class _StreamingLogHandler(logging.Handler):
    """Pushes log records into a queue for async consumption."""

    def __init__(self, queue: SimpleQueue[str | None]) -> None:
        super().__init__()
        self.queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        self.queue.put(self.format(record))


def _patch_task_consoles(capture_console: Console) -> list[tuple[object, str, Any]]:
    """Redirect any already-imported task module console references."""
    patched: list[tuple[object, str, Any]] = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("catalyst_sdwan_lab.tasks"):
            continue
        if module is None or not hasattr(module, "console"):
            continue
        original_console = getattr(module, "console")
        setattr(module, "console", capture_console)
        patched.append((module, "console", original_console))
    return patched


def _restore_patched_values(patched: list[tuple[object, str, Any]]) -> None:
    for module, attr_name, original_value in reversed(patched):
        setattr(module, attr_name, original_value)


def _run_task_in_thread(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    queue: SimpleQueue[str | None],
) -> tuple[str | None, BaseException | None]:
    """Run the task function synchronously in a thread, capturing output."""
    import catalyst_sdwan_lab.tasks.utils as utils

    buf = io.StringIO()
    original_console = utils.console
    capture_console = Console(file=buf, width=120, no_color=True)
    utils.console = capture_console
    patched = _patch_task_consoles(capture_console)

    handler = _StreamingLogHandler(queue)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger("catalyst_sdwan_lab")
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    error: BaseException | None = None
    try:
        fn(*args, **kwargs)
    except BaseException as e:
        error = e
    finally:
        utils.console = original_console
        _restore_patched_values(patched)
        root_logger.removeHandler(handler)
        queue.put(None)  # sentinel

    console_output = buf.getvalue().strip()
    return console_output, error


async def capture_task_async(
    ctx: Context, fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> str:
    """
    Run a task function and stream progress via MCP notifications.

    - Sends log messages as ctx.info() notifications in real-time.
    - Returns the final result/error as a string.
    """
    queue: SimpleQueue[str | None] = SimpleQueue()

    loop = asyncio.get_event_loop()
    future = loop.run_in_executor(
        None, _run_task_in_thread, fn, args, kwargs, queue
    )

    # Drain the queue, forwarding messages to the MCP client as they arrive
    step = 0
    while True:
        # Poll the queue without blocking the event loop
        try:
            msg = queue.get_nowait()
        except Empty:
            # Check if the thread is done
            if future.done():
                # Drain remaining messages
                while True:
                    try:
                        msg = queue.get_nowait()
                    except Empty:
                        break
                    if msg is None:
                        break
                    step += 1
                    await ctx.report_progress(step, message=msg)
                break
            await asyncio.sleep(0.1)
            continue

        if msg is None:
            break
        step += 1
        await ctx.report_progress(step, message=msg)

    console_output, error = await future

    if error is None:
        return console_output if console_output else "Done."
    elif isinstance(error, typer.Exit):
        error_text = console_output or f"Task exited with code {error.exit_code}"
        if error.exit_code != 0:
            return f"Error: {error_text}"
        return error_text
    else:
        import traceback

        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        return f"Error: {type(error).__name__}: {error}\n{tb}"


def capture_task(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """
    Synchronous fallback for running tasks without MCP context.
    Used when no Context is available.
    """
    import catalyst_sdwan_lab.tasks.utils as utils

    buf = io.StringIO()
    original_console = utils.console
    capture_console = Console(file=buf, width=120, no_color=True)
    utils.console = capture_console
    patched = _patch_task_consoles(capture_console)

    log_records: list[str] = []

    class _LogCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(self.format(record))

    handler = _LogCapture()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger("catalyst_sdwan_lab")
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    try:
        fn(*args, **kwargs)
        console_output = buf.getvalue().strip()
        parts = [p for p in ["\n".join(log_records), console_output] if p]
        return "\n".join(parts) if parts else "Done."
    except typer.Exit as e:
        console_output = buf.getvalue().strip()
        parts = [p for p in ["\n".join(log_records), console_output] if p]
        error_text = "\n".join(parts) if parts else f"Task exited with code {e.exit_code}"
        if e.exit_code != 0:
            return f"Error: {error_text}"
        return error_text
    except Exception as e:
        import traceback

        return f"Error: {type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        utils.console = original_console
        _restore_patched_values(patched)
        root_logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# Background job model
#
# Long-running tasks (deploy, restore, add_devices, images_upload) cannot
# stream into the agent's context, because an MCP tool's only channel to the
# agent is its return value — delivered once, when the call completes.
#
# To surface progress (and interactive prompts such as the Cisco PKI
# registration URL) WHILE the task runs, we run the task in a background
# thread and let the agent pull buffered log events through repeated
# job_status() calls. Each poll long-polls server-side until a new event or a
# status change occurs, so polling stays cheap and responsive.
# ---------------------------------------------------------------------------


def _finalize_result(
    console_output: str, error: BaseException | None
) -> tuple[str, str]:
    """Map a finished task to (status, result_text). status is 'done' or 'error'."""
    if error is None:
        return "done", console_output or "Done."
    if isinstance(error, typer.Exit):
        text = console_output or f"Task exited with code {error.exit_code}"
        if error.exit_code not in (0, None):
            return "error", f"Error: {text}"
        return "done", text
    import traceback

    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    return "error", f"Error: {type(error).__name__}: {error}\n{tb}"


@dataclass
class _Job:
    """A background task whose events the agent pulls via job_status."""

    id: str
    label: str
    status: str = "running"  # running | done | error
    events: list[str] = field(default_factory=list)
    delivered: int = 0
    result: str | None = None
    _cond: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, msg: str) -> None:
        with self._cond:
            self.events.append(msg)
            self._cond.notify_all()

    def finish(self, status: str, result: str) -> None:
        with self._cond:
            self.status = status
            self.result = result
            self._cond.notify_all()

    def wait_and_drain(self, timeout: float) -> tuple[list[str], str, str | None]:
        """Block up to timeout for new events / completion, then drain new events."""
        with self._cond:
            if self.delivered >= len(self.events) and self.status == "running":
                self._cond.wait(timeout)
            new = self.events[self.delivered :]
            self.delivered = len(self.events)
            return new, self.status, self.result


class _JobLogHandler(logging.Handler):
    """Routes log records emitted during a job into that job's event buffer."""

    def __init__(self, job: _Job, thread_id: int) -> None:
        super().__init__()
        self.job = job
        self._thread_id = thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self._thread_id:
            self.job.emit(self.format(record))


_JOBS: dict[str, _Job] = {}
_JOBS_LOCK = threading.Lock()


def _job_worker(
    job: _Job, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    import catalyst_sdwan_lab.tasks.utils as utils

    buf = io.StringIO()
    original_console = utils.console
    capture_console = Console(file=buf, width=120, no_color=True)
    utils.console = capture_console
    patched = _patch_task_consoles(capture_console)

    handler = _JobLogHandler(job, threading.current_thread().ident or 0)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger("catalyst_sdwan_lab")
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    error: BaseException | None = None
    try:
        fn(*args, **kwargs)
    except BaseException as e:  # noqa: BLE001 - surfaced via job result
        error = e
    finally:
        utils.console = original_console
        _restore_patched_values(patched)
        root_logger.removeHandler(handler)

    status, result = _finalize_result(buf.getvalue().strip(), error)
    job.finish(status, result)


def start_job(label: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Start a task in a background thread and return its job id."""
    job = _Job(id=uuid.uuid4().hex[:8], label=label)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    thread = threading.Thread(
        target=_job_worker, args=(job, fn, args, kwargs), daemon=True
    )
    thread.start()
    return job.id


async def poll_job(job_id: str, timeout: float = 15.0) -> str:
    """Long-poll a job: wait for new events/completion, return them as text."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return f"status: unknown\nError: no job with id '{job_id}'"

    loop = asyncio.get_event_loop()
    new, status, result = await loop.run_in_executor(
        None, job.wait_and_drain, timeout
    )

    lines = [f"job_id: {job_id}", f"status: {status}"]
    if new:
        lines.append("events:")
        lines.extend(f"  {e}" for e in new)
    elif status == "running":
        lines.append("(no new events yet — keep polling)")
    if status in ("done", "error") and result is not None:
        lines.append("result:")
        lines.append(result)
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)
    return "\n".join(lines)


