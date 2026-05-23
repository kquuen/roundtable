"""Structured logging configuration for roundtable."""

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger("roundtable")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(handler)
