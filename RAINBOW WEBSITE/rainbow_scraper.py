"""
rainbow_scraper.py
───────────────────
Scrapes ALL categories from rainbow-hongqiao.com concurrently.
- No ScraperAPI needed (no Cloudflare)
- WooCommerce pagination (?page=N)
- Same JSON format as Currys scraper
- Each category gets its own output folder
- Resume support per category
- 3 categories running at the same time
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
# ║                   CONFIGURATION                          ║
# ╚══════════════════════════════════════════════════════════╝

# All categories on rainbow-hongqiao.com
CATEGORIES = [
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/",            "executive-task-chairs"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/premium/",    "executive-premium"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/luxury/",     "executive-luxury"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/modern/",     "executive-modern"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/ergonomic/",  "executive-ergonomic"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/basic/",      "executive-basic"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/mesh-back/",  "executive-mesh-back"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/art/",        "executive-art"),
    ("https://rainbow-hongqiao.com/product-category/executive-task-chair/comfort/",    "executive-comfort"),
    ("https://rainbow-hongqiao.com/product-category/lounge-furniture/",                "lounge-furniture"),
    ("https://rainbow-hongqiao.com/product-category/lounge-furniture/bar-stool/",      "lounge-bar-stool"),
    ("https://rainbow-hongqiao.com/product-category/lounge-furniture/lounge-chair/",   "lounge-chair"),
    ("https://rainbow-hongqiao.com/product-category/lounge-furniture/sofa/",           "lounge-sofa"),
    ("https://rainbow-hongqiao.com/product-category/multipurpose-chair/",              "multipurpose-chair"),
    ("https://rainbow-hongqiao.com/product-category/guest-chair/",                     "guest-chair"),
    ("https://rainbow-hongqiao.com/product-category/guest-chair/premium/",             "guest-premium"),
    ("https://rainbow-hongqiao.com/product-category/guest-chair/mesh-back/",           "guest-mesh-back"),
    ("https://rainbow-hongqiao.com/product-category/guest-chair/comfort/",             "guest-comfort"),
    ("https://rainbow-hongqiao.com/product-category/guest-chair/modern/",              "guest-modern"),
    ("https://rainbow-hongqiao.com/product-category/guest-chair/luxury/",              "guest-luxury"),
    ("https://rainbow-hongqiao.com/product-category/stackable-chairs/",                "stackable-chairs"),
]

MAX_PAGES            = 8       # max pages per category
MAX_CATEGORY_THREADS = 3        # categories scraped concurrently
BASE_URL             = "https://rainbow-hongqiao.com"
BASE_OUTPUT_DIR      = "output"

# Request headers to mimic a real browser
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

print_lock = Lock()


# ── THREAD-SAFE PRINT ─────────────────────────────────────────────────────────
def tprint(msg):
    with print_lock:
        print(msg)


# ── FETCH (no ScraperAPI needed) ──────────────────────────────────────────────
def fetch(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.text
            else:
                tprint(f"    ⚠️  HTTP {resp.status_code} (attempt {attempt}) — {url[:60]}")
        except Exception as e:
            tprint(f"    ❌  Attempt {attempt} failed: {e}")
            time.sleep(2)
    return None


# ── GET PRODUCT URLS FROM A LISTING PAGE ──────────────────────────────────────
def get_product_urls(html):
    soup = BeautifulSoup(html, "lxml")
    urls = []
    seen = set()

    # WooCommerce product links — inside .products ul li h2 a
    # or .woocommerce-loop-product__link
    for a in soup.find_all("a", class_="woocommerce-loop-product__link"):
        href = a.get("href", "")
        if href and href not in seen:
            seen.add(href)
            urls.append(href)

    if urls:
        return urls

    # Fallback: any /product/ link
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/product/" in href and href not in seen:
            # Skip category links
            if "/product-category/" in href:
                continue
            seen.add(href)
            urls.append(href)

    return urls


# ── CHECK IF NEXT PAGE EXISTS ─────────────────────────────────────────────────
def has_next_page(html):
    soup = BeautifulSoup(html, "lxml")
    # WooCommerce uses .next for pagination
    return bool(
        soup.select_one("a.next") or
        soup.select_one(".woocommerce-pagination .next") or
        soup.find("a", class_="next")
    )


# ── COLLECT ALL URLS WITH PAGINATION ──────────────────────────────────────────
def collect_all_urls(category_url, category_name):
    all_urls  = []
    seen_urls = set()

    for page_num in range(1, MAX_PAGES + 1):
        # WooCommerce pagination: /page/2/, /page/3/ etc.
        if page_num == 1:
            page_url = category_url
        else:
            # Ensure trailing slash before adding page
            base = category_url.rstrip("/")
            page_url = f"{base}/page/{page_num}/"

        tprint(f"  [{category_name}] 📄 Page {page_num}: {page_url}")

        html = fetch(page_url)
        if not html:
            tprint(f"  [{category_name}] ❌ Failed — stopping pagination")
            break

        # Check for 404 / no products
        soup = BeautifulSoup(html, "lxml")
        if soup.find(class_=re.compile(r'error-404|no-products|woocommerce-info')):
            tprint(f"  [{category_name}] 🏁 No more pages")
            break

        urls = get_product_urls(html)
        new  = [u for u in urls if u not in seen_urls]

        if not new:
            tprint(f"  [{category_name}] 🏁 No new products — stopping")
            break

        seen_urls.update(new)
        all_urls.extend(new)
        tprint(f"  [{category_name}] ✅ Page {page_num}: +{len(new)} | total: {len(all_urls)}")

        # Stop if no next page link
        if not has_next_page(html):
            tprint(f"  [{category_name}] 🏁 Last page reached")
            break

        time.sleep(random.uniform(0.5, 1.5))

    return all_urls


# ── PARSE KEY FEATURES ────────────────────────────────────────────────────────
def parse_key_features(soup):
    features = []

    # Rainbow site uses numbered list with bold titles inside product description
    # e.g. "1. High Back Design: ..."
    entry_content = soup.find(class_=re.compile(r'entry-content|woocommerce-product-details__short-description|short-description', re.I))
    if entry_content:
        for li in entry_content.find_all("li"):
            # Get bold text (the feature title)
            bold = li.find("strong") or li.find("b")
            if bold:
                txt = bold.get_text(strip=True).rstrip(":")
            else:
                txt = li.get_text(strip=True)
            if txt and len(txt) > 2:
                # Clean up numbering like "1." at start
                txt = re.sub(r'^\d+\.\s*', '', txt)
                features.append(txt)

    if features:
        return features[:8]

    # Fallback: h2 "Key Features" section
    for heading in soup.find_all(["h2", "h3"]):
        if "key feature" in heading.get_text().lower():
            sibling = heading.find_next_sibling()
            while sibling:
                if sibling.name in ("h2", "h3"):
                    break
                for li in sibling.find_all("li"):
                    bold = li.find("strong") or li.find("b")
                    txt = bold.get_text(strip=True) if bold else li.get_text(strip=True)
                    txt = re.sub(r'^\d+\.\s*', '', txt.rstrip(":"))
                    if txt and len(txt) > 2:
                        features.append(txt)
                sibling = sibling.find_next_sibling()
            break

    return features[:8]


# ── PARSE SPECIFICATIONS ──────────────────────────────────────────────────────
def parse_specs(soup):
    """
    Rainbow site doesn't have a formal spec table like Currys.
    We extract specs from the product description content — 
    bold labels followed by description text, structured as groups.
    """
    seen_groups = {}

    # Try WooCommerce additional information tab (if it has attributes)
    attr_table = soup.find("table", class_=re.compile(r'woocommerce-product-attributes|shop_attributes', re.I))
    if attr_table:
        group_title = "Specifications"
        seen_groups[group_title] = []
        for row in attr_table.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if th and td:
                key   = th.get_text(strip=True)
                value = td.get_text(strip=True)
                if key:
                    seen_groups[group_title].append({"key": key, "value": value})

    # Parse description for structured content
    desc = soup.find("div", class_=re.compile(r'woocommerce-Tabs-panel--description|entry-content', re.I))
    if desc:
        # Look for patterns like "Key Features:" sections with ordered lists
        current_group = None

        for el in desc.children:
            if not hasattr(el, 'get_text'):
                continue

            tag = getattr(el, 'name', None)

            # Headings become group titles
            if tag in ("h2", "h3", "h4"):
                txt = el.get_text(strip=True)
                if txt and len(txt) < 60:
                    current_group = txt
                    if current_group not in seen_groups:
                        seen_groups[current_group] = []

            # Ordered/unordered lists become specs under current group
            elif tag in ("ol", "ul"):
                if not current_group:
                    current_group = "Features"
                    if current_group not in seen_groups:
                        seen_groups[current_group] = []

                for li in el.find_all("li", recursive=False):
                    bold = li.find(["strong", "b"])
                    if bold:
                        key = bold.get_text(strip=True).rstrip(":")
                        # Remove the bold tag to get remaining value text
                        bold.extract()
                        # Also remove nested list items
                        for sub in li.find_all(["ul", "ol"]):
                            sub.extract()
                        value = li.get_text(strip=True).lstrip(":").strip()
                        if not value:
                            # Try nested list as value
                            value = ", ".join(
                                sub_li.get_text(strip=True)
                                for sub_li in li.find_all("li")
                            )
                    else:
                        key   = re.sub(r'^\d+\.\s*', '', li.get_text(strip=True))
                        value = ""

                    key = re.sub(r'^\d+\.\s*', '', key)
                    if key and len(key) > 1:
                        seen_groups[current_group].append({"key": key, "value": value})

            # Paragraphs with bold key: value pattern
            elif tag == "p":
                for strong in el.find_all(["strong", "b"]):
                    key = strong.get_text(strip=True).rstrip(":")
                    strong.extract()
                    value = el.get_text(strip=True).lstrip(":").strip()
                    if key and len(key) > 1 and len(key) < 60:
                        grp = current_group or "Details"
                        if grp not in seen_groups:
                            seen_groups[grp] = []
                        seen_groups[grp].append({"key": key, "value": value})
                    break  # only first bold per paragraph

    # Remove empty groups
    return [
        {"title": title, "specs": specs}
        for title, specs in seen_groups.items()
        if specs
    ]


# ── HELPERS ───────────────────────────────────────────────────────────────────
def extract_colour(soup, specs):
    # Check specs first
    for group in specs:
        for spec in group.get("specs", []):
            if spec["key"].lower() in ("colour", "color", "colors", "colours"):
                return [spec["value"]]

    # Check product variations (WooCommerce swatches)
    colours = []
    for el in soup.find_all(class_=re.compile(r'swatch|color-option|colour', re.I)):
        txt = el.get("title") or el.get("data-value") or el.get_text(strip=True)
        if txt and len(txt) < 30:
            colours.append(txt)

    return list(set(colours))[:5] if colours else []


def extract_warranty(specs):
    for group in specs:
        for spec in group.get("specs", []):
            if "guarantee" in spec["key"].lower() or "warranty" in spec["key"].lower():
                return spec["value"]
    return "N/A"


def extract_sku(soup):
    # WooCommerce SKU in .sku element
    sku_el = soup.find(class_="sku")
    if sku_el:
        return sku_el.get_text(strip=True)
    # Try meta
    meta = soup.find("meta", attrs={"property": "product:retailer_item_id"})
    if meta:
        return meta.get("content", "N/A")
    return "N/A"


def extract_price(soup):
    # WooCommerce price
    price_el = soup.select_one(".woocommerce-Price-amount bdi") or \
               soup.select_one(".price .amount") or \
               soup.select_one(".price")
    if price_el:
        txt = price_el.get_text(strip=True)
        # Remove currency symbol
        txt = re.sub(r'[^\d.,]', '', txt).strip()
        return txt
    return "N/A"


def extract_images(soup):
    images = []
    seen   = set()

    # WooCommerce product gallery
    for el in soup.select(".woocommerce-product-gallery__image a, .woocommerce-product-gallery img"):
        src = el.get("href") or el.get("data-large_image") or el.get("src", "")
        if src and src not in seen and not src.endswith(".gif"):
            # Get full size (remove -300x300, -600x600 etc.)
            full = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', src)
            seen.add(full)
            images.append(full)

    return images[:6]


def extract_categories(soup):
    cats = []
    for a in soup.select(".posted_in a, .product_meta .posted_in a"):
        txt = a.get_text(strip=True)
        if txt:
            cats.append(txt)
    return cats


def extract_tags(soup):
    tags = []
    for a in soup.select(".tagged_as a, .product_meta .tagged_as a"):
        txt = a.get_text(strip=True)
        if txt:
            tags.append(txt)
    return tags


def build_subtitle(full_name):
    words = full_name.split()
    return " ".join(words[:4]) if len(words) > 4 else full_name


def restructure(raw, soup, category_name):
    specs        = raw.get("specifications", [])
    colour       = extract_colour(soup, specs)
    warranty     = extract_warranty(specs)
    key_features = raw.get("key_features", [])
    full_name    = raw.get("full_name", "N/A")

    rating_raw = raw.get("rating", "N/A")
    try:
        rating = f"{float(rating_raw)}/5"
    except Exception:
        rating = rating_raw

    return {
        "title":            full_name,
        "subtitle":         build_subtitle(full_name),
        "brand":            "Rainbow Hong Qiao",
        "sku":              raw.get("sku", "N/A"),
        "url":              raw.get("url", ""),
        "category":         category_name,
        "tags":             raw.get("tags", []),
        "overview":         raw.get("description", "N/A"),
        "price":            raw.get("price", "N/A"),
        "currency":         "USD",
        "color":            colour,
        "rating":           rating,
        "review_count":     raw.get("review_count", "0"),
        "availability":     raw.get("availability", True),
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
    html = fetch(url)
    if not html:
        return {}, None

    soup = BeautifulSoup(html, "lxml")
    raw  = {"url": url}

    # Title
    h1 = soup.find("h1", class_=re.compile(r'product_title|entry-title', re.I)) or soup.find("h1")
    raw["full_name"] = h1.get_text(strip=True) if h1 else "N/A"

    # SKU
    raw["sku"] = extract_sku(soup)

    # Price
    raw["price"] = extract_price(soup)

    # Description — short description first, then full
    short_desc = soup.find(class_=re.compile(r'woocommerce-product-details__short-description|short-description', re.I))
    if short_desc:
        raw["description"] = short_desc.get_text(separator=" ", strip=True)[:1000]
    else:
        desc_tab = soup.find("div", class_=re.compile(r'woocommerce-Tabs-panel--description|entry-content', re.I))
        raw["description"] = desc_tab.get_text(separator=" ", strip=True)[:1000] if desc_tab else "N/A"

    # Rating
    rating_el = soup.find(class_=re.compile(r'rating|star-rating', re.I))
    if rating_el:
        # WooCommerce rating in <strong class="rating">X.X</strong> or aria-label
        aria = rating_el.get("aria-label", "")
        m    = re.search(r'([\d.]+)\s*out\s*of', aria)
        raw["rating"] = m.group(1) if m else "N/A"
    else:
        raw["rating"] = "N/A"

    # Review count
    review_el = soup.find(class_=re.compile(r'review_count|woocommerce-review-link', re.I))
    if review_el:
        m = re.search(r'\d+', review_el.get_text())
        raw["review_count"] = m.group() if m else "0"
    else:
        raw["review_count"] = "0"

    # Availability
    avail_el = soup.find(class_=re.compile(r'in-stock|out-of-stock|availability', re.I))
    raw["availability"] = "out" not in (avail_el.get_text().lower() if avail_el else "")

    # Images
    raw["images"] = extract_images(soup)

    # Tags
    raw["tags"] = extract_tags(soup)

    # Specifications + key features
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

    # STEP 1: Collect URLs
    if os.path.exists(urls_file):
        with open(urls_file, "r") as f:
            all_urls = [line.strip() for line in f if line.strip()]
        tprint(f"  [{category_name}] 📋 Loaded {len(all_urls)} saved URLs")
    else:
        all_urls = collect_all_urls(category_url, category_name)
        all_urls = list(dict.fromkeys(all_urls))  # deduplicate
        with open(urls_file, "w") as f:
            f.write("\n".join(all_urls))
        tprint(f"  [{category_name}] ✅ {len(all_urls)} URLs collected & saved")

    if not all_urls:
        tprint(f"  [{category_name}] ⚠️  No URLs found — skipping")
        return category_name, 0

    # STEP 2: Load progress
    all_products = []
    done_set     = set()
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                all_products = json.load(f)
            done_set = {p["url"] for p in all_products}
            tprint(f"  [{category_name}] ⚡ Resuming — {len(done_set)} done, {len(all_urls)-len(done_set)} remaining")
        except Exception:
            pass

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
        tprint(f"  [{category_name}] ✅ {product.get('title','N/A')[:45]}")

        # Checkpoint every 10
        if (i + 1) % 10 == 0:
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump(all_products, f, indent=2, ensure_ascii=False)
            tprint(f"  [{category_name}] 💾 Checkpoint: {len(all_products)} saved")

        time.sleep(random.uniform(0.5, 1.2))

    # STEP 4: Final save
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
    print("🚀  RAINBOW HONG QIAO — MULTI-CATEGORY SCRAPER")
    print(f"📦  {len(CATEGORIES)} categories | Max {MAX_PAGES} pages each")
    print(f"⚡  {MAX_CATEGORY_THREADS} categories running concurrently")
    print(f"🌐  No ScraperAPI needed — direct requests")
    print("="*60)

    # Show all categories with resume status
    print("\nCategories to scrape:")
    for url, name in CATEGORIES:
        progress_file = os.path.join(BASE_OUTPUT_DIR, name, "progress.json")
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

    start_time = time.time()
    results    = {}

    with ThreadPoolExecutor(max_workers=MAX_CATEGORY_THREADS) as executor:
        futures = {
            executor.submit(scrape_category, url, name): name
            for url, name in CATEGORIES
        }
        for future in as_completed(futures):
            cat_name = futures[future]
            try:
                name, count = future.result()
                results[name] = count
            except Exception as e:
                tprint(f"\n❌  [{cat_name}] Failed: {e}")
                results[cat_name] = 0

    # Summary
    elapsed    = int(time.time() - start_time)
    mins, secs = divmod(elapsed, 60)

    print("\n" + "="*60)
    print("📊  FINAL SUMMARY")
    print("="*60)
    total = 0
    for name, count in results.items():
        print(f"  ✅  {name:<40} {count} products")
        total += count
    print(f"\n  🎯  TOTAL: {total} products scraped")
    print(f"  ⏱️   Time:  {mins}m {secs}s")
    print(f"\n📁  All output saved in: {BASE_OUTPUT_DIR}/")
    for _, name in CATEGORIES:
        print(f"      {BASE_OUTPUT_DIR}/{name}/{name}.json")


if __name__ == "__main__":
    main()