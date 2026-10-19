import json
import logging
import sys
from datetime import datetime, timezone
from contextvars import ContextVar

# Context variables for correlation ID and job ID
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")
job_id_ctx: ContextVar[str] = ContextVar("job_id", default="")

class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            super().emit(record)
        except (ValueError, OSError):
            pass

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "correlation_id": correlation_id_ctx.get() or getattr(record, "correlation_id", None),
            "job_id": job_id_ctx.get() or getattr(record, "job_id", None),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps({k: v for k, v in log_obj.items() if v is not None})

def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("incubrix")
    logger.setLevel(level)
    logger.handlers.clear()

    handler = SafeStreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger

logger = setup_logging()
