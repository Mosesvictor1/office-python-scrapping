"""
mg_stationery_scraper.py
─────────────────────────
Scrapes ALL 24 categories from mgstationeryonline.com.my
- No ScraperAPI or ZenRows needed
- Direct requests — plain HTML, no JS rendering
- HikaShop (Joomla) e-commerce structure
- Same JSON format as Currys scraper
- 3 categories running concurrently
- Resume support per category
"""

import requests
import json
import time
import random
import os
import re
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
BASE_URL             = "https://mgstationeryonline.com.my"
MAX_PAGES            = 20
MAX_CATEGORY_THREADS = 3
BASE_OUTPUT_DIR      = "output/mg-stationery"

CATEGORIES = [
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/gel-pen",               "gel-pen"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/ball-point-pen",        "ball-point-pen"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/table-pen",             "table-pen"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/correction-series",     "correction-series"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/glue-series",           "glue-series"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/highlighter",           "highlighter"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/marker",                "marker"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/sticky-note",           "sticky-note"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/mechanical-pencil",     "mechanical-pencil"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/pencil-lead",           "pencil-lead"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/gel-pen-refill",        "gel-pen-refill"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/math-instrument",       "math-instrument"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/calculator",            "calculator"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/file",                  "file"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/id-card-holder-lanyard","id-card-holder-lanyard"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/punch",                 "punch"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/stapler",               "stapler"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/scissors",              "scissors"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/utility-knife",         "utility-knife"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/art-series",            "art-series"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/pen-holder",            "pen-holder"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/eraser",                "eraser"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/pencil-sharpener",      "pencil-sharpener"),
    ("https://mgstationeryonline.com.my/en/mg-stationery-johor-bahru/gaspard-et-lisa",       "gaspard-et-lisa"),
]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

print_lock = Lock()


def tprint(msg):
    with print_lock:
        print(msg)


# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.text
            else:
                tprint(f"    ⚠️  HTTP {resp.status_code} (attempt {attempt})")
                time.sleep(2)
        except Exception as e:
            tprint(f"    ❌  Attempt {attempt} failed: {e}")
            time.sleep(2)
    return None


# ── GET PRODUCT URLS ──────────────────────────────────────────────────────────
def get_product_urls(html):
    soup = BeautifulSoup(html, "lxml")
    urls = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Product links use /product/ pattern on this site
        # e.g. /en/mg-stationery-johor-bahru/gel-pen/product/307-m-g-gel-pen-0-5-r3
        if "/product/" not in href:
            continue
        # Skip cart, wishlist, category links
        if any(x in href for x in ["updatecart", "wishlist", "/category/", "addtowishlist"]):
            continue
        full  = href if href.startswith("http") else BASE_URL + href
        clean = full.split("?")[0]
        # Ensure English version
        if "/ms/" in clean:
            clean = clean.replace("/ms/", "/en/")
        if clean not in seen:
            seen.add(clean)
            urls.append(clean)

    return urls


# ── CHECK NEXT PAGE ───────────────────────────────────────────────────────────
def has_next_page(html):
    soup    = BeautifulSoup(html, "lxml")
    pager   = soup.find(class_=re.compile(r'pager|pagination|hikashop_pager', re.I))
    if pager:
        if pager.find("a", string=re.compile(r'next|›|»|>', re.I)):
            return True
        if pager.find("a", rel="next"):
            return True
    return False


# ── COLLECT ALL URLS ──────────────────────────────────────────────────────────
def collect_all_urls(category_url, category_name):
    all_urls  = []
    seen_urls = set()

    for page_num in range(1, MAX_PAGES + 1):
        page_url = category_url if page_num == 1 else f"{category_url}?limitstart={(page_num-1)*12}"

        tprint(f"  [{category_name}] 📄 Page {page_num}")
        html = fetch(page_url)
        if not html:
            break

        urls = get_product_urls(html)
        new  = [u for u in urls if u not in seen_urls]

        if not new:
            tprint(f"  [{category_name}] 🏁 No new products")
            break

        seen_urls.update(new)
        all_urls.extend(new)
        tprint(f"  [{category_name}] ✅ +{len(new)} | total: {len(all_urls)}")

        if not has_next_page(html):
            tprint(f"  [{category_name}] 🏁 Last page")
            break

        time.sleep(random.uniform(0.5, 1.0))

    return all_urls


# ── EXTRACT IMAGES ────────────────────────────────────────────────────────────
def extract_images(soup):
    images = []
    seen   = set()

    for el in soup.find_all("img"):
        src = el.get("src", "")
        if "com_hikashop/upload" not in src:
            continue
        # Get full resolution instead of thumbnail
        full = src.replace("/thumbnails/500x500f/", "/").replace("/thumbnails/400x400f/", "/")
        if not full.startswith("http"):
            full = BASE_URL + full
        if full not in seen:
            seen.add(full)
            images.append(full)
        if len(images) >= 6:
            break

    return images


