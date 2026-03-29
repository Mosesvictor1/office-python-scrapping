"""
HP Laptop Scraper — ScraperAPI + BeautifulSoup
✅ All products saved to a single: hp_scraper_output/laptops/hp.json
✅ Category forced to "laptops" for every product
✅ Key features extracted from keyPoints array in __data__ JSON blob
✅ Specifications extracted from page HTML (dl/table/div patterns)
✅ Spec format matches: [{title, specs: [{key, value}]}]
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
import csv
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
SCRAPER_API_KEY = "74516ff476f866b13129e249ca1fe471"
SCRAPER_API_URL = "http://api.scraperapi.com"

# All output goes under this folder
OUTPUT_DIR      = "hp_scraper_output"
CATEGORY_DIR    = os.path.join(OUTPUT_DIR, "laptops")   # ← single category

# Single JSON file for ALL products
OUTPUT_JSON     = os.path.join(CATEGORY_DIR, "hp.json")
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, "checkpoint.json")
LOG_FILE        = os.path.join(OUTPUT_DIR, "scrape_log.txt")

# HP listing pages
HP_LISTING_URLS = [
    "https://www.hp.com/us-en/shop/vwa/laptops",
    "https://www.hp.com/us-en/shop/vwa/laptops/type=laptop",
    "https://www.hp.com/us-en/shop/cat/laptops",
    "https://www.hp.com/us-en/shop/vwa/laptops/segm=Home",
    "https://www.hp.com/us-en/shop/vwa/laptops/segm=Business",
    "https://www.hp.com/us-en/shop/vwa/laptops/form=Convertible",
    "https://www.hp.com/us-en/shop/cat/gaming-3074457345617980168--1",
]

TARGET_PRODUCTS = 60
MAX_CONCURRENT  = 5
REQUEST_TIMEOUT = 90

_lock = Lock()


# ─────────────────────────────────────────────────────────────
#  SETUP
# ─────────────────────────────────────────────────────────────
def setup_dirs():
    os.makedirs(CATEGORY_DIR, exist_ok=True)
    log(f"📁 Output folder ready: {CATEGORY_DIR}")


def log(msg: str):
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
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
    data = {
        "scraped_urls": list(scraped_urls),
        "products":     products,
        "last_updated": datetime.now().isoformat(),
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
#  SCRAPERAPI FETCH
# ─────────────────────────────────────────────────────────────
def fetch(url: str, render_js: bool = True) -> str | None:
    params = {
        "api_key":      SCRAPER_API_KEY,
        "url":          url,
        "render":       "true" if render_js else "false",
        "country_code": "us",
        "keep_headers": "true",
    }
    try:
        r = requests.get(SCRAPER_API_URL, params=params, timeout=REQUEST_TIMEOUT)
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
#  PHASE 1 — Collect product URLs
# ─────────────────────────────────────────────────────────────
def collect_product_urls_from_page(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen, urls = set(), []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/pdp/" in href or re.search(r"/product/[^/]+/\d+", href):
            full  = href if href.startswith("http") else f"https://www.hp.com{href}"
            clean = full.split("?")[0].rstrip("#reviews").rstrip("/")
            if clean not in seen and "hp.com" in clean:
                seen.add(clean)
                urls.append(clean)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                for el in data.get("itemListElement", []):
                    u = el.get("url") or el.get("item", {}).get("url", "")
                    if u and u not in seen:
                        seen.add(u); urls.append(u)
        except Exception:
            pass

    for a in soup.select("a.product-link, a[data-test-hook*='product'], a.product-tile"):
        href = a.get("href", "")
        if href and href not in seen:
            full = href if href.startswith("http") else f"https://www.hp.com{href}"
            seen.add(href); urls.append(full)

    return urls


def collect_all_product_urls(existing_urls: set) -> list[str]:
    log("\n📋 PHASE 1 — Collecting product URLs from listing pages")
    all_urls: list[str] = []
    seen: set[str]      = set(existing_urls)

    def fetch_listing(listing_url):
        log(f"  📡 Listing: {listing_url}")
        html = fetch(listing_url, render_js=True)
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
    return all_urls[:TARGET_PRODUCTS]


# ─────────────────────────────────────────────────────────────
#  EXTRACT HP __data__ JSON BLOB
#  HP embeds the entire store state inside <!-- {...} --> inside
#  <div id="data"> — this is the richest source of product info.
# ─────────────────────────────────────────────────────────────
def _extract_store_json(soup: BeautifulSoup) -> dict:
    data_div = soup.find("div", {"id": "data"})
    if not data_div:
        return {}
    # Try to get raw text from comment or text node
    raw = data_div.get_text()
    raw = re.sub(r"^<!--\s*", "", raw.strip())
    raw = re.sub(r"\s*-->$",   "", raw.strip())
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _get_components(store: dict) -> dict:
    """Drill into slugInfo.components where all product data lives."""
    return store.get("slugInfo", {}).get("components", {})


# ─────────────────────────────────────────────────────────────
#  JSON-LD Product
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
#  Source priority:
#    1. productInitial.keyPoints  (plain list of strings)
#    2. pdpFeatures.keyPoints     (grouped [{data:[{title,description}]}])
#    3. DOM fallback
# ─────────────────────────────────────────────────────────────
def _get_key_features(soup: BeautifulSoup, prod_initial: dict, pdp_features: dict) -> list[str]:
    features: list[str] = []

    # 1) productInitial.keyPoints — plain list
    kp_list = prod_initial.get("keyPoints", [])
    if kp_list and isinstance(kp_list, list):
        for k in kp_list:
            if isinstance(k, str) and k.strip():
                features.append(k.strip())
        if features:
            return features

    # 2) pdpFeatures.keyPoints — [{data:[{title, description}]}]
    kp_groups = pdp_features.get("keyPoints", [])
    for group in kp_groups:
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
    for sel in [".key-features li", ".product-features li",
                '[data-test-hook="key-features"] li', ".highlights li"]:
        for li in soup.select(sel):
            t = li.get_text(strip=True)
            if t and t not in features:
                features.append(t)
        if features:
            return features

    return features


# ─────────────────────────────────────────────────────────────
#  SPECIFICATIONS
#  HP PDP pages embed tech specs in the HTML.  We look for:
#    • <dl> blocks (most common on HP)
#    • <table> blocks
#    • generic key-value div patterns
#  Format: [{title: str, specs: [{key, value}]}]
# ─────────────────────────────────────────────────────────────
def _get_specifications(soup: BeautifulSoup, prod_initial: dict) -> list[dict]:
    spec_groups: list[dict] = []

    # ── Strategy 1: <dl> blocks ──────────────────────────────
    for dl in soup.find_all("dl"):
        prev        = dl.find_previous(["h2", "h3", "h4"])
        group_title = prev.get_text(strip=True) if prev else "Specifications"
        specs: list[dict] = []
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key   = dt.get_text(strip=True)
            value = dd.get_text(strip=True)
            if key:
                specs.append({"key": key, "value": value})
        if specs:
            spec_groups.append({"title": group_title, "specs": specs})

    if spec_groups:
        return spec_groups

    # ── Strategy 2: <table> blocks ───────────────────────────
    for table in soup.find_all("table"):
        prev        = table.find_previous(["h2", "h3", "h4"])
        group_title = prev.get_text(strip=True) if prev else "Specifications"
        specs: list[dict] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key   = cells[0].get_text(strip=True)
                value = cells[1].get_text(strip=True)
                if key:
                    specs.append({"key": key, "value": value})
        if specs:
            spec_groups.append({"title": group_title, "specs": specs})

    if spec_groups:
        return spec_groups

    # ── Strategy 3: Build specs from productInitial fields ───
    #  HP always has these fields available in the store JSON.
    spec_map = {
        "Operating System":  prod_initial.get("dte_facet_OS", ""),
        "Category":          prod_initial.get("pm_category", ""),
        "Series":            prod_initial.get("pm_series", ""),
        "Model":             prod_initial.get("pm_model", ""),
        "Form Factor":       prod_initial.get("facet_formfactor", ""),
        "Brand":             prod_initial.get("brand", ""),
        "SKU / Part Number": prod_initial.get("mfpartnumber", ""),
        "UPC":               prod_initial.get("upc", ""),
        "Warranty":          BeautifulSoup(
                                 prod_initial.get("wrntyfeatures", ""),
                                 "html.parser"
                             ).get_text(strip=True),
        "Sustainability":    prod_initial.get("sustainability_logo_attribute", ""),
        "Energy Star":       "Yes" if prod_initial.get("energystar") else "No",
        "Country of Origin": prod_initial.get("Country of Origin", ""),
        "AI PC":             prod_initial.get("facet_aipc", ""),
    }

    specs = [
        {"key": k, "value": v}
        for k, v in spec_map.items()
        if v and v.strip()
    ]

    # Add screen size from overViewLabels
    for label in prod_initial.get("overViewLabels", []):
        if label.get("label") and label.get("value"):
            specs.append({"key": label["label"], "value": str(label["value"])})

    if specs:
        spec_groups.append({"title": "General", "specs": specs})

    return spec_groups


# ─────────────────────────────────────────────────────────────
#  REMAINING FIELD HELPERS
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
            curr_map = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR"}
            return digits, curr_map.get(symbol, "USD")

    return "", "USD"


def _get_rating(ld, prod_initial) -> tuple[str, str]:
    if prod_initial.get("rating"):
        val   = str(prod_initial["rating"])
        count = str(prod_initial.get("numReviews", ""))
        return f"{val}/5", count
    agg = ld.get("aggregateRating", {})
    if agg:
        val   = str(agg.get("ratingValue", ""))
        best  = str(agg.get("bestRating", "5"))
        count = str(agg.get("reviewCount") or agg.get("ratingCount", ""))
        return (f"{val}/{best}" if val else ""), count
    return "", ""


def _get_images(ld, pdp_images) -> list[str]:
    images: list[str] = []

    # Prefer fullImages from HP store JSON
    for key in ("fullImages", "mediumImages", "smallImages"):
        img_list = pdp_images.get(key, [])
        for item in img_list:
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

    store      = _extract_store_json(soup)
    components = _get_components(store)

    prod_initial = components.get("productInitial", {})
    prod_price   = components.get("productInitialPrice", {})
    pdp_images   = components.get("pdpImages", {})
    pdp_features = components.get("pdpFeatures", {})

    ld = _extract_json_ld_product(soup)

    title = _get_title(soup, ld, prod_initial)
    if not title:
        return None

    brand       = _get_brand(ld, prod_initial)
    sku         = _get_sku(ld, prod_initial, product_url)
    price, curr = _get_price(soup, ld, prod_price)
    rating, revs= _get_rating(ld, prod_initial)
    images      = _get_images(ld, pdp_images)
    overview    = _get_overview(soup, pdp_features)

    # ── Key features (formatted as list of strings) ──────────
    key_features = _get_key_features(soup, prod_initial, pdp_features)

    # ── Specifications (formatted as [{title, specs:[{key,value}]}]) ─
    specifications = _get_specifications(soup, prod_initial)

    return {
        "title":         title,
        "brand":         brand,
        "sku":           sku,
        "url":           product_url,
        "category":      "laptops",          # ← always "laptops"
        "overview":      overview,
        "price":         price,
        "currency":      curr,
        "rating":        rating,
        "review_count":  revs,
        "availability":  _get_availability(soup, ld),
        "key_features":  key_features,       # ← list of plain strings
        "specifications": specifications,    # ← [{title, specs:[{key,val}]}]
        "images":        images,
        "is_active":     True,
        "featured_product": False,
        "new_arrival":   False,
        "best_seller":   False,
        "is_deleted":    False,
    }


# ─────────────────────────────────────────────────────────────
#  CONCURRENT SCRAPING
# ─────────────────────────────────────────────────────────────
def scrape_one(url: str) -> dict | None:
    html = fetch(url, render_js=True)
    if not html:
        return None
    try:
        product = parse_product_page(html, url)
        if product and product.get("title"):
            return product
    except Exception as e:
        log(f"  ⚠️  Parse error {url[:60]}: {e}")
    return None


def scrape_products_concurrent(urls: list[str],
                                checkpoint: dict,
                                ck_lock: Lock) -> list[dict]:
    products     = list(checkpoint["products"])
    scraped_urls = set(checkpoint["scraped_urls"])
    total        = len(urls)

    log(f"\n🖥️  PHASE 2 — Scraping {total} product pages ({MAX_CONCURRENT} at a time)")

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
                log(f"  [{done}/{total}] ✅ {product['title'][:60]} | "
                    f"{product['price']} {product['currency']}")
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
#  SAVE OUTPUT — single hp.json under laptops/
# ─────────────────────────────────────────────────────────────
def save_json(products: list[dict]):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    log(f"💾 JSON → {OUTPUT_JSON}  ({len(products)} products)")


def save_summary(products: list[dict]):
    summary_path = os.path.join(OUTPUT_DIR, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"HP Laptop Scraper — Run {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Output file  : {OUTPUT_JSON}\n")
        f.write(f"Total products: {len(products)}\n\n")
        for i, p in enumerate(products, 1):
            f.write(f"{i:3}. {p['title'][:70]}\n")
            f.write(f"     SKU: {p['sku']} | Price: {p['price']} {p['currency']} "
                    f"| Rating: {p['rating']} ({p['review_count']} reviews)\n"
                    f"     Key Features: {len(p['key_features'])} items | "
                    f"Spec Groups: {len(p['specifications'])}\n\n")
    log(f"📄 Summary → {summary_path}")


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("  HP Laptop Scraper  |  All products → laptops/hp.json")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    setup_dirs()

    checkpoint     = load_checkpoint()
    scraped_urls   = checkpoint["scraped_urls"]
    existing_prods = checkpoint["products"]

    log(f"  Already scraped: {len(scraped_urls)} URLs, "
        f"{len(existing_prods)} products saved")

    # ── PHASE 1: Collect product URLs ──
    new_urls = collect_all_product_urls(scraped_urls)

    if not new_urls:
        log("⚠️  No new URLs found.")
        if existing_prods:
            save_json(existing_prods)
            save_summary(existing_prods)
        return

    log(f"  New URLs to scrape: {len(new_urls)}")

    # ── PHASE 2: Concurrent scraping ──
    lock     = Lock()
    products = scrape_products_concurrent(new_urls, checkpoint, lock)

    # ── SAVE ALL ──
    print(f"\n{'=' * 65}")
    print(f"  Total products scraped: {len(products)}")
    print(f"  Category: laptops (all products)")
    print(f"  Output  : {OUTPUT_JSON}")
    print("=" * 65)

    if products:
        save_json(products)
        save_summary(products)

        p = products[0]
        print(f"\n📊 Sample (product 1):")
        print(f"  title        : {p['title'][:70]}")
        print(f"  brand        : {p['brand']}")
        print(f"  sku          : {p['sku']}")
        print(f"  price        : {p['price']} {p['currency']}")
        print(f"  rating       : {p['rating']}  ({p['review_count']} reviews)")
        print(f"  category     : {p['category']}")
        print(f"  availability : {p['availability']}")
        print(f"  images       : {len(p['images'])} found")
        print(f"  key_features : {len(p['key_features'])} items")
        if p['key_features']:
            for kf in p['key_features'][:3]:
                print(f"    • {kf[:80]}")
        print(f"  spec groups  : {len(p['specifications'])}")
        if p['specifications']:
            for sg in p['specifications']:
                print(f"    [{sg['title']}] — {len(sg['specs'])} specs")
                for s in sg['specs'][:2]:
                    print(f"      {s['key']}: {s['value'][:60]}")
        print(f"\n📁 File structure:")
        print(f"   {OUTPUT_DIR}/")
        print(f"   ├── laptops/")
        print(f"   │   └── hp.json      ← all {len(products)} products")
        print(f"   ├── checkpoint.json")
        print(f"   ├── summary.txt")
        print(f"   └── scrape_log.txt")
    else:
        print("\n⚠️  No products found.")
        print("   • Check ScraperAPI credits / key")
        print("   • HP may need longer JS render time")
        print("   • Check scrape_log.txt for details")


if __name__ == "__main__":
    main()