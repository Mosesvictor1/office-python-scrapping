"""
deli_scraper.py
───────────────────────────────────────────────────────────
Scrapes www.deliworld.com — direct requests, NO ScraperAPI needed.

Changes:
  - Output organized into parent folders:
      output/deli/school-stationery/{slug}/{slug}.json
      output/deli/writing-instrument/{slug}/{slug}.json
      output/deli/office-supplies/{slug}/{slug}.json
      output/deli/office-furniture/{slug}/{slug}.json
      output/deli/stick-up/{slug}/{slug}.json
  - Removed: Dmast, Agnite, Nusign
  - Auto-migrates existing output/deli/{slug}/ folders into correct parent
  - Resumes from where it stopped (smart per-category resume)
  - Max 50 products per category
"""

import requests
import json
import os
import re
import shutil
import time
import random
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── Config ───────────────────────────────────────────────────────────────────
BASE_URL            = "https://www.deliworld.com"
OUTPUT_BASE         = "output/deli"
DELAY               = (1, 3)
MAX_WORKERS         = 3
PROGRESS_SAVE_EVERY = 5
RETRIES             = 3
TIMEOUT             = 30
MAX_PRODUCTS        = 50

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.deliworld.com/",
}

# ── Categories ────────────────────────────────────────────────────────────────
# Format: (parent_folder, display_name, category_url_path, category_slug)
CATEGORIES = [
    # ── School Stationery ────────────────────────────────────────────────────
    ("school-stationery", "Bag",                       "/product/bags-for-school/",            "bags-for-school"),
    ("school-stationery", "Coloring Product",          "/product/art-materials/",              "art-materials"),
    ("school-stationery", "Kids Education",            "/product/educational-tools-for-kids/", "kids-education"),
    ("school-stationery", "Paper Product",             "/product/deli-notebook-product/",      "paper-product"),
    ("school-stationery", "School Supplies",           "/product/school-supplies/",            "school-supplies"),
    # ── Writing Instrument ───────────────────────────────────────────────────
    ("writing-instrument", "Gel Pen",                   "/product/gel-pen/",                    "gel-pen"),
    ("writing-instrument", "Exam-Oriented Pen",         "/product/exam-writing-pen/",           "exam-oriented-pen"),
    ("writing-instrument", "Gel Pen Refill",            "/product/gel-pen-refill/",             "gel-pen-refill"),
    ("writing-instrument", "Fountain Pen",              "/product/fountain-pen/",               "fountain-pen"),
    ("writing-instrument", "Desk Pen Stand",            "/product/reception-desk-pen/",         "desk-pen-stand"),
    ("writing-instrument", "Ballpoint Pen",             "/product/ballpoint-pen/",              "ballpoint-pen"),
    ("writing-instrument", "Roller Pen",                "/product/roller-pen/",                 "roller-pen"),
    ("writing-instrument", "Permanent Marker",          "/product/permanent-marker/",           "permanent-marker"),
    ("writing-instrument", "Dry Erase Marker",          "/product/dry-erase-marker/",           "dry-erase-marker"),
    ("writing-instrument", "Highlighter",               "/product/fluorescent-marker-pen/",     "highlighter"),
    ("writing-instrument", "Paint Marker",              "/product/paint-marker/",               "paint-marker"),
    ("writing-instrument", "Mechanical Pencil & Leads", "/product/mechanical-pencil-and-lead/", "mechanical-pencil-leads"),
    ("writing-instrument", "Ink",                       "/product/ink/",                        "ink"),
    ("writing-instrument", "Whiteboard Marker",         "/product/whiteboard-marker/",          "whiteboard-marker"),
    ("writing-instrument", "Pigment Liner",             "/product/pigment-liner/",              "pigment-liner"),
    ("writing-instrument", "Brush Pen",                 "/product/brush-pen/",                  "brush-pen"),
    # ── Office Supplies ──────────────────────────────────────────────────────
    ("office-supplies", "Stapler & Punch",           "/product/stapler-punchs/",            "stapler-punch"),
    ("office-supplies", "Office Life",               "/product/office-life/",               "office-life"),
    ("office-supplies", "Calculator",                "/product/calculator/",                "calculator"),
    ("office-supplies", "File & Folder",             "/product/deli-file-folder/",          "file-folder"),
    ("office-supplies", "Office Paper",              "/product/office-paper/",              "office-paper"),
    ("office-supplies", "Desktop Supplies",          "/product/desktop-supplies/",          "desktop-supplies"),
    ("office-supplies", "Organization",              "/product/organization/",              "organization"),
    # ── Office Furniture ─────────────────────────────────────────────────────
    ("office-furniture", "Mesh Chair",        "/product/mesh-chair/",      "mesh-chair"),
    ("office-furniture", "Leather Chair",     "/product/leather-chair/",   "leather-chair"),
    ("office-furniture", "Kids Desk & Chair", "/product/kids-desk-chair/", "kids-desk-chair"),
    # ── Stick Up ─────────────────────────────────────────────────────────────
    ("stick-up", "Screen & Board Cleaner", "/product/screen-board-cleaner/", "screen-board-cleaner"),
    ("stick-up", "Sticky Notes",           "/product/sticky-notes/",         "sticky-notes"),
    ("stick-up", "Tape Dispenser",         "/product/tape-dispenser/",       "tape-dispenser"),
    ("stick-up", "Carton Sealer",          "/product/carton-sealer/",        "carton-sealer"),
    ("stick-up", "Adhesive Roller",        "/product/adhesive-roller/",      "adhesive-roller"),
    ("stick-up", "Tape",                   "/product/tape/",                 "tape"),
    ("stick-up", "Glue Stick",             "/product/glue-stick/",           "glue-stick"),
    ("stick-up", "Liquid Glue",            "/product/liquid-glue/",          "liquid-glue"),
    ("stick-up", "Super Glue",             "/product/super-glue/",           "super-glue"),
    ("stick-up", "White Glue",             "/product/white-glue/",           "white-glue"),
]

