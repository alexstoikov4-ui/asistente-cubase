"""Configuración del registro de actividad y errores."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger("aiengineer")
    if _CONFIGURED:
        return root
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(Path(logs_dir) / "aiengineer.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    eh = RotatingFileHandler(Path(logs_dir) / "errors.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    eh.setLevel(logging.WARNING)
    eh.setFormatter(fmt)
    root.addHandler(eh)
    _CONFIGURED = True
    return root
