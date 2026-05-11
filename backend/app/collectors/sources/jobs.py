from __future__ import annotations

import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

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


_CT_CARD = dict(
    card_selector="article.box_offer",
    title_selector="h2 a.js-o-link::text",
    url_selector="h2 a.js-o-link::attr(href)",
    org_selector="a[offer-grid-article-company-url]::text, p.dFlex a.t_ellipsis::text, p.dFlex.vm_fx.fs16.fc_base.mt5::text",
    region_selector="p.fs16.fc_base.mt5 span.mr10::text, p.fs16.fc_base.mt5 span::text",
    req_selector="p.dFlex.vm_fx.fs16.fc_base.mt5::text, p.fs16.fc_base.mt5::text",
    url_must_contain="/ofertas-de-trabajo/oferta-de-trabajo",
)


def _effective_computrabajo_keyword(seed_url: str | None, query: str) -> str:
    """Palabra clave para /trabajo-de-… en PE: query string `q`, slug en path, o fallback."""
    fb = (query or "").strip() or "empleo"
    if not seed_url or not str(seed_url).strip():
        return fb
    try:
        p = urlparse(str(seed_url).strip())
    except ValueError:
        return fb
    qsd = parse_qs(p.query or "")
    if qsd.get("q") and (qsd["q"][0] or "").strip():
        return (qsd["q"][0] or "").strip()
    path = p.path or ""
    m = re.search(r"/trabajo-de-([^/?#]+)", path, re.I)
    if m:
        slug = unquote(m.group(1))
        return re.sub(r"[-_]+", " ", slug).strip() or fb
    return fb


def _computrabajo_listing_cards(*, base_url: str, html: str, per_page: int) -> list[NormalizedOpportunity]:
    return extract_anchor_cards(
        base_url=base_url,
        html=html,
        source="computrabajo",
        opp_type="empleo",
        limit=per_page,
        **_CT_CARD,
    )


def collect_computrabajo(*, query: str, limit: int = 20, seed_url: str | None = None) -> list[NormalizedOpportunity]:
    # Computrabajo PE: HTML server-side; fetch vía Scrapling (HTTP / sesión / dinámico si aplica).
    kw = _effective_computrabajo_keyword(seed_url, query)
    q_enc = quote_plus(kw)
    base = "https://pe.computrabajo.com/"

    raw: list[NormalizedOpportunity] = []

    if seed_url and str(seed_url).strip():
        su = str(seed_url).strip()
        try:
            host = (urlparse(su).hostname or "").lower()
        except ValueError:
            host = ""
        if "computrabajo.com" in host:
            try:
                html_seed = fetch_html(su, timeout=45)
                raw.extend(_computrabajo_listing_cards(base_url=base, html=html_seed, per_page=max(20, limit)))
            except Exception:  # noqa: BLE001
                pass

    per_page = max(20, min(limit, 120))
    # ~15–25 ofertas por página típico; subir páginas si el admin pide muchas filas.
    pages_to_try = min(35, max(5, (limit + 14) // 15 + 1))

    for p in range(1, pages_to_try + 1):
        url = f"https://pe.computrabajo.com/trabajo-de-{q_enc}?q={q_enc}&p={p}"
        html = fetch_html(url, timeout=45)
        raw.extend(_computrabajo_listing_cards(base_url=base, html=html, per_page=per_page))
        if len(dedupe_by_url(raw)) >= limit:
            break

    items = dedupe_by_url(raw)[:limit]

    # Enrich with detail page (more requirements info) for top N.
    enrich_n = min(max(25, limit // 3), len(items), 60)
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


def collect_getonbrd_latam(*, query: str, limit: int = 25) -> list[NormalizedOpportunity]:
    """Listados remotos/LATAM tech-friendly (HTML cambia; best-effort)."""
    q = quote_plus(query.strip() or "developer")
    url = f"https://www.getonbrd.com/empleos?query={q}"
    html = fetch_html(url, timeout=50)
    items = extract_anchor_cards(
        base_url="https://www.getonbrd.com/",
        html=html,
        source="getonbrd",
        opp_type="empleo",
        card_selector="article, li, div[class*='job'], a[href*='/empleos/']",
        title_selector="h2::text, h3::text, h4::text, p[class*='title']::text, a::text",
        url_selector="a::attr(href)",
        org_selector="span[class*='company']::text, p[class*='company']::text, small::text",
        region_selector="span[class*='location']::text, span[class*='city']::text",
        req_selector="p::text, span::text",
        limit=limit,
        url_must_contain="/empleos/",
        url_must_not_contain="/empresas/",
    )
    bad = {"empleos", "blog", "ingresar", "registr"}
    out = []
    for it in items:
        if it.title.strip().lower() in bad:
            continue
        out.append(
            NormalizedOpportunity(
                title=it.title,
                type="empleo",
                organization=it.organization if it.organization != "—" else "GetOnBoard",
                region=it.region if it.region != "—" else "LATAM / Remoto",
                requirements=it.requirements,
                url=it.url,
                source=it.source,
            )
        )
    return out[:limit]


def collect_laborum_pe(*, query: str, limit: int = 22) -> list[NormalizedOpportunity]:
    """Laborum PE — búsqueda por texto."""
    q = quote_plus(query.strip() or "asistente")
    url = f"https://www.laborum.pe/trabajos?q={q}"
    html = fetch_html(url, timeout=50)
    items = extract_anchor_cards(
        base_url="https://www.laborum.pe/",
        html=html,
        source="laborum",
        opp_type="empleo",
        card_selector="article, div[class*='Card'], div[class*='card'], li[class*='job'], a[href*='/trabajo/']",
        title_selector="h2::text, h3::text, a::text",
        url_selector="a::attr(href)",
        org_selector="span[class*='company']::text, p::text",
        region_selector="span[class*='location']::text, span[class*='place']::text",
        req_selector="p::text, span::text",
        limit=limit,
        url_must_contain="/trabajo",
    )
    return items[:limit]


def collect_jobs(*, query: str, limit: int = 60) -> list[NormalizedOpportunity]:
    items: list[NormalizedOpportunity] = []
    items.extend(collect_computrabajo(query=query, limit=max(20, limit // 2)))
    for fn, cap in (
        (collect_getonbrd_latam, max(8, limit // 5)),
        (collect_laborum_pe, max(8, limit // 5)),
    ):
        try:
            items.extend(fn(query=query, limit=cap))
        except Exception:  # noqa: BLE001
            continue
    try:
        items.extend(collect_bumeran(query=query, limit=max(5, min(18, limit // 4))))
    except Exception:  # noqa: BLE001
        pass
    return dedupe_by_url(items)[:limit]