# Map every slug to its parent folder — used during migration
SLUG_TO_PARENT = {slug: parent for parent, _, _, slug in CATEGORIES}

print_lock = Lock()

def log(msg):
    with print_lock:
        print(msg)

# ── Path helpers ──────────────────────────────────────────────────────────────
def category_dir(parent, slug):
    return os.path.join(OUTPUT_BASE, parent, slug)

def progress_path(parent, slug):
    return os.path.join(category_dir(parent, slug), "progress.json")

def output_path(parent, slug):
    return os.path.join(category_dir(parent, slug), f"{slug}.json")

def load_progress(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"scraped_urls": [], "products": []}

def save_progress(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Migration: flat → parent structure ───────────────────────────────────────
def migrate_existing_folders():
    """
    Moves  output/deli/{slug}/
       →   output/deli/{parent}/{slug}/

    Also renames products.json → {slug}.json if needed.
    Skips slugs not in CATEGORIES (dmast, agnite, nusign — leave untouched).
    """
    if not os.path.isdir(OUTPUT_BASE):
        return

    migrated = []

    for entry in sorted(os.listdir(OUTPUT_BASE)):
        old_path = os.path.join(OUTPUT_BASE, entry)

        # Only flat directories whose name matches a known slug
        if not os.path.isdir(old_path):
            continue
        if entry not in SLUG_TO_PARENT:
            continue  # not in our category list — leave alone

        parent   = SLUG_TO_PARENT[entry]
        new_dir  = os.path.join(OUTPUT_BASE, parent, entry)

        # Already in the correct place
        if os.path.abspath(old_path) == os.path.abspath(new_dir):
            # Still check for products.json rename
            old_pj = os.path.join(new_dir, "products.json")
            new_pj = os.path.join(new_dir, f"{entry}.json")
            if os.path.exists(old_pj) and not os.path.exists(new_pj):
                os.rename(old_pj, new_pj)
                migrated.append(f"  📄 {parent}/{entry}/products.json → {entry}.json")
            continue

        os.makedirs(os.path.join(OUTPUT_BASE, parent), exist_ok=True)

        if os.path.isdir(new_dir):
            # Destination exists — merge files (don't overwrite)
            for fname in os.listdir(old_path):
                src  = os.path.join(old_path, fname)
                dest = os.path.join(new_dir, fname)
                if not os.path.exists(dest):
                    shutil.copy2(src, dest)
            shutil.rmtree(old_path)
            migrated.append(f"  🔀 {entry}/ merged → {parent}/{entry}/")
        else:
            shutil.move(old_path, new_dir)
            migrated.append(f"  📦 {entry}/ → {parent}/{entry}/")

        # Rename products.json → {slug}.json
        old_pj = os.path.join(new_dir, "products.json")
        new_pj = os.path.join(new_dir, f"{entry}.json")
        if os.path.exists(old_pj) and not os.path.exists(new_pj):
            os.rename(old_pj, new_pj)
            migrated.append(f"  📄 {parent}/{entry}/products.json → {entry}.json")

    if migrated:
        print("🗂️  Migrating existing folders into parent structure:")
        for m in migrated:
            print(m)
        print()
    else:
        print("🗂️  Folders already organised — no migration needed.\n")

# ── HTTP ──────────────────────────────────────────────────────────────────────
def fetch(url, retries=RETRIES):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 429:
                wait = 20 + attempt * 10
                log(f"  ⏳ Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                log(f"  ⚠️  HTTP {resp.status_code} → {url}")
                time.sleep(3)
        except Exception as e:
            log(f"  ❌ Fetch error ({attempt+1}/{retries}): {e}")
            time.sleep(5 * (attempt + 1))
    return None

# ── URL collection ────────────────────────────────────────────────────────────
def get_product_urls(cat_url_path, limit=MAX_PRODUCTS):
    urls = []
    seen = set()
    page = 1

    while len(urls) < limit:
        full_url = (
            BASE_URL + cat_url_path
            if page == 1
            else BASE_URL + cat_url_path + f"?page={page}"
        )
        html = fetch(full_url)
        if not html:
            break

        soup = BeautifulSoup(html, "lxml")

        # Only the active product grid — no sidebar/nav fallback
        grid = soup.select_one(".sep-two-pro-list .pp-lists.active .p-boxs")
        if not grid:
            grid = soup.select_one(".sep-two-pro-list .pp-lists .p-boxs")

        found_this_page = 0
        if grid:
            for card in grid.find_all("div", class_="p-lists", recursive=False):
                if len(urls) >= limit:
                    break
                thumb = card.find("a", class_="thumbs")
                if not thumb:
                    continue
                href = thumb.get("href", "")
                if not href or not href.endswith(".html"):
                    continue
                full = href if href.startswith("http") else BASE_URL + href
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
                    found_this_page += 1

        if len(urls) >= limit:
            break

        pag    = soup.select_one(".sep-two-pro-list .sep-pagination")
        next_a = pag.find("a", class_="next-page") if pag else None
        if not next_a or "disable" in next_a.get("class", []) or found_this_page == 0:
            break

        page += 1
        time.sleep(random.uniform(*DELAY))

    return urls[:limit]

# ── Product detail ────────────────────────────────────────────────────────────
def scrape_product(url, category_name, category_slug):
    html = fetch(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")

    title_el = soup.select_one(".sep-in-detail-boxs h1.titles")
    title    = title_el.get_text(strip=True) if title_el else ""
    if not title:
        title_el = soup.select_one("h1")
        title    = title_el.get_text(strip=True) if title_el else ""

    sku = ""
    m   = re.search(r'SAP[:\s]+(\d+)', html)
    if m:
        sku = m.group(1)

    spec_text  = ""
    color_text = ""
    for item in soup.select(".sep-in-detail-list1 .p-lists"):
        text = item.get_text(" ", strip=True)
        if "Specification:" in text:
            spec_text  = re.sub(r"Specification:\s*", "", text).strip()
        elif "Color:" in text:
            color_text = re.sub(r"Color:\s*", "", text).strip()
        elif "SAP" not in text and text and not color_text:
            color_text = text

    images    = []
    seen_imgs = set()
    swiper    = soup.select_one(".sep-in-dt-list .swiper-wrapper")
    if swiper:
        for img in swiper.find_all("img", src=True):
            src = img["src"]
            if "/uploads/image/" in src and src not in seen_imgs:
                seen_imgs.add(src)
                images.append(BASE_URL + src if not src.startswith("http") else src)
    if not images:
        mi = soup.select_one(".sep-in-detail-boxs .imgs .thumbs img")
        if mi and mi.get("src"):
            images.append(BASE_URL + mi["src"] if not mi["src"].startswith("http") else mi["src"])
    if not images:
        og = soup.find("meta", {"property": "og:image"})
        if og and og.get("content"):
            images.append(og["content"])

    rating = review_count = "N/A"
    review_count = "0"
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            if data.get("@type") == "Product":
                agg = data.get("aggregateRating", {})
                if agg:
                    rating       = str(agg.get("ratingValue", "N/A"))
                    review_count = str(agg.get("reviewCount", "0"))
                break
        except Exception:
            pass

    overview  = ""
    meta_desc = soup.find("meta", {"name": "description"})
    if meta_desc:
        overview = meta_desc.get("content", "").strip()

    subtitle   = ""
    breadcrumb = soup.select(".breadcrumb li")
    if len(breadcrumb) >= 2:
        subtitle = breadcrumb[-2].get_text(strip=True)

    key_features = [
        li.get_text(strip=True)
        for li in soup.select(".sep-in-detail-list1 .ul-boxs li")
        if li.get_text(strip=True)
    ]

    spec_entries = []
    if sku:        spec_entries.append({"key": "SAP",           "value": sku})
    if spec_text:  spec_entries.append({"key": "Specification", "value": spec_text})
    if color_text: spec_entries.append({"key": "Color",         "value": color_text})
    specs  = [{"title": "Product Details", "specs": spec_entries}] if spec_entries else []
    colors = [c.strip() for c in color_text.split(",")] if "," in color_text else ([color_text] if color_text else [])

    return {
        "title":            title,
        "subtitle":         subtitle,
        "brand":            "Deli",
        "sku":              sku,
        "url":              url,
        "category":         category_name,
        "overview":         overview,
        "price":            "N/A",
        "currency":         "N/A",
        "color":            colors,
        "rating":           f"{rating}/5" if rating != "N/A" else "N/A",
        "review_count":     review_count,
        "availability":     True,
        "key_features":     key_features,
        "specifications":   specs,
        "images":           images,
        "warranty":         "N/A",
        "is_active":        True,
        "featured_product": False,
        "new_arrival":      False,
        "best_seller":      False,
        "is_deleted":       False,
    }

# ── Scrape one category ───────────────────────────────────────────────────────
def scrape_category(parent, cat_name, cat_url_path, cat_slug):
    out_dir = category_dir(parent, cat_slug)
    os.makedirs(out_dir, exist_ok=True)

    pf = progress_path(parent, cat_slug)
    of = output_path(parent, cat_slug)

    log(f"\n{'='*60}")
    log(f"📁 [{parent}] {cat_name}  →  {BASE_URL}{cat_url_path}")

    progress     = load_progress(pf)
    scraped_urls = set(progress["scraped_urls"])
    products     = progress["products"]
    already_done = len(products)

    if already_done >= MAX_PRODUCTS:
        log(f"  ✅ Already complete ({already_done}/{MAX_PRODUCTS}) — skipping")
        if not os.path.exists(of):
            with open(of, "w", encoding="utf-8") as f:
                json.dump(products[:MAX_PRODUCTS], f, ensure_ascii=False, indent=2)
        return products

    remaining = MAX_PRODUCTS - already_done
    log(f"  ⚡ {already_done} done — need {remaining} more (target {MAX_PRODUCTS})")
    log(f"  🔍 Collecting up to {MAX_PRODUCTS} product URLs...")

    all_urls = get_product_urls(cat_url_path, limit=MAX_PRODUCTS)
    log(f"  📦 {len(all_urls)} URLs found | {len(scraped_urls)} already scraped")

    pending = [u for u in all_urls if u not in scraped_urls][:remaining]

    if not pending:
        log("  ✅ Nothing new to scrape!")
        if not os.path.exists(of):
            with open(of, "w", encoding="utf-8") as f:
                json.dump(products, f, ensure_ascii=False, indent=2)
        return products

    log(f"  🚀 Scraping {len(pending)} products...")
    lock  = Lock()
    count = [0]

    def scrape_one(url):
        time.sleep(random.uniform(*DELAY))
        prod = scrape_product(url, cat_name, cat_slug)
        with lock:
            if prod:
                products.append(prod)
                scraped_urls.add(url)
                count[0] += 1
                log(f"  ✅ [{already_done + count[0]}/{MAX_PRODUCTS}] {prod['title'][:65]}")
                if count[0] % PROGRESS_SAVE_EVERY == 0:
                    save_progress(pf, {
                        "scraped_urls": list(scraped_urls),
                        "products": products
                    })
            else:
                log(f"  ❌ Failed: {url}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for fut in as_completed([ex.submit(scrape_one, u) for u in pending]):
            try:
                fut.result()
            except Exception as e:
                log(f"  ⚠️ Thread error: {e}")

    final = products[:MAX_PRODUCTS]
    save_progress(pf, {"scraped_urls": list(scraped_urls), "products": final})
    with open(of, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    log(f"  💾 {len(final)} products → {of}")
    return final

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("🚀 Deli World Scraper — Organised by parent folder")
    print(f"   {len(CATEGORIES)} categories | max {MAX_PRODUCTS} each\n")
    os.makedirs(OUTPUT_BASE, exist_ok=True)

    # Step 1 — migrate flat folders into parent structure
    migrate_existing_folders()

    # Step 2 — status overview
    print("📊 Status:")
    current_parent = None
    for parent, cat_name, _, cat_slug in CATEGORIES:
        if parent != current_parent:
            print(f"\n  📂 {parent}/")
            current_parent = parent
        pf   = progress_path(parent, cat_slug)
        done = 0
        if os.path.exists(pf):
            try:
                done = len(load_progress(pf).get("products", []))
            except Exception:
                pass
        status = (
            f"✅ done    ({done:>2})" if done >= MAX_PRODUCTS else
            f"⚡ partial ({done:>2})" if done > 0             else
            f"🆕 fresh   ( 0)"
        )
        print(f"     {status}   {cat_name}")

    print()

    # Step 3 — scrape
    total = 0
    for parent, cat_name, cat_url_path, cat_slug in CATEGORIES:
        try:
            prods = scrape_category(parent, cat_name, cat_url_path, cat_slug)
            total += len(prods)
        except KeyboardInterrupt:
            print("\n⛔ Stopped. Progress saved.")
            break
        except Exception as e:
            log(f"  ❌ '{cat_name}' failed: {e}")

    # Step 4 — final tree
    print(f"\n{'='*60}")
    print(f"✅ DONE — {total} total products\n")
    print("📁 Final structure:")
    current_parent = None
    for parent, cat_name, _, cat_slug in CATEGORIES:
        if parent != current_parent:
            print(f"\n  output/deli/{parent}/")
            current_parent = parent
        of   = output_path(parent, cat_slug)
        size = 0
        if os.path.exists(of):
            try:
                with open(of) as f:
                    size = len(json.load(f))
            except Exception:
                pass
        print(f"    └─ {cat_slug}/{cat_slug}.json   ({size} products)")


if __name__ == "__main__":
    main()