# ── EXTRACT COLOURS ───────────────────────────────────────────────────────────
def extract_colours(soup):
    colours = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            if "color" in cells[0].get_text(strip=True).lower():
                val = cells[1].get_text(strip=True)
                for c in re.split(r'[,/\n|]', val):
                    c = c.strip()
                    if c and len(c) < 30:
                        colours.append(c)
                break
    return colours[:10]


# ── EXTRACT PRICE ─────────────────────────────────────────────────────────────
def extract_price(soup):
    for el in soup.find_all(class_=re.compile(r'price|hikashop_product_price', re.I)):
        txt = el.get_text(strip=True)
        m   = re.search(r'RM\s*([\d,]+\.?\d*)', txt)
        if m:
            return m.group(1).replace(",", "")
    for el in soup.find_all(string=re.compile(r'RM\s*[\d.]+', re.I)):
        m = re.search(r'RM\s*([\d,]+\.?\d*)', str(el))
        if m:
            return m.group(1).replace(",", "")
    return "N/A"


# ── EXTRACT SKU ───────────────────────────────────────────────────────────────
def extract_sku(soup):
    for tag in soup.find_all(["h4", "strong", "p", "span"]):
        txt = tag.get_text(strip=True)
        m   = re.match(r'^([A-Z]{2,}\d{3,}(?:[-][A-Z0-9]+)?)$', txt)
        if m:
            return m.group(1)
    return "N/A"


# ── EXTRACT DESCRIPTION ───────────────────────────────────────────────────────
def extract_description(soup):
    desc_el = soup.find(class_=re.compile(r'hikashop_product_description|product.?description', re.I))
    if desc_el:
        return desc_el.get_text(separator=" ", strip=True)[:1000]
    paragraphs = soup.find_all("p")
    longest    = max((p.get_text(strip=True) for p in paragraphs), key=len, default="")
    return longest[:1000] if len(longest) > 50 else "N/A"


# ── EXTRACT KEY FEATURES ──────────────────────────────────────────────────────
def extract_key_features(soup):
    features = []
    desc_el  = soup.find(class_=re.compile(r'hikashop_product_description|product.?description', re.I))
    if desc_el:
        for li in desc_el.find_all("li"):
            txt = li.get_text(strip=True)
            if txt and len(txt) > 3:
                features.append(txt)
    return features[:8]


# ── RESTRUCTURE ───────────────────────────────────────────────────────────────
def restructure(raw, category_name):
    full_name = raw.get("full_name", "N/A")
    words     = full_name.split()
    subtitle  = " ".join(words[:4]) if len(words) > 4 else full_name

    return {
        "title":            full_name,
        "subtitle":         subtitle,
        "brand":            "M&G",
        "sku":              raw.get("sku", "N/A"),
        "url":              raw.get("url", ""),
        "category":         category_name,
        "overview":         raw.get("description", "N/A"),
        "price":            raw.get("price", "N/A"),
        "currency":         "MYR",
        "color":            raw.get("colors", []),
        "rating":           "N/A",
        "review_count":     "0",
        "availability":     True,
        "key_features":     raw.get("key_features", []),
        "specifications":   [],
        "images":           raw.get("images", []),
        "warranty":         "N/A",
        "is_active":        True,
        "featured_product": False,
        "new_arrival":      False,
        "best_seller":      False,
        "is_deleted":       False,
    }


# ── SCRAPE PRODUCT DETAIL ─────────────────────────────────────────────────────
def scrape_detail(url, category_name):
    html = fetch(url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "lxml")
    raw  = {"url": url}

    name_el          = soup.find(class_=re.compile(r'hikashop_product_name', re.I)) or soup.find("h1")
    raw["full_name"] = name_el.get_text(strip=True) if name_el else "N/A"
    raw["full_name"] = re.sub(r'\s+[A-Z]{2,}\d{3,}[-]?[A-Z0-9]*$', '', raw["full_name"]).strip()

    raw["sku"]         = extract_sku(soup)
    raw["price"]       = extract_price(soup)
    raw["description"] = extract_description(soup)
    raw["key_features"]= extract_key_features(soup)
    raw["images"]      = extract_images(soup)
    raw["colors"]      = extract_colours(soup)

    tprint(f"    [{category_name}] 🖼️ {len(raw['images'])} imgs | 🎨 {len(raw['colors'])} colors | RM {raw['price']}")

    return raw


