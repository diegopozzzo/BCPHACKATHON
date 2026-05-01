from __future__ import annotations

import logging
from collections.abc import Iterable
from urllib.parse import urlparse

from scrapling.fetchers import DynamicFetcher, Fetcher, FetcherSession

logger = logging.getLogger(__name__)


def _response_to_html(resp) -> str:
    """
    Scrapling fetchers return either a parsed page-like object or a custom Response.
    Handle both.
    """
    html = getattr(resp, "html", None)
    if isinstance(html, str) and html.strip():
        return html
    text = getattr(resp, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    body = getattr(resp, "body", None)
    if isinstance(body, (bytes, bytearray)) and body:
        try:
            return body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return body.decode(errors="replace")
    return ""


def fetch_html(url: str, *, timeout: int = 30) -> str:
    """
    Best-effort HTML fetch.

    - Uses HTTP Fetcher first (fast).
    - Uses an impersonated session if the plain request fails.
    - For JS-heavy / protected sources, falls back to browser-based fetchers.
    """
    host = (urlparse(url).hostname or "").lower()
    # Dynamic/Stealth fetchers expect timeout in milliseconds.
    ms = max(int(timeout * 1000), 30_000)

    # Known SPA: requires JS rendering to get listings.
    if "bumeran.com.pe" in host:
        try:
            resp = DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=ms)
            return _response_to_html(resp)
        except Exception:  # noqa: BLE001
            logger.info("DynamicFetcher failed for %s, falling back to HTTP", url)

    try:
        resp = Fetcher.get(url, timeout=timeout)
        html = _response_to_html(resp)
        # Some providers return a SPA shell or an access-denied page on HTTP.
        if "bumeran.com.pe" in host and html and "id=\"root\"" in html and "need to enable javascript" in html.lower():
            resp2 = DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=ms)
            return _response_to_html(resp2)
        return html
    except Exception:  # noqa: BLE001
        logger.info("Fetcher.get failed, retrying with FetcherSession: %s", url)

    with FetcherSession(impersonate="chrome") as session:
        resp = session.get(url, stealthy_headers=True, timeout=timeout)
        html = _response_to_html(resp)
        return html


def first_nonempty(values: Iterable[str | None]) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""

