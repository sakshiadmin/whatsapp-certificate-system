"""
Structured (JSON) logging setup.

In production you will not be sitting in front of a terminal watching logs
scroll by — you will be searching Railway/Render's log viewer for one
specific phone number's request, hours after it happened. Plain text logs
like "Sent certificate to student" are useless for that. JSON logs like
{"event": "certificate_sent", "phone": "+9199...", "certificate_id": "..."}
can be searched and filtered.

We use structlog because it's the standard modern choice, and its default
JSON renderer is exactly what Railway/Render's log search expects.
"""

import logging
import sys

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
