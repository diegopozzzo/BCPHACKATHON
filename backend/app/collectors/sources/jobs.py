from __future__ import annotations

from urllib.parse import quote_plus

from scrapling.parser import Selector

from app.collectors.http import fetch_html
from app.collectors.schema import NormalizedOpportunity
from app.collectors.sources.generic_listings import dedupe_by_url, extract_anchor_cards


def _ct_parse_detail(html: str) -> dict[str, str]:
    s = Selector(html or "")
    tags = [t.strip() for t in s.css("div.mbB span.tag.base::text").getall() if t and t.strip()]
    desc = " ".join([t.strip() for t in s.css("div[div-link='oferta'] p.mbB::text").getall() if t and t.strip()])
    reqs = " ".join([t.strip() for t in s.css("p.fwB.fs18:contains('Requerimientos') ~ ul li::text, ul.disc li::text").getall() if t and t.strip()])
    keywords = " ".join([t.strip() for t in s.css("p.fc_aux.fs13.mbB.mtB::text").getall() if t and t.strip()])
    pieces = []
    if tags:
        pieces.append(" / ".join(tags))
    if desc:
        pieces.append(desc)
    if reqs:
        pieces.append("Requerimientos: " + reqs)
    if keywords:
        pieces.append(keywords)
    return {"requirements": " | ".join([p for p in pieces if p]).strip()}


def collect_computrabajo(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    # Computrabajo PE exposes a keyword URL pattern and returns server-rendered listings.
    q = quote_plus(query.strip() or "python")
    base = "https://pe.computrabajo.com/"

    # Pull multiple pages to increase job count.
    pages_to_try = 5
    per_page = max(10, limit)
    raw: list[NormalizedOpportunity] = []
    for p in range(1, pages_to_try + 1):
        url = f"https://pe.computrabajo.com/trabajo-de-{q}?q={q}&p={p}"
        html = fetch_html(url)
        raw.extend(
            extract_anchor_cards(
                base_url=base,
                html=html,
                source="computrabajo",
                opp_type="empleo",
                card_selector="article.box_offer",
                title_selector="h2 a.js-o-link::text",
                url_selector="h2 a.js-o-link::attr(href)",
                org_selector="a[offer-grid-article-company-url]::text, p.dFlex a.t_ellipsis::text, p.dFlex.vm_fx.fs16.fc_base.mt5::text",
                region_selector="p.fs16.fc_base.mt5 span.mr10::text, p.fs16.fc_base.mt5 span::text",
                req_selector="p.dFlex.vm_fx.fs16.fc_base.mt5::text, p.fs16.fc_base.mt5::text",
                limit=per_page,
                url_must_contain="/ofertas-de-trabajo/oferta-de-trabajo",
            )
        )
        if len(raw) >= limit:
            break

    items = dedupe_by_url(raw)[:limit]

    # Enrich with detail page (more requirements info) for top N.
    enrich_n = min(25, len(items))
    out: list[NormalizedOpportunity] = []
    for idx, it in enumerate(items):
        if idx < enrich_n:
            try:
                detail_html = fetch_html(it.url, timeout=45)
                extra = _ct_parse_detail(detail_html)
                req = extra.get("requirements") or it.requirements
                it = NormalizedOpportunity(
                    title=it.title,
                    type=it.type,
                    organization=it.organization,
                    region=it.region,
                    requirements=req,
                    url=it.url,
                    source=it.source,
                )
            except Exception:  # noqa: BLE001
                pass
        out.append(it)
    return out


def collect_bumeran(*, query: str, limit: int = 20) -> list[NormalizedOpportunity]:
    q = quote_plus(query.strip() or "python")
    url = f"https://www.bumeran.com.pe/empleos-busqueda-{q}.html"
    html = fetch_html(url)
    items = extract_anchor_cards(
        base_url="https://www.bumeran.com.pe/",
        html=html,
        source="bumeran",
        opp_type="empleo",
        card_selector="article, div[class*='job'], div[class*='card'], li",
        title_selector="h1::text, h2::text, h3::text, a::text",
        url_selector="a::attr(href)",
        org_selector="span[class*='company']::text, p[class*='company']::text",
        region_selector="span[class*='location']::text, p[class*='location']::text",
        req_selector="p::text",
        limit=limit,
        url_must_not_contain="/login",
    )
    # If the page renders but does not include actual job cards (common when content is fetched via API),
    # return empty instead of polluting results with navbar links.
    bad_titles = {"crear cuenta", "ingresar", "buscar empresas", "salarios", "blog", "relevantes", "recientes"}
    filtered = [it for it in items if it.title.strip().lower() not in bad_titles]
    return filtered


def collect_jobs(*, query: str, limit: int = 60) -> list[NormalizedOpportunity]:
    items: list[NormalizedOpportunity] = []
    # Computrabajo is currently the most reliable for "empleo".
    items.extend(collect_computrabajo(query=query, limit=limit))
    # Keep Bumeran only if it yields real cards; otherwise it will return [].
    try:
        items.extend(collect_bumeran(query=query, limit=max(5, min(20, limit // 3))))
    except Exception:  # noqa: BLE001
        pass
    return dedupe_by_url(items)[:limit]

