"""
currys_scraper.py
──────────────────
Scrapes ANY Currys category with full specs, key features, structured JSON.
- Organized output folders per category
- JSON only (no CSV)
- Auto pagination
- Resume support
- Key features from .item-title elements

TO SWITCH CATEGORY: just change CATEGORY_URL and CATEGORY_NAME below.
"""

import requests
import json
import time
import random
import os
import re
from bs4 import BeautifulSoup

# ╔══════════════════════════════════════════════════════════╗
# ║              CHANGE THESE TO SWITCH CATEGORY             ║
# ╚══════════════════════════════════════════════════════════╝
SCRAPER_API_KEY = "5bffd9495ce68ae340d3b12eea4ffb13"   

CATEGORY_URL  = "https://www.currys.co.uk/health-and-beauty/haircare"
CATEGORY_NAME = "haircare"




CATEGORIES = [
    ("https://www.currys.co.uk/tv-and-audio/televisions","televisions"),
    ("https://www.currys.co.uk/tv-and-audio/tv-accessories","tv-accessories"),
    ("https://www.currys.co.uk/tv-and-audio/digital-and-smart-tv","digital-and-smart-tv"),
    ("https://www.currys.co.uk/brand/sky/sky.html","sky"),
    ("https://www.currys.co.uk/tv-and-audio/dvd-blu-ray-and-home-cinema","dvd-blu-ray-and-home-cinema"),
    ("https://www.currys.co.uk/computing/projectors","projectors"),
    ("https://www.currys.co.uk/tv-and-audio/speakers-and-hi-fi-systems","speakers-and-hi-fi-systems"),
    ("https://www.currys.co.uk/tv-and-audio/audio-accessories-and-cables","audio-accessories-and-cables"),
    ("https://www.currys.co.uk/tv-and-audio/mp3-and-cd-players","mp3-and-cd-players"),
    ("https://www.currys.co.uk/tv-and-audio/headphones","headphones"),
    ("https://www.currys.co.uk/tv-and-audio/radios","radios"),
    ("https://www.currys.co.uk/tv-and-audio/record-players","record-players"),
]





# ── Other categories (uncomment the one you want) ──────────
# CATEGORY_URL  = "https://www.currys.co.uk/computing/laptops"
# CATEGORY_NAME = "laptops"

# CATEGORY_URL  = "https://www.currys.co.uk/computing/computer-monitors"
# CATEGORY_NAME = "monitors"

# CATEGORY_URL  = "https://www.currys.co.uk/televisions"
# CATEGORY_NAME = "televisions"

# CATEGORY_URL  = "https://www.currys.co.uk/mobile-phones"
# CATEGORY_NAME = "mobile-phones"

# CATEGORY_URL  = "https://www.currys.co.uk/headphones"
# CATEGORY_NAME = "headphones"

# CATEGORY_URL  = "https://www.currys.co.uk/washing-machines"
# CATEGORY_NAME = "washing-machines"

# CATEGORY_URL  = "https://www.currys.co.uk/fridges-freezers"
# CATEGORY_NAME = "fridges-freezers"

# CATEGORY_URL  = "https://www.currys.co.uk/cameras"
# CATEGORY_NAME = "cameras"
# ───────────────────────────────────────────────────────────

MAX_PAGES       = 10
SCRAPER_API_URL = "https://api.scraperapi.com/"
BASE_URL        = "https://www.currys.co.uk"

