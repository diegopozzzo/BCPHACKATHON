## Scrapling (D4Vinci/Scrapling) — knowledge for this repo

Reference: [Scrapling repo](https://github.com/D4Vinci/Scrapling)

### What Scrapling gives us

- **Parser**: a fast DOM selector engine with Scrapy/Parsel-like CSS/XPath selectors (`page.css(...)`, `page.xpath(...)`) plus BS4-like helpers (`find_all`, `find_by_text`).
- **Adaptive scraping**: selectors can be **auto-saved** and later “relocated” if the site changes (`auto_save=True`, then `adaptive=True`).
- **Fetchers**:
  - `Fetcher` / `FetcherSession`: fast HTTP requests (optionally “impersonate” browser TLS fingerprints).
  - `StealthyFetcher` / `StealthySession`: headless browser + stealth bypass for protected pages (Cloudflare Turnstile, etc.).
  - `DynamicFetcher` / `DynamicSession`: Playwright-based browser fetching for JS-heavy pages.
- **Spiders**: Scrapy-like concurrent crawling with pause/resume, multi-session routing (`sid="fast"|"stealth"`), streaming results.
- **Robots.txt support**: optional compliance (`robots_txt_obey` in Spider settings).

### Core API patterns we use

- **Simple fetch + parse**
  - `from scrapling.fetchers import Fetcher`
  - `page = Fetcher.get(url)` or `Fetcher.fetch(url, ...)` depending on fetcher type.
  - Extract with selectors: `page.css("a::attr(href)").getall()`, `page.css("h1::text").get()`.

- **Session (cookies / reuse headers)**
  - `from scrapling.fetchers import FetcherSession`
  - `with FetcherSession(impersonate="chrome") as s: page = s.get(url, stealthy_headers=True)`

- **When a site blocks HTTP**
  - Use `StealthySession`/`StealthyFetcher` (browser-based), at the cost of performance.

### Operational notes

- Installing `scrapling[fetchers]` enables the fetchers. Some fetchers require browsers to be installed via:
  - `scrapling install`
  - (This repo keeps scraping adapters compatible with pure HTTP first; switch to stealth/dynamic per source if needed.)

### Normalization for this hackathon

We normalize every extracted item (jobs, courses, volunteering) to the existing DB schema (`Opportunity`):

- `title`: string
- `type`: `"empleo" | "curso" | "voluntariado"`
- `organization`: string
- `region`: string (e.g. `"Lima"`, `"Remoto"`, etc.)
- `requirements`: string (free text summary / tags)
- `url`: canonical link

