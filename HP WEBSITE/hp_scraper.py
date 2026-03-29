"""
HP Laptop Scraper v3 — ScraperAPI + BeautifulSoup
==================================================
✅ All products saved to: hp_scraper_output/laptops/hp.json
✅ Category forced to "laptops" for every product
✅ Key features from keyPoints array in __data__ JSON blob
✅ Specifications via 5-layer fallback strategy:
     1. HP async GraphQL API  (/app/api/web/graphql/page/pdp%2F{slug}/async)
     2. Known JSON component keys (pdpTechSpecs, techSpecs, specifications …)
     3. Auto-scan all components for spec-shaped data
     4. Rendered HTML patterns (dl, table, data-* attrs, CSS classes)
     5. productInitial scalar fields (last resort)
✅ Spec format: [{title, specs: [{key, value}]}]
✅ Concurrent scraping with resume/checkpoint support

Install:
    pip install requests beautifulsoup4

Run:
    python hp_laptop_scraper.py
"""

import requests
from bs4 import BeautifulSoup
import json
import re
import os
import urllib.parse
import sys
import ast
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
SCRAPER_API_KEY = "a04fa88ccb23ac55c1c31c16d1493f32"
SCRAPER_API_URL = "http://api.scraperapi.com"

OUTPUT_DIR      = "hp_scraper_output"
CATEGORY_DIR    = os.path.join(OUTPUT_DIR, "laptops")
OUTPUT_JSON     = os.path.join(CATEGORY_DIR, "hp.json")
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint.json")
LOG_FILE        = os.path.join(OUTPUT_DIR, "scrape_log.txt")

HP_LISTING_URLS = [
    "https://www.hp.com/us-en/shop/vwa/laptops",
    "https://www.hp.com/us-en/shop/vwa/laptops/type=laptop",
    "https://www.hp.com/us-en/shop/cat/laptops",
    "https://www.hp.com/us-en/shop/vwa/laptops/segm=Home",
    "https://www.hp.com/us-en/shop/vwa/laptops/segm=Business",
    "https://www.hp.com/us-en/shop/vwa/laptops/segm=Enterprise",
    "https://www.hp.com/us-en/shop/vwa/laptops/segm=Small-Office",
    "https://www.hp.com/us-en/shop/vwa/laptops/segm=Small-medium-business",
    "https://www.hp.com/us-en/shop/vwa/laptops/form=Convertible",
    "https://www.hp.com/us-en/shop/vwa/laptops/form=Mobile-workstation",
    "https://www.hp.com/us-en/shop/vwa/laptops/form=Mobile-thin-client",
    "https://www.hp.com/us-en/shop/vwa/laptops/form=Standard-laptop",
    "https://www.hp.com/us-en/shop/cat/gaming-3074457345617980168--1",
]

# Desired TOTAL number of products in the final JSON (including already scraped ones).
# The scraper will resume from checkpoint and scrape only the remaining count.
TARGET_TOTAL_PRODUCTS = 100
MAX_CONCURRENT  = 5
REQUEST_TIMEOUT = 90  # seconds for product pages
ASYNC_TIMEOUT   = 60  # seconds for async API calls (no JS render needed)
LISTING_RENDER_JS = False  # speed: listings are usually server-rendered enough

_lock = Lock()


# ─────────────────────────────────────────────────────────────
#  SETUP & LOGGING
# ─────────────────────────────────────────────────────────────
def setup_dirs():
    os.makedirs(CATEGORY_DIR, exist_ok=True)
    log(f"📁 Output folder ready: {CATEGORY_DIR}")


def log(msg: str):
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        safe = line.encode(enc, errors="replace").decode(enc, errors="replace")
        print(safe)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
#  CHECKPOINT
# ─────────────────────────────────────────────────────────────
def load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            scraped = set(data.get("scraped_urls", []))
            log(f"♻️  Resuming — {len(scraped)} URLs already scraped")
            return {"scraped_urls": scraped, "products": data.get("products", [])}
        except Exception as e:
            log(f"⚠️  Checkpoint load error: {e} — starting fresh")
    return {"scraped_urls": set(), "products": []}


def save_checkpoint(scraped_urls: set, products: list):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "scraped_urls": list(scraped_urls),
            "products":     products,
            "last_updated": datetime.now().isoformat(),
        }, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
