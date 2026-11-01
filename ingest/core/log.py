"""Structured logging.

Console rendering when a human is watching, JSON when not. The JSON path is
what makes logs queryable once they land in a file or in CI; the console path
is what makes them readable while developing.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, level: str = "INFO", force_json: bool | None = None) -> None:
    """Configure structlog once, at process start.

    `force_json` overrides the TTY heuristic. CI has no TTY, so it gets JSON
    without needing to ask.
    """
    as_json = not sys.stderr.isatty() if force_json is None else force_json

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if as_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level]
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
