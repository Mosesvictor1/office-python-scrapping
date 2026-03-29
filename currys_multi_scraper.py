"""
currys_multi_scraper.py
────────────────────────
Scrapes MULTIPLE Currys categories concurrently.
- Each category runs in its own thread (up to 3 at once)
- Max 5 pages per category (~100 products each)
- Each category gets its own output folder
- JSON only, no CSV
- Resume support per category
- Full specs + key features
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

# ╔══════════════════════════════════════════════════════════╗
# ║                    CONFIGURATION                         ║
# ╚══════════════════════════════════════════════════════════╝
SCRAPER_API_KEY = "d90c9fdabee9a1f3f27141aa93ed07d7"  

# Categories to scrape — (URL, folder_name)

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
MAX_PAGES        = 4         
MAX_CATEGORY_THREADS = 3    
SCRAPER_API_URL  = "https://api.scraperapi.com/"
BASE_URL         = "https://www.currys.co.uk"
BASE_OUTPUT_DIR  = "output"

print_lock = Lock()


# ── THREAD-SAFE PRINT ─────────────────────────────────────────────────────────
def tprint(msg):
    with print_lock:
        print(msg)


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
                tprint(f"    ⏳  Rate limited — waiting 15s...")
                time.sleep(15)
            else:
                tprint(f"    ⚠️  HTTP {resp.status_code} (attempt {attempt})")
        except Exception as e:
            tprint(f"    ❌  Attempt {attempt} failed: {e}")
            time.sleep(3)
    return None


# ── GET PRODUCT URLS FROM A LISTING PAGE ──────────────────────────────────────
def get_product_urls(html):
    soup = BeautifulSoup(html, "lxml")
    urls = []
    seen = set()

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
def collect_all_urls(category_url, category_name):
    all_urls  = []
    seen_urls = set()

    tprint(f"  [{category_name}] 📄 Page 1")
    html = fetch(category_url, render_js=True)
    if not html:
        tprint(f"  [{category_name}] ❌ Failed to fetch page 1")
        return []

    urls = get_product_urls(html)
    new  = [u for u in urls if u not in seen_urls]
    seen_urls.update(new)
    all_urls.extend(new)
    tprint(f"  [{category_name}] ✅ Page 1: +{len(new)} | total: {len(all_urls)}")

    page_size         = len(new) if new else 20
    start             = page_size
    page_num          = 2
    consecutive_empty = 0

    while start < 5000 and page_num <= MAX_PAGES:
        page_url = f"{category_url}?start={start}&sz={page_size}"
        tprint(f"  [{category_name}] 📄 Page {page_num} (start={start})")

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
            tprint(f"  [{category_name}] 🏁 No new products — stopping")
            if consecutive_empty >= 2:
                break
        else:
            consecutive_empty = 0
            seen_urls.update(new)
            all_urls.extend(new)
            tprint(f"  [{category_name}] ✅ Page {page_num}: +{len(new)} | total: {len(all_urls)}")

        start    += page_size
        page_num += 1
        time.sleep(random.uniform(1, 2))

    return all_urls


# ── PARSE KEY FEATURES ────────────────────────────────────────────────────────
def parse_key_features(soup):
    features = []

    container = soup.find(class_="key-features-container")
    if container:
        for span in container.find_all(class_="item-title"):
            txt = span.get_text(strip=True)
            if txt and len(txt) > 2:
                features.append(txt)
    if features:
        return features

    container2 = soup.find(class_="pdp-item-features")
    if container2:
        for span in container2.find_all(class_="item-title"):
            txt = span.get_text(strip=True)
            if txt and len(txt) > 2:
                features.append(txt)
    if features:
        return features

    desc = soup.find(class_=re.compile(r'description|overview|summary', re.I))
    if desc:
        for li in desc.find_all("li"):
            txt = li.get_text(strip=True)
            if txt and len(txt) > 5:
                features.append(txt)

    return features[:8]


# ── PARSE SPECIFICATIONS ──────────────────────────────────────────────────────
def parse_specs(soup):
    seen_groups = {}

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

    return [{"title": t, "specs": s} for t, s in seen_groups.items()]


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


def restructure(raw, soup, category_name):
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
        "category":         category_name,
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
def scrape_detail(url, category_name):
    html = fetch(url, render_js=False)
    if not html:
        html = fetch(url, render_js=True)
    if not html:
        return {}, None

    soup = BeautifulSoup(html, "lxml")
    raw  = {"url": url}

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

    if not raw.get("full_name") or raw["full_name"] == "N/A":
        h1 = soup.find("h1")
        raw["full_name"] = h1.get_text(strip=True) if h1 else "N/A"

    if not raw.get("description"):
        meta = soup.find("meta", attrs={"name": "description"}) or \
               soup.find("meta", attrs={"property": "og:description"})
        if meta:
            raw["description"] = meta.get("content", "")[:1000]

    raw["specifications"] = parse_specs(soup)
    raw["key_features"]   = parse_key_features(soup)

    spec_count = sum(len(g.get("specs", [])) for g in raw["specifications"])
    feat_count = len(raw["key_features"])
    tprint(f"    [{category_name}] 📋 {spec_count} specs | ⚡ {feat_count} features")

    return raw, soup


# ── SCRAPE ONE FULL CATEGORY ──────────────────────────────────────────────────
def scrape_category(category_url, category_name):
    output_dir    = os.path.join(BASE_OUTPUT_DIR, category_name)
    progress_file = os.path.join(output_dir, "progress.json")
    urls_file     = os.path.join(output_dir, "urls.txt")
    final_json    = os.path.join(output_dir, f"{category_name}.json")

    os.makedirs(output_dir, exist_ok=True)

    tprint(f"\n{'='*60}")
    tprint(f"🚀  Starting: {category_name.upper()}")
    tprint(f"🔗  {category_url}")
    tprint(f"{'='*60}")

    # STEP 1: URLs
    if os.path.exists(urls_file):
        with open(urls_file, "r") as f:
            all_urls = [line.strip() for line in f if line.strip()]
        tprint(f"  [{category_name}] 📋 Loaded {len(all_urls)} saved URLs")
    else:
        all_urls = collect_all_urls(category_url, category_name)
        all_urls = list(dict.fromkeys(all_urls))
        with open(urls_file, "w") as f:
            f.write("\n".join(all_urls))
        tprint(f"  [{category_name}] ✅ {len(all_urls)} URLs collected")

    # STEP 2: Load progress
    all_products = []
    done_set     = set()
    if os.path.exists(progress_file):
        with open(progress_file, "r", encoding="utf-8") as f:
            all_products = json.load(f)
        done_set = {p["url"] for p in all_products}
        tprint(f"  [{category_name}] ⚡ Resuming — {len(done_set)} done, {len(all_urls)-len(done_set)} remaining")

    remaining = [u for u in all_urls if u not in done_set]

    # STEP 3: Scrape each product
    for i, url in enumerate(remaining):
        num  = len(all_products) + 1
        slug = url.rstrip("/").split("/")[-1][:45]
        tprint(f"  [{category_name}] [{num}/{len(all_urls)}] {slug}")

        raw, soup = scrape_detail(url, category_name)
        if not raw:
            continue

        product = restructure(raw, soup, category_name)
        all_products.append(product)
        tprint(f"  [{category_name}] ✅ {product.get('title','N/A')[:45]} | £{product.get('price','N/A')}")

        # Save checkpoint every 10 products
        if (i + 1) % 10 == 0:
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump(all_products, f, indent=2, ensure_ascii=False)
            tprint(f"  [{category_name}] 💾 Checkpoint: {len(all_products)} saved")

        time.sleep(random.uniform(1, 1.5))

    # STEP 4: Save final JSON
    with open(final_json, "w", encoding="utf-8") as f:
        json.dump(all_products, f, indent=2, ensure_ascii=False)
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(all_products, f, indent=2, ensure_ascii=False)

    tprint(f"\n🎉  [{category_name}] DONE — {len(all_products)} products")
    tprint(f"📁  {final_json}")

    return category_name, len(all_products)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print("🚀  CURRYS MULTI-CATEGORY SCRAPER")
    print(f"📦  {len(CATEGORIES)} categories | Max {MAX_PAGES} pages each")
    print(f"⚡  {MAX_CATEGORY_THREADS} categories running concurrently")
    print("="*60)

    # Show all categories
    print("\nCategories to scrape:")
    for url, name in CATEGORIES:
        output_dir    = os.path.join(BASE_OUTPUT_DIR, name)
        progress_file = os.path.join(output_dir, "progress.json")
        done = 0
        if os.path.exists(progress_file):
            try:
                with open(progress_file, "r") as f:
                    done = len(json.load(f))
            except Exception:
                pass
        status = f"⚡ resume ({done} done)" if done > 0 else "🆕 fresh"
        print(f"  {status:25} {name}")

    print()

    # Run categories concurrently
    start_time = time.time()
    results    = {}

    with ThreadPoolExecutor(max_workers=MAX_CATEGORY_THREADS) as executor:
        futures = {
            executor.submit(scrape_category, url, name): name
            for url, name in CATEGORIES
        }
        for future in as_completed(futures):
            category_name = futures[future]
            try:
                name, count = future.result()
                results[name] = count
            except Exception as e:
                tprint(f"\n❌  [{category_name}] Failed: {e}")
                results[category_name] = 0

    # Final summary
    elapsed = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    print("\n" + "="*60)
    print("📊  FINAL SUMMARY")
    print("="*60)
    total = 0
    for name, count in results.items():
        print(f"  ✅  {name:<35} {count} products")
        total += count
    print(f"\n  🎯  TOTAL: {total} products scraped")
    print(f"  ⏱️   Time:  {mins}m {secs}s")
    print(f"\n📁  All output saved in: {BASE_OUTPUT_DIR}/")
    for _, name in CATEGORIES:
        print(f"      {BASE_OUTPUT_DIR}/{name}/{name}.json")


if __name__ == "__main__":
    main()