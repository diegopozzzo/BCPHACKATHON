from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urljoin

from scrapling.parser import Selector

from app.collectors.schema import NormalizedOpportunity, guess_region, normalize_text


def _abs(base: str, href: str) -> str:
    href = (href or "").strip()
    return urljoin(base, href) if href else ""


def extract_anchor_cards(
    *,
    base_url: str,
    html: str,
    source: str,
    opp_type: str,
    card_selector: str,
    title_selector: str,
    url_selector: str,
    org_selector: str | None = None,
    region_selector: str | None = None,
    req_selector: str | None = None,
    limit: int = 20,
    url_must_contain: str | None = None,
    url_must_not_contain: str | None = None,
) -> list[NormalizedOpportunity]:
    """
    Generic extractor for “cards” list pages.

    Each `*_selector` is relative to each card element.
    """
    page = Selector(html or "")
    out: list[NormalizedOpportunity] = []
    for card in page.css(card_selector)[: max(limit, 0)]:
        title = normalize_text(card.css(title_selector).get())
        url = normalize_text(card.css(url_selector).get())
        if url and not url.lower().startswith(("http://", "https://")):
            url = _abs(base_url, url)
        org = normalize_text(card.css(org_selector).get() if org_selector else "") or "—"
        region = normalize_text(card.css(region_selector).get() if region_selector else "")
        req = normalize_text(card.css(req_selector).get() if req_selector else "")

        if not title or not url:
            continue
        if url_must_contain and url_must_contain not in url:
            continue
        if url_must_not_contain and url_must_not_contain in url:
            continue
        if not region:
            region = guess_region(" ".join([title, org, req]))
        if not req:
            req = normalize_text(" ".join([title, org, region]))

        out.append(
            NormalizedOpportunity(
                title=title,
                type=opp_type,
                organization=org,
                region=region or "—",
                requirements=req,
                url=url,
                source=source,
            )
        )
        if len(out) >= limit:
            break
    return out


def dedupe_by_url(items: Iterable[NormalizedOpportunity]) -> list[NormalizedOpportunity]:
    seen: set[str] = set()
    out: list[NormalizedOpportunity] = []
    for it in items:
        key = it.url.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out

