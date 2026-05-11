"""Logs de latencia en JSON (una línea por evento) para agregar p50/p95 fuera de proceso."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def emit_latency(settings: Any, event: str, **fields: Any) -> None:
    if not getattr(settings, "latency_log_enabled", True):
        return
    payload = {"event": event, **fields}
    logger.info("%s", json.dumps(payload, ensure_ascii=False))
