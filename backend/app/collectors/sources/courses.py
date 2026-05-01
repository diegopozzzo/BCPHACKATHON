from __future__ import annotations

from urllib.parse import quote_plus

from app.collectors.http import fetch_html
from app.collectors.schema import NormalizedOpportunity, guess_region
from app.collectors.sources.generic_listings import dedupe_by_url, extract_anchor_cards


def collect_mtpe(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    # Public MTPE course catalog search endpoint can vary; scrape the catalog and filter client-side.
    url = "https://capacitacionlaboral.trabajo.gob.pe/cursos/"
    html = fetch_html(url)
    items = extract_anchor_cards(
        base_url="https://capacitacionlaboral.trabajo.gob.pe/",
        html=html,
        source="mtpe",
        opp_type="curso",
        card_selector="article, div[class*='course'], div[class*='card']",
        title_selector="h1::text, h2::text, h3::text, a::text",
        url_selector="a::attr(href)",
        org_selector=None,
        region_selector=None,
        req_selector="p::text, li::text",
        limit=limit * 5,
    )
    q_words = [w for w in (query or "").lower().split() if w]
    if q_words:
        items = [
            it
            for it in items
            if all(w in (it.title + " " + it.requirements).lower() for w in q_words)
        ]
    items = items[:limit]
    # Fill org/region defaults
    out: list[NormalizedOpportunity] = []
    for it in items:
        out.append(
            NormalizedOpportunity(
                title=it.title,
                type="curso",
                organization=it.organization if it.organization != "—" else "MTPE - Capacitación Laboral",
                region=it.region if it.region != "—" else guess_region(it.requirements) or "Remoto",
                requirements=it.requirements,
                url=it.url,
                source=it.source,
            )
        )
    return out


def collect_platzi(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    q = quote_plus(query.strip() or "python")
    url = f"https://platzi.com/buscar/?search={q}"
    html = fetch_html(url)
    items = extract_anchor_cards(
        base_url="https://platzi.com/",
        html=html,
        source="platzi",
        opp_type="curso",
        card_selector="a[href*='/cursos/'], article, div[class*='Card'], li",
        title_selector="h1::text, h2::text, h3::text, p::text, a::text",
        url_selector="a::attr(href)",
        org_selector=None,
        region_selector=None,
        req_selector="p::text",
        limit=limit,
    )
    return [
        NormalizedOpportunity(
            title=it.title,
            type="curso",
            organization="Platzi",
            region="Remoto",
            requirements=it.requirements,
            url=it.url,
            source=it.source,
        )
        for it in items
    ]


def collect_edx_business(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    q = quote_plus(query.strip() or "ai")
    url = f"https://business.edx.org/search?q={q}"
    html = fetch_html(url)
    items = extract_anchor_cards(
        base_url="https://business.edx.org/",
        html=html,
        source="edx-business",
        opp_type="curso",
        card_selector="a[href*='/course/'], a[href*='/program/'], article, div[class*='card'], li",
        title_selector="h1::text, h2::text, h3::text, span::text, a::text",
        url_selector="a::attr(href)",
        org_selector=None,
        region_selector=None,
        req_selector="p::text, li::text",
        limit=limit,
    )
    return [
        NormalizedOpportunity(
            title=it.title,
            type="curso",
            organization="edX (Business)",
            region="Remoto",
            requirements=it.requirements,
            url=it.url,
            source=it.source,
        )
        for it in items
    ]


def collect_coursera(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    q = quote_plus(query.strip() or "python")
    url = f"https://www.coursera.org/search?query={q}"
    html = fetch_html(url)
    items = extract_anchor_cards(
        base_url="https://www.coursera.org/",
        html=html,
        source="coursera",
        opp_type="curso",
        card_selector="a[href^='/learn/'], a[href^='/professional-certificates/'], a[href^='/specializations/'], article, li",
        title_selector="h1::text, h2::text, h3::text, span::text, a::text",
        url_selector="a::attr(href)",
        org_selector="span[class*='partner']::text, span[class*='provider']::text",
        region_selector=None,
        req_selector="p::text, li::text",
        limit=limit,
    )
    return [
        NormalizedOpportunity(
            title=it.title,
            type="curso",
            organization=it.organization if it.organization != "—" else "Coursera",
            region="Remoto",
            requirements=it.requirements,
            url=it.url,
            source=it.source,
        )
        for it in items
    ]


def collect_courses(*, query: str, limit: int = 60) -> list[NormalizedOpportunity]:
    per_source = max(5, limit // 4)
    items: list[NormalizedOpportunity] = []
    for fn in (collect_mtpe, collect_platzi, collect_edx_business, collect_coursera):
        try:
            items.extend(fn(query=query, limit=per_source))
        except Exception:  # noqa: BLE001
            continue
    return dedupe_by_url(items)[:limit]

