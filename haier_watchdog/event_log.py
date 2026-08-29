"""Log persistente: texto rotativo + JSONL de eventos."""

from __future__ import annotations

import threading
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class EventLog:
    def __init__(self, path: Path, timezone: str) -> None:
        self._path = path
        self._timezone = timezone
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: str, payload: dict[str, Any] | None = None) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(ZoneInfo(self._timezone)).isoformat(),
            "event": event,
        }
        if payload:
            record.update(payload)
        line = json.dumps(record, ensure_ascii=False, default=str)
        try:
            with self._lock:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            _LOGGER.exception("No se ha podido escribir %s", self._path)


def configure_file_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    text_path = log_dir / "watchdog.log"
    handler = RotatingFileHandler(
        text_path,
        maxBytes=5_000_000,
        backupCount=20,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    if not any(
        isinstance(existing, RotatingFileHandler)
        and getattr(existing, "baseFilename", None) == str(text_path)
        for existing in root.handlers
    ):
        root.addHandler(handler)
    return text_path
