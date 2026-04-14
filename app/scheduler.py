"""Scheduler and watch-mode orchestration."""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable


logger = logging.getLogger(__name__)
_STOP_EVENT = threading.Event()


def _request_stop(signum, _frame) -> None:
    """Request graceful shutdown for long-running watch loops."""
    logger.info("Scheduler stopping after signal=%s", signum)
    _STOP_EVENT.set()


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


def run_once(task: Callable[[], dict]) -> dict:
    """Run the task once and return its output."""
    logger.info("Running single sprint health cycle")
    return task()


def run_watch(task: Callable[[], dict], interval_seconds: int) -> None:
    """Run task repeatedly on fixed interval until interrupted."""
    logger.info("Watch mode enabled with interval=%ss", interval_seconds)
    while not _STOP_EVENT.is_set():
        started = time.time()
        try:
            task()
        except Exception:
            logger.exception("Watch cycle failed")
        elapsed = time.time() - started
        sleep_for = max(0, interval_seconds - elapsed)
        _STOP_EVENT.wait(timeout=sleep_for)
    logger.info("Watch mode exited cleanly")
