"""Logging utilities for CLI and Qt UI."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable


class QtTextLogHandler(logging.Handler):
    """Bridge logging records to a QTextEdit appender callback."""

    def __init__(self, append_callback: Callable[[str], None]):
        super().__init__()
        self._append_callback = append_callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self._append_callback(message)
        except Exception:
            self.handleError(record)


def configure_logger(
    log_file: str | None = None,
    ui_callback: Callable[[str], None] | None = None,
) -> logging.Logger:
    logger = logging.getLogger("tmx_repair")
    log_level_name = os.getenv("TMX_REPAIR_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    logger.setLevel(getattr(logging, log_level_name, logging.INFO))
    logger.propagate = False

    while logger.handlers:
        logger.handlers.pop()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        file_level_name = os.getenv("TMX_REPAIR_FILE_LOG_LEVEL", "WARNING").strip().upper() or "WARNING"
        file_handler = logging.FileHandler(Path(log_file), encoding="utf-8")
        file_handler.setLevel(getattr(logging, file_level_name, logging.WARNING))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if ui_callback is not None:
        qt_handler = QtTextLogHandler(ui_callback)
        qt_handler.setFormatter(formatter)
        logger.addHandler(qt_handler)

    return logger