#  SCRAPERAPI FETCH
#  render_js=True  → full JS render (needed for product pages)
#  render_js=False → plain HTTP (fast, enough for JSON APIs)
# ─────────────────────────────────────────────────────────────
def fetch(url: str, render_js: bool = True, timeout: int = REQUEST_TIMEOUT) -> str | None:
    params = {
        "api_key":      SCRAPER_API_KEY,
        "url":          url,
        "render":       "true" if render_js else "false",
        "country_code": "us",
        "keep_headers": "true",
    }
    try:
        r = requests.get(SCRAPER_API_URL, params=params, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.exceptions.HTTPError as e:
        log(f"  ❌ HTTP {e} — {url[:80]}")
    except requests.exceptions.Timeout:
        log(f"  ⏱️  Timeout — {url[:80]}")
    except requests.exceptions.RequestException as e:
        log(f"  ❌ Error — {url[:80]}: {e}")
    return None


# ─────────────────────────────────────────────────────────────
#  PHASE 1 — Collect product URLs from listing pages
# ─────────────────────────────────────────────────────────────
def collect_product_urls_from_page(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen, urls = set(), []

    def _canonicalize(u: str) -> str:
        u = (u or "").strip()
        if not u:
            return ""
        if u.startswith("/"):
            u = f"https://www.hp.com{u}"
        # remove query + fragment
        u = u.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        return u

    def _push(full_url: str):
        c = _canonicalize(full_url)
        if c and c not in seen and "hp.com" in c:
            seen.add(c)
            urls.append(c)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/pdp/" in href or re.search(r"/product/[^/]+/\d+", href):
            full = href if href.startswith("http") else f"https://www.hp.com{href}"
            _push(full)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                for el in data.get("itemListElement", []):
                    u = el.get("url") or el.get("item", {}).get("url", "")
                    if u:
                        _push(u)
        except Exception:
            pass

    for a in soup.select("a.product-link, a[data-test-hook*='product'], a.product-tile"):
        href = a.get("href", "")
        if href:
            full = href if href.startswith("http") else f"https://www.hp.com{href}"
            _push(full)

    return urls


def collect_all_product_urls(existing_urls: set, limit: int | None = None) -> list[str]:
    log("\n📋 PHASE 1 — Collecting product URLs from listing pages")
    all_urls: list[str] = []
    seen: set[str]      = set(existing_urls)

    def fetch_listing(listing_url):
        log(f"  📡 Listing: {listing_url}")
        html = fetch(listing_url, render_js=LISTING_RENDER_JS)
        if not html:
            return []
        found = collect_product_urls_from_page(html)
        log(f"  🔗 Found {len(found)} URLs")
        return found

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_listing, u): u for u in HP_LISTING_URLS}
        for future in as_completed(futures):
            for url in future.result():
                if url not in seen:
                    seen.add(url)
                    all_urls.append(url)

    log(f"\n✅ Total unique new product URLs: {len(all_urls)}")
    if limit is None:
        return all_urls
    if limit <= 0:
        return []
    return all_urls[:limit]


