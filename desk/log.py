"""Daily log files. Secret values are stripped before a line is written."""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_SECRETS: list[str] = []
_READY = False


def register_secret(value: str | None):
    if not value:
        return
    text = str(value).strip()
    if len(text) < 6:
        return
    if text not in _SECRETS:
        _SECRETS.append(text)


def redact(text: str) -> str:
    for secret in _SECRETS:
        if secret and secret in text:
            text = text.replace(secret, "***")
    return text


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg = clean
            record.args = ()
        return True


def setup(log_dir: str | Path = "logs"):
    global _READY
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("desk")
    root.setLevel(logging.INFO)
    if not _READY:
        root.addFilter(_Redact())
        _READY = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    return path


def get_logger(name: str, log_dir: str | Path = "logs") -> logging.Logger:
    setup(log_dir)
    logger = logging.getLogger(f"desk.{name}")
    target = str(Path(log_dir) / f"{name}.log")
    for h in logger.handlers:
        if getattr(h, "_desk_target", None) == target:
            return logger
    handler = TimedRotatingFileHandler(target, when="midnight", backupCount=14, encoding="utf-8")
    handler._desk_target = target
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    handler.addFilter(_Redact())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def scrub_env_name(name: str):
    register_secret(os.environ.get(name))
