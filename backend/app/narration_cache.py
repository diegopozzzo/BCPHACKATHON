"""Caché de narraciones LLM (memoria LRU o Redis opcional vía REDIS_URL)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

_mem_lock = Lock()
_mem: OrderedDict[str, tuple[str, float]] = OrderedDict()
_redis_warned = False


def narration_cache_digest(namespace: str, payload: dict[str, Any]) -> str:
    raw = json.dumps({"ns": namespace, "p": payload}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mem_get(key_full: str) -> str | None:
    with _mem_lock:
        tup = _mem.get(key_full)
        if tup is None:
            return None
        text, exp_mono = tup
        if exp_mono < time.monotonic():
            del _mem[key_full]
            return None
        _mem.move_to_end(key_full, last=True)
        return text


def _mem_set(settings: Settings, key_full: str, text: str) -> None:
    ttl = max(1, settings.narration_cache_ttl_sec)
    exp = time.monotonic() + ttl
    max_ent = settings.narration_cache_max_entries
    with _mem_lock:
        if key_full in _mem:
            del _mem[key_full]
        while len(_mem) >= max_ent and _mem:
            _mem.popitem(last=False)
        _mem[key_full] = (text, exp)


def _redis_key(settings: Settings, digest: str) -> str:
    return f"{settings.narration_cache_redis_prefix}:{digest}"


def _redis_sync_get(settings: Settings, digest: str) -> str | None:
    global _redis_warned
    try:
        import redis
    except ImportError:
        if not _redis_warned:
            logger.warning("REDIS_URL definido pero el paquete redis no está instalado; solo caché en memoria.")
            _redis_warned = True
        return None
    try:
        r = redis.Redis.from_url(settings.redis_url or "", decode_responses=True, socket_timeout=5.0)
        return r.get(_redis_key(settings, digest))
    except Exception as exc:  # noqa: BLE001
        if not _redis_warned:
            logger.warning("Redis narración GET falló: %s", exc)
            _redis_warned = True
        return None


def _redis_sync_set(settings: Settings, digest: str, text: str) -> None:
    global _redis_warned
    try:
        import redis
    except ImportError:
        return
    try:
        r = redis.Redis.from_url(settings.redis_url or "", decode_responses=True, socket_timeout=5.0)
        ttl = max(1, settings.narration_cache_ttl_sec)
        r.set(_redis_key(settings, digest), text, ex=ttl)
    except Exception as exc:  # noqa: BLE001
        if not _redis_warned:
            logger.warning("Redis narración SET falló: %s", exc)
            _redis_warned = True


async def narration_cache_get(settings: Settings, namespace: str, digest: str) -> str | None:
    if not settings.narration_cache_enabled or settings.narration_cache_ttl_sec <= 0:
        return None
    key_full = f"{namespace}:{digest}"
    hit = _mem_get(key_full)
    if hit is not None:
        return hit
    if settings.redis_url:
        redis_hit = await asyncio.to_thread(_redis_sync_get, settings, digest)
        if redis_hit:
            _mem_set(settings, key_full, redis_hit)
            return redis_hit
    return None


async def narration_cache_set(settings: Settings, namespace: str, digest: str, text: str) -> None:
    if not settings.narration_cache_enabled or settings.narration_cache_ttl_sec <= 0 or not text.strip():
        return
    key_full = f"{namespace}:{digest}"
    _mem_set(settings, key_full, text)
    if settings.redis_url:
        await asyncio.to_thread(_redis_sync_set, settings, digest, text)
