"""Secret redaction for logs + optional access token gate."""
from __future__ import annotations

import logging
import os
import re
import secrets

_PATTERNS = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_\-]{16,}", r"gsk_[A-Za-z0-9]{16,}", r"AIza[0-9A-Za-z_\-]{20,}", r"AKIA[0-9A-Z]{16}",
    r"(?i)(api[-_]?key|subscription-key|authorization|x-api-key|xi-api-key|token)([\"'=:\s]+)([^\s\"'&,]{6,})",
)]
_ENV_SECRET_HINT = ("KEY", "SECRET", "TOKEN")


def redact(text: str) -> str:
    for p in _PATTERNS:
        if p.groups == 3:
            text = p.sub(lambda m: m.group(1) + m.group(2) + "***", text)
        else:
            text = p.sub("***", text)
    for k, v in os.environ.items():
        if v and len(v) >= 8 and any(h in k for h in _ENV_SECRET_HINT):
            text = text.replace(v, "***")
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        except Exception:
            pass
        return True


def install_log_redaction():
    f = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "voice", "voice.session"):
        lg = logging.getLogger(name)
        lg.addFilter(f)
        for h in lg.handlers:
            h.addFilter(f)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs (Google ?key=)


def access_token() -> str | None:
    return os.environ.get("APP_ACCESS_TOKEN") or None


def check_token(given: str | None) -> bool:
    tok = access_token()
    return True if not tok else bool(given) and secrets.compare_digest(given, tok)
