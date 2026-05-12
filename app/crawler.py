import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import logging

logger = logging.getLogger(__name__)

# Paths to skip
SKIP_PATTERNS = [
    r"/privacy", r"/policy", r"/offer", r"/ofer", r"/login", r"/register",
    r"/cart", r"/checkout", r"/compare", r"/favorite", r"/account",
    r"/public-offer", r"/confidential", r"\?", r"#", r"\.pdf", r"\.jpg",
    r"\.png", r"\.xml", r"/feed", r"/rss", r"/sitemap",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SEOAgentBot/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
}


def should_skip(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(re.search(p, path) for p in SKIP_PATTERNS)


def classify_page(url: str, soup: BeautifulSoup) -> str:
    path = urlparse(url).path.strip("/")
    if not path or path == "":
        return "главная"
    segments = path.split("/")
    # Product pages often have long slugs or model numbers
    if len(segments) >= 3:
        return "товар"
    # Brand/category pages
    if "catalog" in path or "watches" in path or "brands" in path:
        if len(segments) >= 2:
            return "категория/бренд"
        return "каталог"
    if "sale" in path or "new" in path or "hit" in path:
        return "промо"
    if "about" in path:
        return "о компании"
    if "service" in path or "delivery" in path or "dostavka" in path:
        return "сервис"
    return "страница"


async def discover_pages(base_url: str, max_pages: int = 15) -> list[dict]:
    """Crawl the site and discover important pages."""
    domain = urlparse(base_url).netloc
    visited = set()
    to_visit = [base_url]
    pages = []

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15.0) as client:
        while to_visit and len(visited) < max_pages * 3:
            url = to_visit.pop(0)
            norm = url.rstrip("/")
            if norm in visited:
                continue
            if urlparse(url).netloc != domain:
                continue
            if should_skip(url):
                continue
            visited.add(norm)

            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                ct = resp.headers.get("content-type", "")
                if "text/html" not in ct:
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                page_type = classify_page(url, soup)

                pages.append({
                    "url": url.rstrip("/"),
                    "title": title,
                    "type": page_type,
                })

                # Collect links for further crawling
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    full = urljoin(url, href).split("#")[0].split("?")[0]
                    if urlparse(full).netloc == domain and full.rstrip("/") not in visited:
                        to_visit.append(full)

            except Exception as e:
                logger.warning(f"Failed to fetch {url}: {e}")
                continue

    # Prioritize: categories & products first, skip duplicates
    priority = {"главная": 0, "категория/бренд": 1, "каталог": 2, "промо": 3, "товар": 4, "страница": 8, "о компании": 9, "сервис": 9}
    pages.sort(key=lambda p: priority.get(p["type"], 5))
    return pages[:max_pages]


async def fetch_page_content(url: str) -> dict:
    """Fetch a page and extract structured content."""
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    # Extract meta
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    meta_desc = ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag:
        meta_desc = meta_tag.get("content", "")
    meta_kw = ""
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    if kw_tag:
        meta_kw = kw_tag.get("content", "")

    h1 = ""
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(strip=True)

    # Separate content zones
    # Main content: try common selectors
    main_selectors = ["main", "article", "[role=main]", ".content", ".product", ".catalog",
                      "#content", ".main-content", ".page-content"]
    main_el = None
    for sel in main_selectors:
        main_el = soup.select_one(sel)
        if main_el:
            break
    if not main_el:
        main_el = soup.find("body")

    # Navigation / footer text (technical)
    nav_texts = []
    for tag in soup.find_all(["nav", "header", "footer"]):
        nav_texts.append(tag.get_text(" ", strip=True))
        tag.decompose()  # remove from main content calc

    # Forms text
    for tag in soup.find_all("form"):
        nav_texts.append(tag.get_text(" ", strip=True))
        tag.decompose()

    # All remaining text = content
    body_text = soup.get_text(" ", strip=True) if soup.find("body") else ""

    nav_text = " ".join(nav_texts)

    # Links text
    link_words = []
    for a in soup.find_all("a"):
        link_words.append(a.get_text(strip=True))

    return {
        "url": url,
        "title": title,
        "h1": h1,
        "meta_description": meta_desc,
        "meta_keywords": meta_kw,
        "body_text": body_text,
        "nav_text": nav_text,
        "link_texts": " ".join(link_words),
    }
