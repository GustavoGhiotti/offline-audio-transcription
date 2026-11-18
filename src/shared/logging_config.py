from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path


class _SomenteLogsDoRobo(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == "root"


def configurar_logging(root: Path, debug: bool) -> Path:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"transcricao_{date.today():%Y%m%d}.log"
    console_handler = logging.StreamHandler(sys.stdout)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    own_logs_filter = _SomenteLogsDoRobo()
    console_handler.addFilter(own_logs_filter)
    file_handler.addFilter(own_logs_filter)

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            console_handler,
            file_handler,
        ],
        force=True,
    )
    return log_file
