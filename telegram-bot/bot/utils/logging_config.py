import json
import logging
import sys
import traceback
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for machine-parseable log files.

    Outputs one JSON object per line with fields:
      ts, level, logger, msg, [exc], [agent], [duration_ms], [tokens]

    Extra fields can be passed via logger.info("msg", extra={"agent": "nimrod"}).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                    .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Include any extra fields passed by the caller
        for key in ("agent", "duration_ms", "tokens", "cost_usd", "model",
                     "project", "operation", "attempt", "error_type"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exc"] = "".join(
                traceback.format_exception(*record.exc_info)
            )

        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO"):
    """Configure logging for the bot.

    - stdout: human-readable text format (for terminal / journalctl)
    - bot.log: structured JSON, one object per line (for parsing / search)
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))

    # Clear any existing handlers (prevents duplicates on reload)
    root.handlers.clear()

    # Human-readable format for stdout (terminal / journalctl)
    text_handler = logging.StreamHandler(sys.stdout)
    text_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(text_handler)

    # Structured JSON format for log file (machine-parseable)
    json_handler = logging.FileHandler("bot.log", encoding="utf-8")
    json_handler.setFormatter(JSONFormatter())
    root.addHandler(json_handler)

    # Reduce noise from httpx (used by python-telegram-bot internally)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
