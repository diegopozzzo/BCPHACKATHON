from __future__ import annotations

from urllib.parse import quote_plus

from app.collectors.http import fetch_html
from app.collectors.schema import NormalizedOpportunity
from app.collectors.sources.generic_listings import dedupe_by_url, extract_anchor_cards


def collect_expand_peru(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    # Expand Peru is largely informational; treat project pages as “opportunities”.
    q = quote_plus(query.strip() or "voluntariado")
    url = f"https://www.expandperu.org/?s={q}"
    html = fetch_html(url)
    items = extract_anchor_cards(
        base_url="https://www.expandperu.org/",
        html=html,
        source="expandperu",
        opp_type="voluntariado",
        card_selector="article, div[class*='post'], div[class*='card'], li",
        title_selector="h1::text, h2::text, h3::text, a::text",
        url_selector="a::attr(href)",
        org_selector=None,
        region_selector=None,
        req_selector="p::text, li::text",
        limit=limit,
    )
    return [
        NormalizedOpportunity(
            title=it.title,
            type="voluntariado",
            organization="Expand Perú",
            region=it.region if it.region != "—" else "Perú",
            requirements=it.requirements,
            url=it.url,
            source=it.source,
        )
        for it in items
    ]


def collect_proa(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    # Proa is a directory; the volunteer programs are behind their UI. Best-effort search via query param.
    q = quote_plus(query.strip() or "voluntariado")
    url = f"https://proa.pe/?s={q}"
    html = fetch_html(url)
    items = extract_anchor_cards(
        base_url="https://proa.pe/",
        html=html,
        source="proa",
        opp_type="voluntariado",
        card_selector="article, div[class*='card'], li, a[href*='volunt']",
        title_selector="h1::text, h2::text, h3::text, a::text",
        url_selector="a::attr(href)",
        org_selector=None,
        region_selector=None,
        req_selector="p::text, li::text",
        limit=limit,
    )
    return [
        NormalizedOpportunity(
            title=it.title,
            type="voluntariado",
            organization="Proa",
            region=it.region if it.region != "—" else "Perú",
            requirements=it.requirements,
            url=it.url,
            source=it.source,
        )
        for it in items
    ]


def collect_onlinevolunteering(*, query: str, limit: int = 18) -> list[NormalizedOpportunity]:
    q = quote_plus(query.strip() or "education")
    url = f"https://www.onlinevolunteering.org/en/volunteer-opportunities?q={q}"
    html = fetch_html(url, timeout=55)
    items = extract_anchor_cards(
        base_url="https://www.onlinevolunteering.org/",
        html=html,
        source="unv-online",
        opp_type="voluntariado",
        card_selector="article, li, div[class*='view'], a[href*='/volunteer-opportunities/']",
        title_selector="h2::text, h3::text, a::text",
        url_selector="a::attr(href)",
        org_selector="span::text, em::text",
        region_selector=None,
        req_selector="p::text",
        limit=limit,
        url_must_contain="volunteer-opportunities",
    )
    return [
        NormalizedOpportunity(
            title=it.title,
            type="voluntariado",
            organization=it.organization if it.organization != "—" else "Online Volunteering (UNV)",
            region=it.region if it.region != "—" else "Remoto",
            requirements=it.requirements,
            url=it.url,
            source=it.source,
        )
        for it in items
    ]


def collect_volunteering(*, query: str, limit: int = 40) -> list[NormalizedOpportunity]:
    per_source = max(5, limit // 3)
    items: list[NormalizedOpportunity] = []
    for fn in (collect_expand_peru, collect_proa, collect_onlinevolunteering):
        try:
            items.extend(fn(query=query, limit=per_source))
        except Exception:  # noqa: BLE001
            continue
    return dedupe_by_url(items)[:limit]

