"""Scheduler and watch-mode orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable


logger = logging.getLogger(__name__)


def run_once(task: Callable[[], dict]) -> dict:
    """Run the task once and return its output."""
    logger.info("Running single sprint health cycle")
    return task()


def run_watch(task: Callable[[], dict], interval_seconds: int) -> None:
    """Run task repeatedly on fixed interval until interrupted."""
    logger.info("Watch mode enabled with interval=%ss", interval_seconds)
    while True:
        started = time.time()
        try:
            task()
        except Exception:
            logger.exception("Watch cycle failed")
        elapsed = time.time() - started
        sleep_for = max(0, interval_seconds - elapsed)
        time.sleep(sleep_for)
