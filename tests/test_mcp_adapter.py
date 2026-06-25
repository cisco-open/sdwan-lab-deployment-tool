import asyncio
import logging
import time

import pytest
from typer import Exit

pytest.importorskip("mcp", reason="requires the optional 'mcp' extra")

from catalyst_sdwan_lab._mcp_adapter import (
    _finalize_result,
    capture_task,
    poll_job,
    start_job,
)

log = logging.getLogger("catalyst_sdwan_lab.tasks.test")


def _run(coro):
    return asyncio.run(coro)


class TestFinalizeResult:
    def test_success_returns_console_output(self) -> None:
        assert _finalize_result("all good", None) == ("done", "all good")

    def test_success_empty_output_defaults_to_done(self) -> None:
        assert _finalize_result("", None) == ("done", "Done.")

    def test_exit_zero_is_done(self) -> None:
        status, text = _finalize_result("finished", Exit(0))
        assert status == "done"
        assert text == "finished"

    def test_exit_nonzero_is_error(self) -> None:
        status, text = _finalize_result("boom", Exit(1))
        assert status == "error"
        assert text == "Error: boom"

    def test_exit_uses_exit_code_attribute(self) -> None:
        # Regression: typer.Exit exposes .exit_code, not .code
        status, text = _finalize_result("", Exit(2))
        assert status == "error"
        assert "code 2" in text

    def test_generic_exception_is_error_with_traceback(self) -> None:
        status, text = _finalize_result("", ValueError("nope"))
        assert status == "error"
        assert "ValueError" in text
        assert "nope" in text


class TestJobModel:
    def test_job_streams_events_then_result(self) -> None:
        def task() -> None:
            log.info("first event")
            log.info("second event")

        job_id = start_job("test", task)
        # Drain until done.
        seen = ""
        for _ in range(50):
            out = _run(poll_job(job_id, timeout=1.0))
            seen += out
            if "status: done" in out:
                break
        assert "first event" in seen
        assert "second event" in seen
        assert "status: done" in seen

    def test_events_are_delivered_once(self) -> None:
        def task() -> None:
            log.info("only once")
            time.sleep(0.3)

        job_id = start_job("test", task)
        first = _run(poll_job(job_id, timeout=1.0))
        assert "only once" in first
        # Subsequent polls must not re-deliver the same event.
        rest = ""
        for _ in range(20):
            out = _run(poll_job(job_id, timeout=1.0))
            rest += out
            if "status: done" in out:
                break
        assert "only once" not in rest

    def test_failing_task_reports_error_status(self) -> None:
        def task() -> None:
            raise Exit(1)

        job_id = start_job("test", task)
        seen = ""
        for _ in range(50):
            out = _run(poll_job(job_id, timeout=1.0))
            seen += out
            if "status: error" in out or "status: done" in out:
                break
        assert "status: error" in seen

    def test_unknown_job_id(self) -> None:
        out = _run(poll_job("does-not-exist", timeout=0.1))
        assert "unknown" in out

    def test_completed_job_is_removed_after_result_delivered(self) -> None:
        def task() -> None:
            log.info("done event")

        job_id = start_job("test", task)
        for _ in range(50):
            out = _run(poll_job(job_id, timeout=1.0))
            if "status: done" in out:
                break
        # Job has been popped; polling again reports unknown.
        out = _run(poll_job(job_id, timeout=0.1))
        assert "unknown" in out


class TestCaptureTask:
    def test_returns_console_and_log_output(self) -> None:
        def task() -> None:
            log.info("hello from task")

        result = capture_task(task)
        assert "hello from task" in result

    def test_exit_nonzero_returns_error(self) -> None:
        def task() -> None:
            raise Exit(1)

        result = capture_task(task)
        assert result.startswith("Error:")

    def test_generic_exception_returns_error(self) -> None:
        def task() -> None:
            raise RuntimeError("kaboom")

        result = capture_task(task)
        assert "RuntimeError" in result
        assert "kaboom" in result
