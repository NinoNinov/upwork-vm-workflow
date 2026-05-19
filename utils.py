"""
Shared utilities: structured JSON logging, exponential backoff, retry helpers.
Import this module instead of copy-pasting helpers across files.
"""
import json
import logging
import random
import time
from typing import Callable, Optional, Tuple, Type


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    _SKIP = frozenset({
        "args", "asctime", "created", "exc_info", "exc_text",
        "filename", "funcName", "id", "levelname", "levelno",
        "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated",
        "stack_info", "thread", "threadName",
    })

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        for key, val in record.__dict__.items():
            if key not in self._SKIP:
                data[key] = val
        return json.dumps(data, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO, json_logs: bool = False) -> None:
    """Configure root logger for stdout."""
    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


# ---------------------------------------------------------------------------
# Retry / backoff helpers
# ---------------------------------------------------------------------------

def exponential_backoff(attempt: int, base_delay: float = 2.0, max_delay: float = 60.0) -> float:
    """Return delay (s) for the given attempt with exponential backoff + jitter."""
    delay = min(base_delay * (2 ** attempt), max_delay)
    jitter = random.uniform(-0.1 * delay, 0.1 * delay)
    return max(0.1, delay + jitter)


def retry_with_backoff(
    func: Callable,
    max_retries: int = 3,
    base_delay: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    logger: Optional[logging.Logger] = None,
):
    """Call *func* with no args; retry on *exceptions* with exponential backoff."""
    last_exc: Exception = RuntimeError("retry_with_backoff called with 0 attempts")
    for attempt in range(max_retries + 1):
        try:
            return func()
        except exceptions as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = exponential_backoff(attempt, base_delay)
            if logger:
                logger.warning(
                    "Attempt %d/%d failed (%s: %s). Retrying in %.1fs...",
                    attempt + 1, max_retries, type(exc).__name__, exc, delay,
                )
            time.sleep(delay)
    raise last_exc