# ── SCRAPE ONE CATEGORY ───────────────────────────────────────────────────────
def scrape_category(category_url, category_name):
    output_dir    = os.path.join(BASE_OUTPUT_DIR, category_name)
    progress_file = os.path.join(output_dir, "progress.json")
    urls_file     = os.path.join(output_dir, "urls.txt")
    final_json    = os.path.join(output_dir, f"{category_name}.json")

    os.makedirs(output_dir, exist_ok=True)

    tprint(f"\n{'='*60}")
    tprint(f"🚀  Starting: {category_name.upper()}")
    tprint(f"{'='*60}")

    # Step 1: URLs
    if os.path.exists(urls_file):
        with open(urls_file, "r") as f:
            all_urls = [l.strip() for l in f if l.strip()]
        if all_urls:
            tprint(f"  [{category_name}] 📋 Loaded {len(all_urls)} URLs")
        else:
            os.remove(urls_file)
            all_urls = collect_all_urls(category_url, category_name)
            all_urls = list(dict.fromkeys(all_urls))
            with open(urls_file, "w") as f:
                f.write("\n".join(all_urls))
    else:
        all_urls = collect_all_urls(category_url, category_name)
        all_urls = list(dict.fromkeys(all_urls))
        with open(urls_file, "w") as f:
            f.write("\n".join(all_urls))
        tprint(f"  [{category_name}] ✅ {len(all_urls)} URLs saved")

    if not all_urls:
        tprint(f"  [{category_name}] ⚠️  No URLs — skipping")
        return category_name, 0

    # Step 2: Progress
    all_products, done_set = [], set()
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                all_products = json.load(f)
            done_set = {p["url"] for p in all_products}
            tprint(f"  [{category_name}] ⚡ Resuming — {len(done_set)} done, {len(all_urls)-len(done_set)} left")
        except Exception:
            pass

    remaining = [u for u in all_urls if u not in done_set]

    # Step 3: Scrape
    for i, url in enumerate(remaining):
        num  = len(all_products) + 1
        slug = url.rstrip("/").split("/")[-1][:50]
        tprint(f"  [{category_name}] [{num}/{len(all_urls)}] {slug}")

        raw = scrape_detail(url, category_name)
        if not raw or raw.get("full_name") == "N/A":
            continue

        product = restructure(raw, category_name)
        all_products.append(product)
        tprint(f"  [{category_name}] ✅ {product['title'][:50]} | RM {product['price']}")

        if (i + 1) % 10 == 0:
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump(all_products, f, indent=2, ensure_ascii=False)
            tprint(f"  [{category_name}] 💾 Checkpoint: {len(all_products)} saved")

        time.sleep(random.uniform(0.5, 1.5))

    # Step 4: Save
    with open(final_json, "w", encoding="utf-8") as f:
        json.dump(all_products, f, indent=2, ensure_ascii=False)
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(all_products, f, indent=2, ensure_ascii=False)

    tprint(f"\n🎉  [{category_name}] DONE — {len(all_products)} products")
    return category_name, len(all_products)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print("🚀  M&G STATIONERY SCRAPER — mgstationeryonline.com.my")
    print(f"📦  {len(CATEGORIES)} categories | ⚡ {MAX_CATEGORY_THREADS} concurrent")
    print(f"✅  No ScraperAPI or ZenRows needed!")
    print("="*60)

    print("\nCategories:")
    for _, name in CATEGORIES:
        pf   = os.path.join(BASE_OUTPUT_DIR, name, "progress.json")
        done = 0
        if os.path.exists(pf):
            try:
                with open(pf) as f:
                    done = len(json.load(f))
            except Exception:
                pass
        status = f"⚡ resume ({done} done)" if done > 0 else "🆕 fresh"
        print(f"  {status:25} {name}")

    print()
    start_time = time.time()
    results    = {}

    with ThreadPoolExecutor(max_workers=MAX_CATEGORY_THREADS) as executor:
        futures = {executor.submit(scrape_category, url, name): name for url, name in CATEGORIES}
        for future in as_completed(futures):
            cat_name = futures[future]
            try:
                name, count = future.result()
                results[name] = count
            except Exception as e:
                tprint(f"\n❌  [{cat_name}] Failed: {e}")
                results[cat_name] = 0

    elapsed    = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    print("\n" + "="*60)
    print("📊  FINAL SUMMARY")
    print("="*60)
    total = 0
    for name, count in results.items():
        print(f"  ✅  {name:<40} {count} products")
        total += count
    print(f"\n  🎯  TOTAL: {total} products")
    print(f"  ⏱️   Time: {mins}m {secs}s")
    print(f"\n📁  All output in: {BASE_OUTPUT_DIR}/")


if __name__ == "__main__":
    main()