# Output folder per category
OUTPUT_DIR    = os.path.join("output", CATEGORY_NAME)
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "progress.json")
URLS_FILE     = os.path.join(OUTPUT_DIR, "urls.txt")
FINAL_JSON    = os.path.join(OUTPUT_DIR, f"{CATEGORY_NAME}.json")

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch(url, render_js=False, retries=3):
    params = {
        "api_key":      SCRAPER_API_KEY,
        "url":          url,
        "render":       "true" if render_js else "false",
        "country_code": "uk",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(SCRAPER_API_URL, params=params, timeout=90)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 429:
                print(f"    ⏳  Rate limited — waiting 15s...")
                time.sleep(15)
            else:
                print(f"    ⚠️  HTTP {resp.status_code} (attempt {attempt})")
        except Exception as e:
            print(f"    ❌  Attempt {attempt} failed: {e}")
            time.sleep(3)
    return None


# ── GET PRODUCT URLS FROM A LISTING PAGE ──────────────────────────────────────
def get_product_urls(html):
    soup = BeautifulSoup(html, "lxml")
    urls = []
    seen = set()

    # JSON-LD ItemList (most reliable)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                for entry in data.get("itemListElement", []):
                    item = entry.get("item") or entry
                    url  = item.get("url", "")
                    if url:
                        full = url if url.startswith("http") else BASE_URL + url
                        if full not in seen:
                            seen.add(full)
                            urls.append(full)
        except Exception:
            continue

    if urls:
        return urls

    # Fallback: anchor tags with /products/
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/products/" not in href:
            continue
        if any(p.name in ("nav", "header", "footer") for p in a.parents):
            continue
        full = href if href.startswith("http") else BASE_URL + href
        if full not in seen:
            seen.add(full)
            urls.append(full)

    return urls


# ── COLLECT ALL URLS WITH PAGINATION ──────────────────────────────────────────
def collect_all_urls():
    all_urls  = []
    seen_urls = set()

    print(f"  📄  Page 1: {CATEGORY_URL}")
    html = fetch(CATEGORY_URL, render_js=True)
    if not html:
        print("  ❌  Failed to fetch category page")
        return []

    urls = get_product_urls(html)
    new  = [u for u in urls if u not in seen_urls]
    seen_urls.update(new)
    all_urls.extend(new)
    print(f"  ✅  +{len(new)} | total: {len(all_urls)}")

    page_size         = len(new) if new else 20
    start             = page_size
    page_num          = 2
    consecutive_empty = 0

    while start < 5000 and page_num <= MAX_PAGES:
        page_url = f"{CATEGORY_URL}?start={start}&sz={page_size}"
        print(f"  📄  Page {page_num} (start={start}): {page_url}")

        html = fetch(page_url, render_js=True)
        if not html:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                break
            start    += page_size
            page_num += 1
            continue

        urls = get_product_urls(html)
        new  = [u for u in urls if u not in seen_urls]

        if not new:
            consecutive_empty += 1
            print(f"  🏁  No new products — stopping pagination")
            if consecutive_empty >= 2:
                break
        else:
            consecutive_empty = 0
            seen_urls.update(new)
            all_urls.extend(new)
            print(f"  ✅  +{len(new)} | total: {len(all_urls)}")

        start    += page_size
        page_num += 1
        time.sleep(random.uniform(1, 2))

    return all_urls


# ── PARSE KEY FEATURES ────────────────────────────────────────────────────────
def parse_key_features(soup):
    """
    Extracts from:
    <div class="key-features-container">
      <div class="item"><span class="item-title">Windows 11</span></div>
    """
    features = []

    # Primary: .key-features-container .item-title
    container = soup.find(class_="key-features-container")
    if container:
        for span in container.find_all(class_="item-title"):
            txt = span.get_text(strip=True)
            if txt and len(txt) > 2:
                features.append(txt)

    if features:
        return features

    # Fallback: .pdp-item-features .item-title
    container2 = soup.find(class_="pdp-item-features")
    if container2:
        for span in container2.find_all(class_="item-title"):
            txt = span.get_text(strip=True)
            if txt and len(txt) > 2:
                features.append(txt)

    if features:
        return features

    # Last fallback: any list inside description
    desc = soup.find(class_=re.compile(r'description|overview|summary', re.I))
    if desc:
        for li in desc.find_all("li"):
            txt = li.get_text(strip=True)
            if txt and len(txt) > 5:
                features.append(txt)

    return features[:8]


# ── PARSE SPECIFICATIONS ──────────────────────────────────────────────────────
def parse_specs(soup):
    specs_structured = []
    seen_groups      = {}

    for table in soup.find_all("div", class_="tech-specification-table"):
        caption_el  = table.find(class_="tech-specification-caption")
        group_title = caption_el.get_text(strip=True).title() if caption_el else "General"

        for body in table.find_all(class_="tech-specification-body"):
            key_el = body.find(class_="tech-specification-th")
            val_el = body.find(class_="tech-specification-td")
            if not key_el or not val_el:
                continue

            key = key_el.get_text(strip=True)
            for br in val_el.find_all("br"):
                br.replace_with(", ")
            value = val_el.get_text(strip=True)
            value = re.sub(r'\s*,\s*-\s*', ', ', value)
            value = re.sub(r'^-\s*', '', value)
            value = re.sub(r',\s*,', ',', value).strip()

            if not key:
                continue

            if group_title in seen_groups:
                seen_groups[group_title].append({"key": key, "value": value})
            else:
                seen_groups[group_title] = [{"key": key, "value": value}]

    for title, specs in seen_groups.items():
        specs_structured.append({"title": title, "specs": specs})

    return specs_structured


# ── HELPERS ───────────────────────────────────────────────────────────────────
def extract_colour(soup, specs):
    for group in specs:
        for spec in group.get("specs", []):
            if spec["key"].lower() in ("colour", "color"):
                return [spec["value"]]
    swatches = soup.find_all(class_=re.compile(r'colour|color|swatch', re.I))
    colours  = []
    for s in swatches:
        txt = s.get("title") or s.get("aria-label") or s.get_text(strip=True)
        if txt and len(txt) < 30:
            colours.append(txt)
    return list(set(colours))[:5] if colours else []


def extract_warranty(specs):
    for group in specs:
        for spec in group.get("specs", []):
            if "guarantee" in spec["key"].lower() or "warranty" in spec["key"].lower():
                return spec["value"]
    return "N/A"


def build_subtitle(full_name):
    parts = full_name.split(" - ")[0] if " - " in full_name else full_name
    words = parts.split()
    return " ".join(words[:4]) if len(words) > 4 else parts


def restructure(raw, soup):
    full_name    = raw.get("full_name") or "N/A"
    specs        = raw.get("specifications", [])
    colour       = extract_colour(soup, specs)
    warranty     = extract_warranty(specs)
    key_features = raw.get("key_features", [])

    avail_raw = raw.get("availability", "")
    available = "instock" in avail_raw.lower() if avail_raw else False

    rating_raw = raw.get("rating", "N/A")
    try:
        rating = f"{float(rating_raw)}/5"
    except Exception:
        rating = rating_raw

    return {
        "title":            full_name,
        "subtitle":         build_subtitle(full_name),
        "brand":            raw.get("brand", "N/A"),
        "sku":              raw.get("sku", "N/A"),
        "url":              raw.get("url", ""),
        "category":         CATEGORY_NAME,
        "overview":         raw.get("description", "N/A"),
        "price":            raw.get("price", "N/A"),
        "currency":         raw.get("currency", "GBP"),
        "color":            colour,
        "rating":           rating,
        "review_count":     raw.get("review_count", "0"),
        "availability":     available,
        "key_features":     key_features,
        "specifications":   specs,
        "images":           raw.get("images", []),
        "warranty":         warranty,
        "is_active":        True,
        "featured_product": False,
        "new_arrival":      False,
        "best_seller":      False,
        "is_deleted":       False,
    }


# ── SCRAPE PRODUCT DETAIL ─────────────────────────────────────────────────────
def scrape_detail(url):
    html = fetch(url, render_js=False)
    if not html:
        html = fetch(url, render_js=True)
    if not html:
        return {}, None

    soup = BeautifulSoup(html, "lxml")
    raw  = {"url": url}

    # Core info from JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if not isinstance(data, dict):
                continue
            if "@graph" in data:
                for node in data["@graph"]:
                    if isinstance(node, dict) and node.get("@type") == "Product":
                        data = node
                        break
            if data.get("@type") != "Product":
                continue

            raw["full_name"]    = data.get("name", "N/A")
            brand               = data.get("brand", {})
            raw["brand"]        = brand.get("name", "N/A") if isinstance(brand, dict) else str(brand)
            raw["sku"]          = data.get("sku") or data.get("productID") or "N/A"
            raw["description"]  = (data.get("description") or "")[:1000]

            agg                 = data.get("aggregateRating") or {}
            raw["rating"]       = str(agg.get("ratingValue", "N/A")) if isinstance(agg, dict) else "N/A"
            raw["review_count"] = str(agg.get("reviewCount", "0")) if isinstance(agg, dict) else "0"

            offers              = data.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            raw["price"]        = str(offers.get("price", "N/A"))
            raw["currency"]     = offers.get("priceCurrency", "GBP")
            raw["availability"] = offers.get("availability", "").replace("https://schema.org/", "")

            imgs          = data.get("image", [])
            raw["images"] = ([imgs] if isinstance(imgs, str) else imgs[:5]) if imgs else []
            break
        except Exception:
            continue

    # Fallbacks
    if not raw.get("full_name") or raw["full_name"] == "N/A":
        h1 = soup.find("h1")
        raw["full_name"] = h1.get_text(strip=True) if h1 else "N/A"

    if not raw.get("description"):
        meta = soup.find("meta", attrs={"name": "description"}) or \
               soup.find("meta", attrs={"property": "og:description"})
        if meta:
            raw["description"] = meta.get("content", "")[:1000]

    # Specs and key features
    raw["specifications"] = parse_specs(soup)
    raw["key_features"]   = parse_key_features(soup)

    spec_count = sum(len(g.get("specs", [])) for g in raw["specifications"])
    feat_count = len(raw["key_features"])
    print(f"    📋  {spec_count} specs | ⚡ {feat_count} key features")

    return raw, soup


# ── PROGRESS ──────────────────────────────────────────────────────────────────
def save_progress(products):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


# ── FINAL SAVE ────────────────────────────────────────────────────────────────
def save_final(products):
    with open(FINAL_JSON, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    print(f"💾  JSON → {FINAL_JSON}")
    print(f"📊  {len(products)} products saved")

    if products:
        p = products[0]
        print(f"\n{'─'*60}")
        print(f"  title:        {p.get('title')}")
        print(f"  subtitle:     {p.get('subtitle')}")
        print(f"  brand:        {p.get('brand')}")
        print(f"  price:        £{p.get('price')}")
        print(f"  rating:       {p.get('rating')} ({p.get('review_count')} reviews)")
        print(f"  availability: {p.get('availability')}")
        print(f"  color:        {p.get('color')}")
        print(f"  warranty:     {p.get('warranty')}")
        print(f"  key_features: {p.get('key_features')}")
        print(f"  specs groups: {len(p.get('specifications', []))}")
        print(f"  images:       {len(p.get('images', []))} images")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print(f"🚀  Currys Scraper — {CATEGORY_NAME.upper()}")
    print(f"🔗  {CATEGORY_URL}")
    print("=" * 60)

    # Resume or fresh start
    if os.path.exists(PROGRESS_FILE):
        answer = input("\n⚡  Found previous progress. Resume? (y/n): ").strip().lower()
        if answer != "y":
            os.remove(PROGRESS_FILE)
            if os.path.exists(URLS_FILE):
                os.remove(URLS_FILE)
            print("  Starting fresh...\n")

    # STEP 1: Collect all URLs
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, "r") as f:
            all_urls = [line.strip() for line in f if line.strip()]
        print(f"\n📋  Loaded {len(all_urls)} URLs from {URLS_FILE}")
    else:
        print(f"\n📋  STEP 1 — Collecting all {CATEGORY_NAME} URLs...")
        all_urls = collect_all_urls()
        all_urls = list(dict.fromkeys(all_urls))  # deduplicate
        with open(URLS_FILE, "w") as f:
            f.write("\n".join(all_urls))
        print(f"\n✅  {len(all_urls)} unique URLs found")
        print(f"💾  Saved → {URLS_FILE}")

    # STEP 2: Scrape details
    print(f"\n📦  STEP 2 — Scraping {len(all_urls)} products...")
    done         = load_progress()
    done_set     = {p["url"] for p in done}
    remaining    = [u for u in all_urls if u not in done_set]
    all_products = done.copy()

    print(f"  Already done: {len(done)} | Remaining: {len(remaining)}\n")

    for i, url in enumerate(remaining):
        num  = len(all_products) + 1
        slug = url.rstrip("/").split("/")[-1][:55]
        print(f"  [{num}/{len(all_urls)}] {slug}")

        raw, soup = scrape_detail(url)
        if not raw:
            continue

        product = restructure(raw, soup)
        all_products.append(product)
        print(f"    ✅  {product.get('title','N/A')[:55]}  |  £{product.get('price','N/A')}")

        if (i + 1) % 10 == 0:
            save_progress(all_products)
            print(f"    💾  Checkpoint: {len(all_products)} saved")

        time.sleep(random.uniform(1, 2))

    # STEP 3: Final save
    print(f"\n{'='*60}")
    save_final(all_products)
    save_progress(all_products)
    print(f"\n🎉  Done! {len(all_products)} {CATEGORY_NAME} scraped.")
    print(f"📁  {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()