# ─────────────────────────────────────────────────────────────
#  EXTRACT HP __data__ STORE JSON
#  HP embeds full store state in <div id="data"><!-- {...} -->
# ─────────────────────────────────────────────────────────────
def _extract_store_json(soup: BeautifulSoup) -> dict:
    # Primary: <div id="data">
    data_div = soup.find("div", {"id": "data"})
    if data_div:
        raw = re.sub(r"^<!--\s*", "", data_div.get_text().strip())
        raw = re.sub(r"\s*-->$", "", raw)
        try:
            return json.loads(raw)
        except Exception:
            pass

    # Fallback: window.__data__ or window.__STORE__ in <script>
    for script in soup.find_all("script"):
        txt = script.string or ""
        for pattern in [
            r"window\.__data__\s*=\s*({.+});?\s*$",
            r"window\.__STORE__\s*=\s*({.+});?\s*$",
        ]:
            m = re.search(pattern, txt, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
    return {}


def _get_components(store: dict) -> dict:
    return store.get("slugInfo", {}).get("components", {})


# ─────────────────────────────────────────────────────────────
#  JSON-LD Product schema
# ─────────────────────────────────────────────────────────────
def _extract_json_ld_product(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data  = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "Product":
                    return item
        except Exception:
            pass
    return {}


# ─────────────────────────────────────────────────────────────
#  KEY FEATURES
#  Priority: productInitial.keyPoints → pdpFeatures.keyPoints → DOM
# ─────────────────────────────────────────────────────────────
def _get_key_features(soup: BeautifulSoup, prod_initial: dict, pdp_features: dict) -> list[str]:
    features: list[str] = []

    # 1) productInitial.keyPoints — plain list of strings
    for k in prod_initial.get("keyPoints", []):
        if isinstance(k, str) and k.strip():
            features.append(k.strip())
    if features:
        return features

    # 2) pdpFeatures.keyPoints — [{data:[{title, description}]}]
    for group in pdp_features.get("keyPoints", []):
        for item in group.get("data", []):
            title = item.get("title", "").strip()
            desc  = BeautifulSoup(item.get("description", ""), "html.parser").get_text(strip=True)
            if title and desc:
                features.append(f"{title}: {desc}")
            elif title:
                features.append(title)
    if features:
        return features

    # 3) DOM fallback
    for sel in [
        ".key-features li", ".product-features li",
        '[data-test-hook="key-features"] li', ".highlights li",
    ]:
        for li in soup.select(sel):
            t = li.get_text(strip=True)
            if t and t not in features:
                features.append(t)
        if features:
            return features

    return features


# ─────────────────────────────────────────────────────────────
#  SPECIFICATION HELPERS
# ─────────────────────────────────────────────────────────────
def _clean_html(text: str) -> str:
    """Strip HTML tags from a string."""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)


def _strip_spec_citations(text: str) -> str:
    """
    HP frequently appends citation markers like:
      "Windows 11 Pro [1]"
      "100% sRGB [10,12,29,33]"
    Remove those citation brackets.
    """
    if not isinstance(text, str):
        text = str(text)
    # Remove things like [1] or [10,12,29,33]
    text = re.sub(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", "", text).strip()
    return text


def _normalise_spec_value(raw) -> str:
    """
    Convert any HP spec value shape into a clean plain string.

    Sometimes HP returns nested shapes like:
      [{'value': ['Windows 11 Pro [1] ']}]
    which previously got stringified into the output.
    """
    if raw is None:
        return ""

    # Direct primitive
    if isinstance(raw, (str, int, float, bool)):
        return _strip_spec_citations(_clean_html(str(raw))).strip()

    # Dict: often contains another "value"
    if isinstance(raw, dict):
        if "value" in raw:
            return _normalise_spec_value(raw.get("value"))
        if "attributeValue" in raw:
            return _normalise_spec_value(raw.get("attributeValue"))
        # fallback
        return _strip_spec_citations(_clean_html(str(raw))).strip()

    # List: flatten and join
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict) and "value" in item:
                parts.append(_normalise_spec_value(item.get("value")))
            else:
                v = _normalise_spec_value(item)
                if v:
                    parts.append(v)
        parts = [p for p in parts if p]
        if not parts:
            return ""
        return _strip_spec_citations(_clean_html(", ".join(parts))).strip()

    # If HP gives us a stringified python literal, try to eval it.
    if isinstance(raw, str):
        s = _clean_html(raw).strip()
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            if "value" in s:
                try:
                    parsed = ast.literal_eval(s)
                    return _normalise_spec_value(parsed)
                except Exception:
                    pass
        return _strip_spec_citations(s).strip()

    # Fallback
    return _strip_spec_citations(_clean_html(str(raw))).strip()


def _nearest_heading(el) -> str:
    """Find the nearest preceding heading for a DOM element."""
    for tag in ["h2", "h3", "h4", "h5"]:
        prev = el.find_previous(tag)
        if prev:
            return prev.get_text(strip=True)
    return ""


def _normalise_spec_groups(raw) -> list[dict]:
    """
    Convert any of HP's spec data shapes into the standard format:
      [{title: str, specs: [{key, value}]}]

    Handles all known HP shapes:
      A) [{categoryName, specs:[{name,value}]}]           — pdpTechSpecs
      B) [{title, data:[{name,value}|{key,value}]}]       — techSpecs
      C) {sections:[{title, items:[{label,value}]}]}      — productDetails
      D) [{label, value}]                                 — flat list
      E) {<groupTitle>: [{key,value}]}                    — dict-of-lists
    """
    if not raw:
        return []
    groups: list[dict] = []

    # Shape C — dict with "sections"
    if isinstance(raw, dict) and "sections" in raw:
        for section in raw["sections"]:
            title = section.get("title", "Specifications")
            specs = [
                {
                    "key":   (i.get("label") or i.get("key") or i.get("name") or "").strip(),
                    "value": _normalise_spec_value(i.get("value", "")),
                }
                for i in section.get("items", [])
                if (i.get("label") or i.get("key") or i.get("name"))
            ]
            if specs:
                groups.append({"title": title, "specs": specs})
        return groups

    # Shape E — plain dict of group→list
    if isinstance(raw, dict):
        for group_title, items in raw.items():
            if not isinstance(items, list):
                continue
            specs = [
                {
                    "key":   (i.get("key") or i.get("name") or i.get("label") or "").strip(),
                    "value": _normalise_spec_value(i.get("value", "")),
                }
                for i in items
                if isinstance(i, dict) and (i.get("key") or i.get("name") or i.get("label"))
            ]
            if specs:
                groups.append({"title": group_title, "specs": specs})
        return groups

    if not isinstance(raw, list):
        return []

    # Shape D — flat list of {label/key, value}
    if raw and isinstance(raw[0], dict) and (
        "label" in raw[0] or ("key" in raw[0] and "value" in raw[0])
    ) and "data" not in raw[0] and "specs" not in raw[0] and "categoryName" not in raw[0]:
        specs = [
            {
                "key":   (i.get("label") or i.get("key") or i.get("name") or "").strip(),
                "value": _normalise_spec_value(i.get("value", "")),
            }
            for i in raw
            if (i.get("label") or i.get("key") or i.get("name"))
        ]
        if specs:
            groups.append({"title": "Specifications", "specs": specs})
        return groups

    # Shapes A & B — list of group objects
    for group in raw:
        if not isinstance(group, dict):
            continue
        title = (
            group.get("categoryName") or group.get("title") or
            group.get("groupTitle") or group.get("name") or "Specifications"
        ).strip()
        items = (
            group.get("specs") or group.get("data") or
            group.get("items") or group.get("attributes") or []
        )
        specs = []
        for item in items:
            if not isinstance(item, dict):
                continue
            k = (
                item.get("name") or item.get("key") or
                item.get("label") or item.get("attributeName") or ""
            ).strip()
            v = item.get("value") or item.get("attributeValue") or ""
            v = _normalise_spec_value(v)
            if k:
                specs.append({"key": k, "value": v})
        if specs:
            groups.append({"title": title, "specs": specs})

    return groups


def _looks_like_specs(groups: list[dict]) -> bool:
    """Heuristic: does this data actually look like hardware specs?"""
    if not groups or sum(len(g.get("specs", [])) for g in groups) < 2:
        return False
    hardware_hints = {
        "processor", "memory", "storage", "display", "os", "operating",
        "battery", "graphics", "weight", "dimension", "camera", "audio",
        "connectivity", "wireless", "bluetooth", "usb", "screen", "cpu",
        "ram", "ssd", "gpu", "chipset", "resolution", "warranty", "neural",
    }
    for g in groups:
        for s in g.get("specs", []):
            if any(h in s.get("key", "").lower() for h in hardware_hints):
                return True
    return False


# ─────────────────────────────────────────────────────────────
#  STRATEGY 1 — HP Async GraphQL/API Endpoint
#
#  HP loads full tech specs asynchronously via:
#    /us-en/shop/app/api/web/graphql/page/pdp%2F{slug}/async
#  This is a plain JSON response — no JS render needed, fast & cheap.
# ─────────────────────────────────────────────────────────────
def _fetch_async_specs(product_url: str) -> list[dict]:
    m = re.search(r"/pdp/([^/?#]+)", product_url)
    if not m:
        return []

    slug        = m.group(1)
    encoded     = urllib.parse.quote(f"pdp/{slug}", safe="")
    async_url   = f"https://www.hp.com/us-en/shop/app/api/web/graphql/page/{encoded}/async"

    log(f"  🔌 Async API: {async_url[:90]}")
    raw = fetch(async_url, render_js=False, timeout=ASYNC_TIMEOUT)
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except Exception:
        return []

    # Navigate to components — HP nests async responses differently across PDPs.
    # Known working path (as of 2026-03): data.page.pageComponents
    data_root = data.get("data", {}) if isinstance(data, dict) else {}
    page = data_root.get("page", {}) if isinstance(data_root, dict) else {}
    components = (
        (data.get("slugInfo", {}) or {}).get("components")
        or data.get("components")
        or (data_root.get("components") if isinstance(data_root, dict) else None)
        or (page.get("pageComponents") if isinstance(page, dict) else None)
        or {}
    )

    # Check all known spec component keys
    for key in ("pdpTechSpecs", "techSpecs", "specifications",
                "productDetails", "productTechSpecs", "techSpecifications"):
        raw_specs = components.get(key)
        if not raw_specs:
            continue
        groups = _normalise_spec_groups(raw_specs)
        if groups and _looks_like_specs(groups):
            log(f"  ✅ Async specs via component['{key}'] — "
                f"{len(groups)} groups, {sum(len(g['specs']) for g in groups)} specs")
            return groups

    # If component keys not found, try parsing the raw async JSON directly
    # (some HP pages return a flat spec structure at the top level)
    for key in ("pdpTechSpecs", "techSpecs", "specifications"):
        raw_specs = data.get(key)
        if raw_specs:
            groups = _normalise_spec_groups(raw_specs)
            if groups and _looks_like_specs(groups):
                log(f"  ✅ Async specs via top-level['{key}']")
                return groups

    return []


def _scrape_via_async_only(product_url: str) -> dict | None:
    """
    Fallback when ScraperAPI render=true fails (e.g., 500s).
    Builds a minimal product record using the async JSON response.
    """
    m = re.search(r"/pdp/([^/?#]+)", product_url)
    if not m:
        return None

    slug = m.group(1)
    encoded = urllib.parse.quote(f"pdp/{slug}", safe="")
    async_url = f"https://www.hp.com/us-en/shop/app/api/web/graphql/page/{encoded}/async"
    raw = fetch(async_url, render_js=False, timeout=ASYNC_TIMEOUT)
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except Exception:
        return None

    data_root = data.get("data", {}) if isinstance(data, dict) else {}
    page = data_root.get("page", {}) if isinstance(data_root, dict) else {}
    page_components = page.get("pageComponents", {}) if isinstance(page, dict) else {}

    prod_initial = page_components.get("productInitial", {}) if isinstance(page_components, dict) else {}
    prod_price = page_components.get("productInitialPrice", {}) if isinstance(page_components, dict) else {}

    # Title
    title = (prod_initial.get("name") or page.get("title") or "").strip()
    if not title:
        # last resort: use slug
        title = slug.replace("-", " ").strip()

    # SKU
    sku = ""
    for k in ("sku", "mfpartnumber", "upc"):
        v = prod_initial.get(k)
        if v:
            sku = str(v).strip()
            break

    # Price
    price = ""
    if prod_price.get("salePrice"):
        price = str(prod_price["salePrice"])
    elif prod_price.get("regularPrice"):
        price = str(prod_price["regularPrice"])

    specs = _fetch_async_specs(product_url)

    return {
        "title":            title,
        "brand":            (prod_initial.get("brand") or "HP").strip() if isinstance(prod_initial.get("brand"), str) else "HP",
        "sku":              sku,
        "url":              product_url,
        "category":         "laptops",
        "overview":         "",
        "price":            price,
        "currency":         "USD",
        "rating":           "",
        "review_count":     "",
        "availability":     True,
        "key_features":     [],
        "specifications":   specs,
        "images":           [],
        "is_active":        True,
        "featured_product": False,
        "new_arrival":      False,
        "best_seller":      False,
        "is_deleted":       False,
    }


# ─────────────────────────────────────────────────────────────
#  STRATEGY 2 — Known JSON Component Keys (from rendered page)
# ─────────────────────────────────────────────────────────────
def _specs_from_component_keys(components: dict) -> list[dict]:
    SPEC_KEYS = [
        "pdpTechSpecs", "techSpecs", "specifications",
        "productDetails", "pdpOverview", "productTechSpecs", "techSpecifications",
    ]
    for key in SPEC_KEYS:
        raw = components.get(key)
        if raw:
            groups = _normalise_spec_groups(raw)
            if groups and _looks_like_specs(groups):
                log(f"  ✅ Specs from component['{key}']")
                return groups
    return []


# ─────────────────────────────────────────────────────────────
#  STRATEGY 3 — productInitial nested keys
# ─────────────────────────────────────────────────────────────
def _specs_from_prod_initial_nested(prod_initial: dict) -> list[dict]:
    for key in ("techSpecs", "specifications", "techSpecifications", "specs"):
        raw = prod_initial.get(key)
        if raw:
            groups = _normalise_spec_groups(raw)
            if groups and _looks_like_specs(groups):
                log(f"  ✅ Specs from productInitial.{key}")
                return groups
    return []


# ─────────────────────────────────────────────────────────────
#  STRATEGY 4 — Auto-scan all components for spec-shaped data
# ─────────────────────────────────────────────────────────────
def _specs_from_component_scan(components: dict) -> list[dict]:
    skip = {
        "productInitial", "productInitialPrice", "pdpImages",
        "pdpFeatures", "recommendations", "breadcrumb",
        "pdpTechSpecs", "techSpecs", "specifications",
        "productDetails", "pdpOverview", "productTechSpecs", "techSpecifications",
    }
    for key, val in components.items():
        if key in skip:
            continue
        groups = _normalise_spec_groups(val)
        if groups and _looks_like_specs(groups):
            log(f"  ✅ Specs auto-detected in component['{key}']")
            return groups
    return []


# ─────────────────────────────────────────────────────────────
#  STRATEGY 5 — Rendered HTML patterns
#  ScraperAPI with render=true gives us the full JS-rendered DOM,
#  so React-injected elements ARE present in the HTML.
# ─────────────────────────────────────────────────────────────
def _specs_from_rendered_html(soup: BeautifulSoup) -> list[dict]:

    # 5a — <dl> blocks (most common HP spec pattern)
    groups, seen = [], set()
    for dl in soup.find_all("dl"):
        dts, dds = dl.find_all("dt"), dl.find_all("dd")
        if not dts:
            continue
        specs = []
        for dt, dd in zip(dts, dds):
            k, v = dt.get_text(" ", strip=True), dd.get_text(" ", strip=True)
            if k and (k, v) not in seen:
                seen.add((k, v))
                specs.append({"key": k, "value": v})
        if specs:
            groups.append({"title": _nearest_heading(dl) or "Specifications", "specs": specs})
    if groups and _looks_like_specs(groups):
        log(f"  ✅ Specs from HTML <dl> — {len(groups)} groups")
        return groups

    # 5b — <table> blocks
    groups = []
    for table in soup.find_all("table"):
        specs = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                k = cells[0].get_text(" ", strip=True)
                v = cells[1].get_text(" ", strip=True)
                if k:
                    specs.append({"key": k, "value": v})
        if specs:
            groups.append({"title": _nearest_heading(table) or "Specifications", "specs": specs})
    if groups and _looks_like_specs(groups):
        log(f"  ✅ Specs from HTML <table> — {len(groups)} groups")
        return groups

    # 5c — data-test-hook attributes (HP uses these for QA hooks on spec sections)
    groups = []
    for section in soup.find_all(attrs={"data-test-hook": True}):
        hook = section.get("data-test-hook", "")
        if not any(x in hook.lower() for x in ["spec", "tech"]):
            continue
        title   = (section.find(["h2", "h3", "h4"]) or object())
        title   = title.get_text(strip=True) if hasattr(title, "get_text") else "Specifications"
        specs   = []
        for row in section.find_all(["tr", "li"]):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                k = cells[0].get_text(strip=True)
                v = cells[1].get_text(strip=True)
                if k and v and k != v:
                    specs.append({"key": k, "value": v})
        if specs:
            groups.append({"title": title, "specs": specs})
    if groups and _looks_like_specs(groups):
        log(f"  ✅ Specs from data-test-hook sections")
        return groups

    # 5d — CSS class-based spec rows (HP-specific class names)
    all_specs, seen = [], set()
    for row_sel, label_sel, val_sel in [
        (".spec-row",       ".spec-label",      ".spec-value"),
        (".spec-item",      ".spec-name",        ".spec-value"),
        (".tech-specs-row", ".tech-spec-label",  ".tech-spec-value"),
        (".pdp-spec-row",   ".pdp-spec-label",   ".pdp-spec-value"),
    ]:
        for row in soup.select(row_sel):
            le = row.select_one(label_sel)
            ve = row.select_one(val_sel)
            if le and ve:
                k, v = le.get_text(strip=True), ve.get_text(" ", strip=True)
                if k and (k, v) not in seen:
                    seen.add((k, v))
                    all_specs.append({"key": k, "value": v})

    # 5e — Generic class pattern: any element with "spec" in its class
    for spec_section in soup.find_all(class_=lambda c: c and any(
        x in " ".join(c) for x in ["spec-section", "tech-spec", "pdpTechSpec"]
    )):
        title = "Specifications"
        h = spec_section.find(["h2", "h3", "h4", "strong"])
        if h:
            title = h.get_text(strip=True)
        for row in spec_section.find_all(class_=lambda c: c and "row" in str(c).lower()):
            label = row.find(class_=lambda c: c and "label" in str(c).lower())
            value = row.find(class_=lambda c: c and "value" in str(c).lower())
            if label and value:
                k, v = label.get_text(strip=True), value.get_text(strip=True)
                pair = (k, v)
                if k and pair not in seen:
                    seen.add(pair)
                    all_specs.append({"key": k, "value": v})

    # 5f — data-spec-name / data-spec-value attributes
    for el in soup.find_all(attrs={"data-spec-name": True}):
        k = el.get("data-spec-name", "").strip()
        v = (el.get("data-spec-value") or el.get_text(strip=True)).strip()
        pair = (k, v)
        if k and pair not in seen:
            seen.add(pair)
            all_specs.append({"key": k, "value": v})

    if all_specs:
        groups = [{"title": "Specifications", "specs": all_specs}]
        if _looks_like_specs(groups):
            log(f"  ✅ Specs from HTML CSS/data-attr patterns — {len(all_specs)} specs")
            return groups

    return []


# ─────────────────────────────────────────────────────────────
#  STRATEGY 6 — Last resort: productInitial scalar fields
#  Always returns something, even if minimal.
# ─────────────────────────────────────────────────────────────
def _specs_from_prod_initial_scalars(soup: BeautifulSoup, prod_initial: dict) -> list[dict]:
    spec_map = {
        "Operating System":  prod_initial.get("dte_facet_OS", ""),
        "Category":          prod_initial.get("pm_category", ""),
        "Series":            prod_initial.get("pm_series", ""),
        "Model":             prod_initial.get("pm_model", ""),
        "Form Factor":       prod_initial.get("facet_formfactor", ""),
        "Brand":             prod_initial.get("brand", ""),
        "SKU / Part Number": prod_initial.get("mfpartnumber", ""),
        "UPC":               prod_initial.get("upc", ""),
        "Warranty":          _clean_html(prod_initial.get("wrntyfeatures", "")),
        "Sustainability":    prod_initial.get("sustainability_logo_attribute", ""),
        "Energy Star":       "Yes" if prod_initial.get("energystar") else "No",
        "Country of Origin": prod_initial.get("Country of Origin", ""),
        "AI PC":             prod_initial.get("facet_aipc", ""),
    }
    specs = [
        {"key": k, "value": str(v)}
        for k, v in spec_map.items()
        if v and str(v).strip()
    ]
    for label in prod_initial.get("overViewLabels", []):
        if label.get("label") and label.get("value"):
            specs.append({"key": label["label"], "value": str(label["value"])})

    if specs:
        log(f"  ⚠️  Specs from productInitial scalars only ({len(specs)} fields)")
        return [{"title": "General", "specs": specs}]
    return []


# ─────────────────────────────────────────────────────────────
#  MASTER SPEC EXTRACTOR — runs all strategies in priority order
# ─────────────────────────────────────────────────────────────
def _get_specifications(
    soup: BeautifulSoup,
    components: dict,
    prod_initial: dict,
    product_url: str,
) -> list[dict]:
    """
    5-layer spec extraction. Returns the first strategy that yields
    real hardware spec data. Falls back to scalar fields if all else fails.
    """

    # 1. HP async GraphQL API (fastest path to full specs)
    result = _fetch_async_specs(product_url)
    if result:
        return result

    # 2. Known JSON component keys from the rendered page store
    result = _specs_from_component_keys(components)
    if result:
        return result

    # 3. productInitial nested tech spec arrays
    result = _specs_from_prod_initial_nested(prod_initial)
    if result:
        return result

    # 4. Auto-scan all components for spec-shaped data
    result = _specs_from_component_scan(components)
    if result:
        return result

    # 5. Rendered HTML patterns (dl, table, CSS classes, data-attrs)
    result = _specs_from_rendered_html(soup)
    if result:
        return result

    # 6. Last resort — scalar fields from productInitial
    return _specs_from_prod_initial_scalars(soup, prod_initial)


# ─────────────────────────────────────────────────────────────
#  FIELD HELPERS
# ─────────────────────────────────────────────────────────────
def _get_title(soup, ld, prod_initial) -> str:
    if prod_initial.get("name"):
        return prod_initial["name"].strip()
    if ld.get("name"):
        return ld["name"].strip()
    for sel in ["h1.product-title", "h1.pdp-title", "h1"]:
        el = soup.select_one(sel)
        if el:
            return el.get_text(strip=True)
    return ""


def _get_brand(ld, prod_initial) -> str:
    if prod_initial.get("brand"):
        return prod_initial["brand"].strip()
    brand = ld.get("brand", {})
    if isinstance(brand, dict):
        return brand.get("name", "HP").strip()
    return str(brand).strip() if brand else "HP"


def _get_sku(ld, prod_initial, url) -> str:
    for key in ("sku", "mfpartnumber", "upc"):
        v = prod_initial.get(key)
        if v:
            return str(v).strip()
    if ld.get("sku"):
        return str(ld["sku"]).strip()
    m = re.search(r"/([A-Za-z0-9#]+)(?:\?|$)", url)
    return m.group(1) if m else ""


def _get_price(soup, ld, prod_price) -> tuple[str, str]:
    if prod_price.get("salePrice"):
        return str(prod_price["salePrice"]), "USD"
    if prod_price.get("regularPrice"):
        return str(prod_price["regularPrice"]), "USD"

    offer = ld.get("offers", {})
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    if offer.get("price"):
        return str(offer["price"]), offer.get("priceCurrency", "USD")

    for sel in [".price", ".product-price", '[data-test-hook="price"]',
                ".pdp-price", ".current-price", "span.price"]:
        el = soup.select_one(sel)
        if el:
            raw    = el.get_text(strip=True)
            digits = re.sub(r"[^\d.]", "", raw)
            symbol = raw[0] if raw and not raw[0].isdigit() else ""
            return digits, {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR"}.get(symbol, "USD")

    return "", "USD"


def _get_rating(ld, prod_initial) -> tuple[str, str]:
    if prod_initial.get("rating"):
        return f"{prod_initial['rating']}/5", str(prod_initial.get("numReviews", ""))
    agg = ld.get("aggregateRating", {})
    if agg:
        val  = str(agg.get("ratingValue", ""))
        best = str(agg.get("bestRating", "5"))
        cnt  = str(agg.get("reviewCount") or agg.get("ratingCount", ""))
        return (f"{val}/{best}" if val else ""), cnt
    return "", ""


def _get_images(ld, pdp_images) -> list[str]:
    images: list[str] = []
    for key in ("fullImages", "mediumImages", "smallImages"):
        for item in pdp_images.get(key, []):
            if not isinstance(item, dict) or item.get("type") == "video":
                continue
            url = item.get("url", "")
            if url and isinstance(url, str) and url not in images:
                images.append(url)
        if images:
            break

    if not images:
        raw = ld.get("image", [])
        if isinstance(raw, str):
            raw = [raw]
        for item in raw:
            if isinstance(item, str) and item:
                images.append(item)
            elif isinstance(item, dict):
                src = item.get("contentUrl") or item.get("url") or item.get("@id")
                if src and isinstance(src, str):
                    images.append(src)

    clean = []
    for u in images:
        if not isinstance(u, str) or not u.strip():
            continue
        u = u.strip()
        if u.startswith("/"):
            u = f"https://www.hp.com{u}"
        clean.append(u)
    return clean


def _get_overview(soup, pdp_features) -> str:
    desc_list = pdp_features.get("description", [])
    if desc_list:
        raw = " ".join(desc_list)
        return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)
    for sel in [".product-description", ".pdp-description",
                ".product-overview", ".overview-text", ".cust-html"]:
        el = soup.select_one(sel)
        if el:
            return el.get_text(separator=" ", strip=True)
    meta = (soup.find("meta", {"name": "description"}) or
            soup.find("meta", {"property": "og:description"}))
    if meta:
        return meta.get("content", "").strip()
    return ""


def _get_availability(soup, ld) -> bool:
    offer = ld.get("offers", {})
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    avail = offer.get("availability", "")
    if avail:
        return "InStock" in avail
    meta = soup.find("meta", {"itemprop": "availability"})
    if meta:
        return "InStock" in meta.get("content", "")
    return True


# ─────────────────────────────────────────────────────────────
#  PARSE ONE PRODUCT PAGE
# ─────────────────────────────────────────────────────────────
def parse_product_page(html: str, product_url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")

    store        = _extract_store_json(soup)
    components   = _get_components(store)
    prod_initial = components.get("productInitial", {})
    prod_price   = components.get("productInitialPrice", {})
    pdp_images   = components.get("pdpImages", {})
    pdp_features = components.get("pdpFeatures", {})
    ld           = _extract_json_ld_product(soup)

    title = _get_title(soup, ld, prod_initial)
    if not title:
        return None

    brand, sku          = _get_brand(ld, prod_initial), _get_sku(ld, prod_initial, product_url)
    price, curr         = _get_price(soup, ld, prod_price)
    rating, revs        = _get_rating(ld, prod_initial)
    images              = _get_images(ld, pdp_images)
    overview            = _get_overview(soup, pdp_features)
    key_features        = _get_key_features(soup, prod_initial, pdp_features)

    # Run all 5 spec strategies
    specifications = _get_specifications(soup, components, prod_initial, product_url)

    return {
        "title":            title,
        "brand":            brand,
        "sku":              sku,
        "url":              product_url,
        "category":         "laptops",
        "overview":         overview,
        "price":            price,
        "currency":         curr,
        "rating":           rating,
        "review_count":     revs,
        "availability":     _get_availability(soup, ld),
        "key_features":     key_features,
        "specifications":   specifications,
        "images":           images,
        "is_active":        True,
        "featured_product": False,
        "new_arrival":      False,
        "best_seller":      False,
        "is_deleted":       False,
    }


# ─────────────────────────────────────────────────────────────
#  CONCURRENT SCRAPING
# ─────────────────────────────────────────────────────────────
def scrape_one(url: str) -> dict | None:
    html = fetch(url, render_js=True)
    if not html:
        # When HP/ScraperAPI has intermittent render failures, still try async-only path.
        product = _scrape_via_async_only(url)
        return product if product and product.get("title") else None
    try:
        product = parse_product_page(html, url)
        if product and product.get("title"):
            return product
    except Exception as e:
        log(f"  ⚠️  Parse error {url[:60]}: {e}")
    return None


def scrape_products_concurrent(urls: list[str], checkpoint: dict, ck_lock: Lock) -> list[dict]:
    products     = list(checkpoint["products"])
    scraped_urls = set(checkpoint["scraped_urls"])
    total        = len(urls)

    log(f"\n🖥️  PHASE 2 — Scraping {total} product pages ({MAX_CONCURRENT} concurrent)")

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as pool:
        future_to_url = {pool.submit(scrape_one, url): url for url in urls}
        done = 0
        for future in as_completed(future_to_url):
            url   = future_to_url[future]
            done += 1
            try:
                product = future.result()
            except Exception as e:
                log(f"  [{done}/{total}] ❌ Exception for {url[:60]}: {e}")
                product = None

            if product:
                total_specs = sum(len(g["specs"]) for g in product["specifications"])
                log(
                    f"  [{done}/{total}] ✅ {product['title'][:50]} | "
                    f"{product['price']} {product['currency']} | "
                    f"{len(product['specifications'])} groups / {total_specs} specs"
                )
                with ck_lock:
                    scraped_urls.add(url)
                    products.append(product)
                    save_checkpoint(scraped_urls, products)
            else:
                log(f"  [{done}/{total}] ⚠️  Skipped: {url[:60]}")
                with ck_lock:
                    scraped_urls.add(url)
                    save_checkpoint(scraped_urls, products)

    return products


# ─────────────────────────────────────────────────────────────
#  SAVE OUTPUT
# ─────────────────────────────────────────────────────────────
def save_json(products: list[dict]):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    log(f"💾 JSON → {OUTPUT_JSON}  ({len(products)} products)")


def save_summary(products: list[dict]):
    summary_path = os.path.join(OUTPUT_DIR, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"HP Laptop Scraper v3 — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Output : {OUTPUT_JSON}\n")
        f.write(f"Total  : {len(products)} products\n\n")
        for i, p in enumerate(products, 1):
            total_specs = sum(len(g["specs"]) for g in p["specifications"])
            f.write(
                f"{i:3}. {p['title'][:70]}\n"
                f"     SKU: {p['sku']} | ${p['price']} | "
                f"{p['rating']} ({p['review_count']} reviews)\n"
                f"     Features: {len(p['key_features'])} | "
                f"Spec groups: {len(p['specifications'])} / {total_specs} specs\n\n"
            )
    log(f"📄 Summary → {summary_path}")


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("  HP Laptop Scraper v3  |  laptops/hp.json")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    setup_dirs()

    checkpoint     = load_checkpoint()
    scraped_urls   = checkpoint["scraped_urls"]
    existing_prods = checkpoint["products"]

    log(f"  Already scraped: {len(scraped_urls)} URLs, {len(existing_prods)} products")

    remaining = max(0, TARGET_TOTAL_PRODUCTS - len(existing_prods))
    if remaining <= 0:
        log(f"✅ Target reached: {len(existing_prods)}/{TARGET_TOTAL_PRODUCTS} products already scraped")
        if existing_prods:
            save_json(existing_prods)
            save_summary(existing_prods)
        return

    new_urls = collect_all_product_urls(scraped_urls, limit=remaining)

    if not new_urls:
        log("⚠️  No new URLs found.")
        if existing_prods:
            save_json(existing_prods)
            save_summary(existing_prods)
        return

    log(f"  New URLs to scrape: {len(new_urls)}")

    lock     = Lock()
    products = scrape_products_concurrent(new_urls, checkpoint, lock)

    print(f"\n{'=' * 65}")
    print(f"  Total products: {len(products)}")
    print(f"  Output        : {OUTPUT_JSON}")
    print("=" * 65)

    if products:
        save_json(products)
        save_summary(products)

        p           = products[0]
        total_specs = sum(len(g["specs"]) for g in p["specifications"])
        print(f"\n📊 Sample — {p['title'][:60]}")
        print(f"  SKU        : {p['sku']}")
        print(f"  Price      : ${p['price']} {p['currency']}")
        print(f"  Rating     : {p['rating']} ({p['review_count']} reviews)")
        print(f"  Features   : {len(p['key_features'])} items")
        print(f"  Spec groups: {len(p['specifications'])} / {total_specs} total specs")

        for sg in p["specifications"]:
            print(f"    [{sg['title']}] — {len(sg['specs'])} specs")
            for s in sg["specs"][:2]:
                print(f"      {s['key']}: {s['value'][:60]}")

        print(f"\n📁 {OUTPUT_DIR}/")
        print(f"   ├── laptops/hp.json  ({len(products)} products)")
        print(f"   ├── checkpoint.json")
        print(f"   ├── summary.txt")
        print(f"   └── scrape_log.txt")
    else:
        print("\n⚠️  No products scraped.")
        print("   • Check ScraperAPI credits/key")
        print("   • Review scrape_log.txt for errors")


if __name__ == "__main__":
    main()