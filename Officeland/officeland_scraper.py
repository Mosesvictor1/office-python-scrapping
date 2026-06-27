"""
officelandng.com Scraper  ─  v4
================================
WooCommerce / WordPress (Woodmart theme)

v4 changes:
  • Images uploaded to Cloudinary during scrape (parallel per product)
  • Scrape + upload pipelined: next PDP scrapes while previous product uploads
  • Per-product image batches keyed by product URL — no cross-product mixing
  • Shared Cloudinary URL cache avoids re-uploading duplicate source images

v3 changes:
  • Max 50 products per sub-category (MAX_PRODUCTS_PER_CATEGORY)
  • Every sub-category filter list always starts with the parent filter
      – Office Chairs sub-cats  → always begin with "office_chairs"
      – Desks / Tables sub-cats → always begin with "desks"
      – Home Office sub-cats    → always begin with "home_office"
  • Images hard-capped at 5 (MAX_IMAGES)
  • Output organised into parent-category sub-folders:
      output/officeland/Office Chairs/executive_chairs.json
      output/officeland/Office Tables/meeting_tables.json  …

Usage:
  pip install playwright requests
  playwright install chromium
  python3 officeland_scraper.py
"""

import json
import os
import re
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── Hard limits ───────────────────────────────────────────────────────────────
MAX_PRODUCTS_PER_CATEGORY = 50   # scraped PDPs per JSON file
MAX_IMAGES                = 5    # images per product
PER_PAGE                  = 36   # listing page size

BASE_URL   = "https://officelandng.com"
OUTPUT_DIR = "output/officeland"

# ── Cloudinary ────────────────────────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME    = "decbrtduj"
CLOUDINARY_UPLOAD_PRESET = "unsigned_preset"
CLOUDINARY_UPLOAD_URL    = (
    f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
)
UPLOAD_TO_CLOUDINARY     = True
MAX_UPLOAD_WORKERS       = 5     # parallel uploads within one product
CLOUDINARY_CACHE_FILE    = os.path.join(OUTPUT_DIR, "cloudinary_cache.json")

# ── Network resilience ────────────────────────────────────────────────────────
NETWORK_RETRY_ATTEMPTS   = 5
NETWORK_RETRY_BASE_SEC   = 3
NETWORK_WAIT_MAX_SEC     = 180    # wait up to 3 min for reconnect
NETWORK_ERROR_MARKERS    = (
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_REFUSED",
    "ERR_NETWORK_CHANGED",
    "ERR_TIMED_OUT",
    "ERR_ADDRESS_UNREACHABLE",
    "net::ERR",
)


class NetworkUnavailableError(Exception):
    """Raised when page navigation fails due to no internet after retries."""


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ── Master filter definitions ──────────────────────────────────────────────────
# Each key maps to a filter object from the provided filter list.
# PARENT filters (used as first entry for every sub-category):
#   "office_chairs" → parent of all chair sub-cats
#   "desks"         → parent of all table/desk sub-cats
#   "home_office"   → parent of home-office sub-cats

F = {
    # ── Chair filters ──────────────────────────────────────────────────────
    "mesh_chairs":       {"name": "Mesh Office Chairs",       "type": "Office Chairs", "sub_category": "office"},
    "leather_chairs":    {"name": "Leather Office Chairs",    "type": "Office Chairs", "sub_category": "office"},
    "multipurpose":      {"name": "multipurpose chair",       "type": "Office Chairs", "sub_category": "office"},
    "stackable":         {"name": "Stackable chairs",         "type": "Office Chairs", "sub_category": "office"},
    "conference_chairs": {"name": "Conference Office Chairs", "type": "Office Chairs", "sub_category": "office"},
    "recliner_chairs":   {"name": "Recliner Office Chairs",   "type": "Office Chairs", "sub_category": "office"},
    "task_chairs":       {"name": "Task Office Chairs",       "type": "Office Chairs", "sub_category": "office"},
    "ergonomic_chairs":  {"name": "Ergonomic Office Chairs",  "type": "Office Chairs", "sub_category": "office"},
    "executive_chairs":  {"name": "Executive Office Chairs",  "type": "Office Chairs", "sub_category": "office"},
    "home_chairs":       {"name": "Home Office Chairs",       "type": "Office Chairs", "sub_category": "office"},
    "stools":            {"name": "Office Stools",            "type": "Office Chairs", "sub_category": "office"},
    "side_chairs":       {"name": "Side Chairs",              "type": "Office Chairs", "sub_category": "office"},

    # ── Desk / Table filters ───────────────────────────────────────────────
    "desks":             {"name": "Desks",                    "type": "Desks",         "sub_category": "office"},
    "desks_tables":      {"name": "Desks & Office Tables",    "type": "Desks",         "sub_category": "office"},
    "meeting_tables":    {"name": "Meeting Tables",           "type": "Desks",         "sub_category": "office"},
    "computer_desks":    {"name": "Computer Desks",           "type": "Desks",         "sub_category": "office"},
    "coffee_tables":     {"name": "Coffee Tables",            "type": "Desks",         "sub_category": "office"},
    "executive_desks":   {"name": "Executive Desks",          "type": "Desks",         "sub_category": "office"},
    "reception_desks":   {"name": "Reception Desks",          "type": "Desks",         "sub_category": "office"},
    "workstation":       {"name": "Work Station",             "type": "Desks",         "sub_category": "office"},
    "standing_desks":    {"name": "Standing Desks",           "type": "Desks",         "sub_category": "office"},

    # ── Home Office filters ────────────────────────────────────────────────
    "home_office":       {"name": "Home Office",              "type": "Home Office",   "sub_category": "office"},
    "home_storage":      {"name": "Home Office Storage",      "type": "Home Office",   "sub_category": "office"},
    "home_lighting":     {"name": "Home Office Lighting",     "type": "Home Office",   "sub_category": "office"},
    "desk_accessories":  {"name": "Desk Accessories",         "type": "Home Office",   "sub_category": "office"},
}

# ── RULE: every sub-category filter list must begin with its parent filter ────
#
#  Parent filter keys by group:
#    Office Chairs sub-cats  → "office_chairs"  (first)
#    Desks / Tables sub-cats → "desks"          (first)
#    Home Office sub-cats    → "home_office"    (first)
#
#  Verified below in the CATEGORIES list — each "filters" array:
#    1. Starts with the parent key
#    2. Followed by the specific sub-category key(s)
#
# ── All categories ─────────────────────────────────────────────────────────────

CATEGORIES = [

    # ════════════════════════════════════════════════════════════════════════
    # OFFICE CHAIRS  (parent filter: "office_chairs")
    # ════════════════════════════════════════════════════════════════════════
    
    {
        "name":          "Ergonomic Chairs",
        "url":           f"{BASE_URL}/category/office-chairs/ergonomic-chairs/",
        "json_file":     "ergonomic_chairs.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        # parent first → specific sub second
        "filters":       ["office_chairs", "ergonomic_chairs"],
    },
    {
        "name":          "Executive Chairs",
        "url":           f"{BASE_URL}/category/office-chairs/executive-chairs/",
        "json_file":     "executive_chairs.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        "filters":       ["office_chairs", "executive_chairs"],
    },
    {
        "name":          "Conference Chairs",
        "url":           f"{BASE_URL}/category/office-chairs/conference-chairs/",
        "json_file":     "conference_chairs.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        "filters":       ["office_chairs", "conference_chairs", "stackable", "multipurpose"],
    },
    {
        "name":          "Task Chairs",
        "url":           f"{BASE_URL}/category/office-chairs/task-chairs/",
        "json_file":     "task_chairs.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        "filters":       ["office_chairs", "task_chairs"],
    },
    {
        "name":          "Recliner Chairs",
        "url":           f"{BASE_URL}/category/office-chairs/recliner-chairs/",
        "json_file":     "recliner_chairs.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        "filters":       ["office_chairs", "recliner_chairs"],
    },
    {
        "name":          "High Stools",
        "url":           f"{BASE_URL}/category/office-chairs/high-stools/",
        "json_file":     "high_stools.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        "filters":       ["office_chairs", "stools"],
    },
    {
        "name":          "School Chairs",
        "url":           f"{BASE_URL}/category/office-chairs/school-chair/",
        "json_file":     "school_chairs.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        "filters":       ["office_chairs", "side_chairs", "stackable"],
    },
    {
        "name":          "Chair Customization",
        "url":           f"{BASE_URL}/category/office-chairs/customization-options/",
        "json_file":     "chair_customization.json",
        "parent_folder": "Office Chairs",
        "category_slug": "office-chairs",
        "filters":       ["office_chairs", "mesh_chairs", "leather_chairs"],
    },

    # ════════════════════════════════════════════════════════════════════════
    # OFFICE TABLES  (parent filter: "desks")
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Office Tables",
        "url":           f"{BASE_URL}/category/office-tables/",
        "json_file":     "office_tables.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        # Top-level — "desks" parent + generic "desks_tables"
        "filters":       ["desks", "desks_tables"],
    },
    {
        "name":          "Executive Desks",
        "url":           f"{BASE_URL}/category/office-tables/executive-desks/",
        "json_file":     "executive_desks.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        "filters":       ["desks", "executive_desks"],
    },
    {
        "name":          "Ergonomic Desks",
        "url":           f"{BASE_URL}/category/office-tables/ergonomic-desks/",
        "json_file":     "ergonomic_desks.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        "filters":       ["desks", "standing_desks", "computer_desks"],
    },
    {
        "name":          "Reception Desks",
        "url":           f"{BASE_URL}/category/office-tables/reception-desks/",
        "json_file":     "reception_desks.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        "filters":       ["desks", "reception_desks"],
    },
    {
        "name":          "Workstations",
        "url":           f"{BASE_URL}/category/office-tables/workstations/",
        "json_file":     "workstations.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        "filters":       ["desks", "workstation", "computer_desks"],
    },
    {
        "name":          "Coffee Tables",
        "url":           f"{BASE_URL}/category/office-tables/coffee-tables/",
        "json_file":     "coffee_tables.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        # parent "desks" first, then specific "coffee_tables"
        "filters":       ["desks", "coffee_tables"],
    },
    {
        "name":          "Meeting Tables",
        "url":           f"{BASE_URL}/category/office-tables/meeting-tables/",
        "json_file":     "meeting_tables.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        "filters":       ["desks", "meeting_tables"],
    },
    {
        "name":          "Folding Tables",
        "url":           f"{BASE_URL}/category/office-tables/folding-tables/",
        "json_file":     "folding_tables.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        "filters":       ["desks", "desks_tables"],
    },
    {
        "name":          "Training Tables",
        "url":           f"{BASE_URL}/category/office-tables/training-tables/",
        "json_file":     "training_tables.json",
        "parent_folder": "Office Tables",
        "category_slug": "office-tables",
        "filters":       ["desks", "desks_tables"],
    },

    # ════════════════════════════════════════════════════════════════════════
    # OFFICE FURNITURE  (parent filter: "home_office" for storage/accessories)
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Office Furniture",
        "url":           f"{BASE_URL}/category/office-furniture/",
        "json_file":     "office_furniture.json",
        "parent_folder": "Office Furniture",
        "category_slug": "office-furniture",
        "filters":       ["home_office", "desks_tables"],
    },
    {
        "name":          "Storage Furniture",
        "url":           f"{BASE_URL}/category/storage-furniture/",
        "json_file":     "storage_furniture.json",
        "parent_folder": "Office Furniture",
        "category_slug": "office-furniture",
        # parent "home_office" first, then specific storage sub
        "filters":       ["home_office", "home_storage"],
    },

    # ════════════════════════════════════════════════════════════════════════
    # COMPUTING  (no furniture filters — leave types empty)
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Computing",
        "url":           f"{BASE_URL}/category/computing/",
        "json_file":     "computing.json",
        "parent_folder": "Computing",
        "category_slug": "computing",
        "filters":       [],
    },
    {
        "name":          "Laptops",
        "url":           f"{BASE_URL}/category/laptop/",
        "json_file":     "laptops.json",
        "parent_folder": "Computing",
        "category_slug": "computing",
        "filters":       [],
    },
    {
        "name":          "HP Laptops",
        "url":           f"{BASE_URL}/category/hp/hp-laptop/",
        "json_file":     "hp_laptops.json",
        "parent_folder": "Computing",
        "category_slug": "computing",
        "filters":       [],
    },
    {
        "name":          "HP Monitors",
        "url":           f"{BASE_URL}/category/hp/hp-monitor/",
        "json_file":     "hp_monitors.json",
        "parent_folder": "Computing",
        "category_slug": "computing",
        "filters":       [],
    },

    # ════════════════════════════════════════════════════════════════════════
    # PRINTERS & SCANNERS
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Printers & Scanners",
        "url":           f"{BASE_URL}/category/it-products/printers-and-scanners/",
        "json_file":     "printers_scanners.json",
        "parent_folder": "Printers & Scanners",
        "category_slug": "printers-scanners",
        "filters":       [],
    },
    {
        "name":          "HP Printers",
        "url":           f"{BASE_URL}/category/hp/hp-printers/",
        "json_file":     "hp_printers.json",
        "parent_folder": "Printers & Scanners",
        "category_slug": "printers-scanners",
        "filters":       [],
    },

    # ════════════════════════════════════════════════════════════════════════
    # INKS & TONERS
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Inks & Toners",
        "url":           f"{BASE_URL}/category/it-products/inks-toners/",
        "json_file":     "inks_toners.json",
        "parent_folder": "Inks & Toners",
        "category_slug": "inks-toners",
        "filters":       [],
    },
    {
        "name":          "HP Inks & Toners",
        "url":           f"{BASE_URL}/category/hp/hp-inks-toners/",
        "json_file":     "hp_inks_toners.json",
        "parent_folder": "Inks & Toners",
        "category_slug": "inks-toners",
        "filters":       [],
    },

    # ════════════════════════════════════════════════════════════════════════
    # STATIONERY
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Stationery",
        "url":           f"{BASE_URL}/category/stationery/",
        "json_file":     "stationery.json",
        "parent_folder": "Stationery",
        "category_slug": "stationery",
        "filters":       [],
    },
    {
        "name":          "Office Stationery",
        "url":           f"{BASE_URL}/category/office-stationery/",
        "json_file":     "office_stationery.json",
        "parent_folder": "Stationery",
        "category_slug": "stationery",
        "filters":       ["home_office", "desk_accessories"],
    },

    # ════════════════════════════════════════════════════════════════════════
    # OFFICE MACHINES
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Office Machines",
        "url":           f"{BASE_URL}/category/office-machines/",
        "json_file":     "office_machines.json",
        "parent_folder": "Office Machines",
        "category_slug": "office-machines",
        "filters":       [],
    },
    {
        "name":          "Shredders",
        "url":           f"{BASE_URL}/category/office-supplies/office-tools/shredders/",
        "json_file":     "shredders.json",
        "parent_folder": "Office Machines",
        "category_slug": "office-machines",
        "filters":       [],
    },

    # ════════════════════════════════════════════════════════════════════════
    # ACCESSORIES
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Accessories",
        "url":           f"{BASE_URL}/category/accessories/",
        "json_file":     "accessories.json",
        "parent_folder": "Accessories",
        "category_slug": "accessories",
        "filters":       ["home_office", "desk_accessories"],
    },

    # ════════════════════════════════════════════════════════════════════════
    # SMART TECH
    # ════════════════════════════════════════════════════════════════════════
    {
        "name":          "Smart Tech",
        "url":           f"{BASE_URL}/category/smart-tech/",
        "json_file":     "smart_tech.json",
        "parent_folder": "Smart Tech",
        "category_slug": "smart-tech",
        "filters":       [],
    },
]


# ── Filter builder ────────────────────────────────────────────────────────────

def build_filters(filter_keys: list) -> dict:
    """Convert a list of filter keys into the required filters payload."""
    types = [F[k] for k in filter_keys if k in F]
    return {"types": types}


# ── Helpers ───────────────────────────────────────────────────────────────────

def human_sleep(lo=1.2, hi=2.8):
    time.sleep(random.uniform(lo, hi))


def is_network_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(marker in msg for marker in NETWORK_ERROR_MARKERS)


def check_internet(timeout: int = 12) -> bool:
    """Quick HEAD request to verify the target site is reachable."""
    try:
        resp = requests.head(BASE_URL, timeout=timeout, allow_redirects=True)
        return resp.status_code < 500
    except Exception:
        return False


def wait_for_internet(max_wait_sec: int = NETWORK_WAIT_MAX_SEC) -> bool:
    if check_internet():
        return True
    print(f"     🌐 Network offline — waiting up to {max_wait_sec}s for reconnect...")
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        human_sleep(5, 8)
        if check_internet():
            print("     🌐 Network restored — resuming")
            return True
    print("     ❌ Network still offline after waiting")
    return False


def goto_with_retry(page, url: str, wait_until="domcontentloaded", timeout=60_000):
    """Navigate with retries on transient network failures."""
    last_err = None
    for attempt in range(1, NETWORK_RETRY_ATTEMPTS + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except Exception as e:
            last_err = e
            if not is_network_error(e):
                raise
            print(f"     ⚠️  Network error (attempt {attempt}/{NETWORK_RETRY_ATTEMPTS})")
            if attempt >= NETWORK_RETRY_ATTEMPTS:
                break
            if not wait_for_internet():
                break
            backoff = NETWORK_RETRY_BASE_SEC * attempt
            human_sleep(backoff, backoff + 2)
    raise NetworkUnavailableError(str(last_err)) from last_err


def slug_from_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").split("/")[-1]


def derive_subtitle(title: str, brand: str) -> str:
    if brand and title.upper().startswith(brand.upper()):
        return title[len(brand):].strip(" -–:")
    parts = title.split()
    return " ".join(parts[:5]) if len(parts) > 5 else title


def extract_brand(title: str) -> str:
    KNOWN_BRANDS = [
        "HP", "Canon", "Epson", "Brother", "Samsung", "Dell", "Lenovo",
        "Asus", "Acer", "Microsoft", "Logitech", "APC", "Deli", "Gorenje",
        "Hansa", "Alphason", "Xiaomi", "Redmi",
    ]
    for brand in KNOWN_BRANDS:
        if title.upper().startswith(brand.upper()):
            return brand
    return ""


def load_existing(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {p["url"]: p for p in data if "url" in p}
        except Exception:
            pass
    return {}


def save_products(path: str, products: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(products.values()), f, indent=2, ensure_ascii=False)


# ── Cloudinary upload helpers ─────────────────────────────────────────────────

def is_cloudinary_url(url: str) -> bool:
    return bool(url) and "res.cloudinary.com" in url


def load_cloudinary_cache() -> dict:
    if os.path.exists(CLOUDINARY_CACHE_FILE):
        try:
            with open(CLOUDINARY_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cloudinary_cache(cache: dict):
    os.makedirs(os.path.dirname(CLOUDINARY_CACHE_FILE), exist_ok=True)
    with open(CLOUDINARY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def upload_single_image(
    source_url: str,
    public_id: str,
    cache: dict,
    cache_lock: threading.Lock,
) -> str:
    """Upload one remote image to Cloudinary; returns Cloudinary URL or original on failure."""
    with cache_lock:
        if source_url in cache:
            return cache[source_url]

    if is_cloudinary_url(source_url):
        return source_url

    try:
        resp = requests.post(
            CLOUDINARY_UPLOAD_URL,
            data={
                "file": source_url,
                "upload_preset": CLOUDINARY_UPLOAD_PRESET,
                "public_id": public_id,
            },
            timeout=120,
        )
        resp.raise_for_status()
        cloud_url = resp.json()["secure_url"]
        with cache_lock:
            cache[source_url] = cloud_url
        return cloud_url
    except Exception as e:
        print(f"         ⚠️  Cloudinary upload failed ({public_id}): {e}")
        return source_url


def upload_product_images(
    product_url: str,
    source_urls: list,
    cache: dict,
    cache_lock: threading.Lock,
) -> list:
    """
    Upload all images for ONE product in parallel.
    Results are indexed by position so image order never crosses products.
    """
    if not source_urls:
        return []

    slug = slug_from_url(product_url)
    results = [None] * len(source_urls)
    pending = []

    for idx, src in enumerate(source_urls):
        if is_cloudinary_url(src):
            results[idx] = src
            continue
        with cache_lock:
            cached = cache.get(src)
        if cached:
            results[idx] = cached
            continue
        pending.append((idx, src))

    if not pending:
        return results

    workers = min(MAX_UPLOAD_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(
                upload_single_image,
                src,
                f"officeland/{slug}/{idx}",
                cache,
                cache_lock,
            ): idx
            for idx, src in pending
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                print(f"         ⚠️  Upload worker error [{idx}]: {e}")
                results[idx] = source_urls[idx]

    return results


def images_need_cloudinary_upload(images: list) -> bool:
    if not images or not UPLOAD_TO_CLOUDINARY:
        return False
    return any(not is_cloudinary_url(url) for url in images)


def finalize_product_upload(
    pending: dict,
    products_dict: dict,
    out_path: str,
    cache: dict,
) -> int:
    """Wait for a product's upload batch, persist product + cache, return upload count."""
    prod_url = pending["prod_url"]
    product = pending["product"]
    source_urls = pending["source_urls"]
    upload_future = pending["future"]

    uploaded = 0
    try:
        cloud_images = upload_future.result(timeout=600)
        product["images"] = cloud_images
        uploaded = sum(
            1 for src, dst in zip(source_urls, cloud_images)
            if src != dst and is_cloudinary_url(dst)
        )
    except Exception as e:
        print(f"         ⚠️  Image batch failed for {slug_from_url(prod_url)}: {e}")

    products_dict[prod_url] = product
    save_products(out_path, products_dict)
    save_cloudinary_cache(cache)
    return uploaded


def make_browser_context(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1440, "height": 900},
        locale="en-GB",
        extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, ctx


# ── Phase 1: listing-page link discovery (capped at MAX_PRODUCTS_PER_CATEGORY) ─

def get_product_links(page, category_url: str, already_have: int = 0) -> list:
    """
    Paginate through listing pages and return up to
    (MAX_PRODUCTS_PER_CATEGORY - already_have) new product URLs.
    Stops early once the cap is reached so we never over-fetch.
    """
    all_links: set = set()
    page_num = 1
    remaining = MAX_PRODUCTS_PER_CATEGORY - already_have

    if remaining <= 0:
        print(f"     ✅ Already at cap ({MAX_PRODUCTS_PER_CATEGORY}) — skipping discovery")
        return []

    while True:
        url = f"{category_url}?per_page={PER_PAGE}&paged={page_num}"
        print(f"     📄 Listing page {page_num}: {url}")
        try:
            goto_with_retry(page, url)
            try:
                page.wait_for_selector(
                    "ul.products li.product, .products .product",
                    timeout=15_000
                )
            except PWTimeout:
                print(f"     ⚠️  No product grid on page {page_num} — done")
                break

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            human_sleep(1, 1.5)

            # Collect product hrefs
            hrefs = page.eval_on_selector_all(
                "ul.products li.product a, .products .product a",
                "els => [...new Set(els.map(e => e.href))]"
            )
            new_links = {
                h for h in hrefs
                if re.search(r"/product/[^/?#]+/?$", urlparse(h).path)
                and "wishlist" not in h
                and "compare" not in h
            }

            # Fallback broader selector
            if not new_links:
                all_hrefs = page.eval_on_selector_all(
                    "a[href*='/product/']",
                    "els => [...new Set(els.map(e => e.href))]"
                )
                new_links = {
                    h for h in all_hrefs
                    if re.search(r"/product/[^/?#]+/?$", urlparse(h).path)
                    and "wishlist" not in h and "compare" not in h
                }

            if not new_links:
                print(f"     ✅ No products on page {page_num} — done")
                break

            before = len(all_links)
            all_links |= new_links
            after = len(all_links)
            print(f"        {len(new_links)} found (+{after - before} new, {after} total)")

            # Stop if we have enough to hit the cap
            if len(all_links) >= remaining:
                print(f"     🛑 Reached cap limit ({MAX_PRODUCTS_PER_CATEGORY}) — stopping discovery")
                break

            has_next = page.locator("a.next.page-numbers").count() > 0
            if not has_next:
                break

            page_num += 1
            human_sleep(1, 2)

        except PWTimeout:
            print(f"     ⚠️  Timeout on page {page_num}")
            break
        except NetworkUnavailableError as e:
            print(f"     ❌ Discovery stopped — no internet: {e}")
            if not all_links:
                raise
            break
        except Exception as e:
            print(f"     ⚠️  Error on page {page_num}: {e}")
            break

    # Enforce hard cap — take only what we need
    links = list(all_links)[:remaining]
    return links


# ── Phase 2: Product Detail Page ─────────────────────────────────────────────

def scrape_pdp(page, url: str, cat: dict) -> dict:
    """Scrape one product page and return a payload matching the required schema."""
    filter_keys   = cat["filter_keys"]
    category_name = cat["name"]
    cat_slug      = cat["category_slug"]
    json_file     = cat["json_file"]

    product = {
        "title":            "",
        "subtitle":         "",
        "brand":            "",
        "sku":              "",
        "url":              url,
        "category":         cat_slug,
        "overview":         "",
        "price":            "",       # site is "Add to Quote" only
        "currency":         "NGN",
        "color":            [],
        "rating":           "",
        "review_count":     "",
        "availability":     True,
        "key_features":     [],
        "specifications":   [],
        "images":           [],
        "filters":          build_filters(filter_keys),
        "warranty":         "",
        "is_active":        True,
        "featured_product": False,
        "new_arrival":      False,
        "best_seller":      False,
        "is_deleted":       False,
        # Internal tracking fields
        "_scraped_from":    "pdp",
        "_category_file":   json_file,
        "_primary_category": category_name,
    }

    try:
        goto_with_retry(page, url)
        human_sleep(1.5, 2.5)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        human_sleep(0.8, 1.2)

        # ── Title ──────────────────────────────────────────────────────────
        for sel in ["h1.product_title", "h1.entry-title", "h1"]:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible(timeout=3000):
                    product["title"] = el.inner_text().strip()
                    break
            except Exception:
                pass

        # ── Brand & subtitle ───────────────────────────────────────────────
        brand = ""
        try:
            brand_el = page.locator(
                ".product_meta .brand a, .product_meta .wd-attr-name + span a"
            ).first
            if brand_el.count() > 0:
                brand = brand_el.inner_text().strip()
        except Exception:
            pass
        if not brand:
            brand = extract_brand(product["title"])
        product["brand"]    = brand
        product["subtitle"] = derive_subtitle(product["title"], brand)

        # ── SKU ────────────────────────────────────────────────────────────
        try:
            sku_el = page.locator(".sku").first
            if sku_el.count() > 0:
                product["sku"] = sku_el.inner_text().strip()
        except Exception:
            pass

        # ── Availability ───────────────────────────────────────────────────
        try:
            if page.locator(".stock.out-of-stock").count() > 0:
                product["availability"] = False
        except Exception:
            pass

        # ── Colors ────────────────────────────────────────────────────────
        COLOR_KEYWORDS = [
            "Black", "White", "Grey", "Gray", "Blue", "Red", "Green",
            "Brown", "Beige", "Orange", "Yellow", "Purple", "Silver",
            "Gold", "Cream", "Navy", "Maroon",
        ]
        colors = [c for c in COLOR_KEYWORDS if c.lower() in product["title"].lower()]
        try:
            color_els = page.query_selector_all(
                ".wd-swatches .wd-swatch-label, "
                ".variations select[name*='color'] option:not([value=''])"
            )
            for el in color_els:
                t = el.inner_text().strip()
                if t and t not in colors:
                    colors.append(t)
        except Exception:
            pass
        product["color"] = colors

        # ── Rating & reviews ───────────────────────────────────────────────
        try:
            rating_el = page.locator(".woocommerce-product-rating .rating").first
            if rating_el.count() > 0:
                product["rating"] = rating_el.inner_text().strip()
            count_el = page.locator(".woocommerce-product-rating .count").first
            if count_el.count() > 0:
                nums = re.findall(r"\d+", count_el.inner_text())
                if nums:
                    product["review_count"] = nums[0]
        except Exception:
            pass

        # ── Overview / short description ───────────────────────────────────
        try:
            el = page.locator(
                ".woocommerce-product-details__short-description"
            ).first
            if el.count() > 0:
                product["overview"] = el.inner_text().strip()[:1500]
        except Exception:
            pass

        # ── Description tab → key features ────────────────────────────────
        key_features = []
        full_desc_text = ""
        try:
            tab = page.locator(
                "a[href='#tab-description'], #tab-title-description a"
            ).first
            if tab.count() > 0:
                try:
                    tab.click(timeout=3000)
                    human_sleep(0.4, 0.8)
                except Exception:
                    pass

            desc_el = page.locator("#tab-description").first
            if desc_el.count() > 0:
                full_desc_text = desc_el.inner_text().strip()

                # Bullet items → key features
                for li in desc_el.query_selector_all("li"):
                    t = li.inner_text().strip()
                    if t and len(t) < 200:
                        key_features.append(t)

                # Fill overview from first long paragraph if not already set
                if not product["overview"]:
                    paras = [
                        p.strip() for p in full_desc_text.split("\n")
                        if len(p.strip()) > 40
                    ]
                    if paras:
                        product["overview"] = paras[0][:1500]
        except Exception:
            pass
        product["key_features"] = key_features[:12]

        # ── Specifications ─────────────────────────────────────────────────
        specs = []
        try:
            addl_tab = page.locator(
                "a[href='#tab-additional_information'], "
                "#tab-title-additional_information a"
            ).first
            if addl_tab.count() > 0:
                try:
                    addl_tab.click(timeout=3000)
                    human_sleep(0.4, 0.8)
                except Exception:
                    pass

            spec_table = page.locator(
                "#tab-additional_information table.shop_attributes"
            ).first
            if spec_table.count() > 0:
                spec_items = []
                for row in spec_table.locator("tr").all():
                    th = row.locator("th").first
                    td = row.locator("td").first
                    if th.count() > 0 and td.count() > 0:
                        key = th.inner_text().strip()
                        val = td.inner_text().strip()
                        if key and val:
                            spec_items.append({"key": key, "value": val})
                if spec_items:
                    specs.append({"title": "Specifications", "specs": spec_items})

            # Description tables
            for tbl in page.query_selector_all("#tab-description table"):
                tbl_specs = []
                for row in tbl.query_selector_all("tr"):
                    cells = row.query_selector_all("td, th")
                    if len(cells) >= 2:
                        key = cells[0].inner_text().strip()
                        val = cells[1].inner_text().strip()
                        if key and val:
                            tbl_specs.append({"key": key, "value": val})
                if tbl_specs:
                    specs.append({"title": "Details", "specs": tbl_specs})
        except Exception:
            pass
        product["specifications"] = specs

        # ── Warranty ──────────────────────────────────────────────────────
        warranty = ""
        m = re.search(
            r"(\d+[\s-]*(?:year|month|yr)s?\s*(?:warranty|guarantee))",
            full_desc_text, re.IGNORECASE
        )
        if m:
            warranty = m.group(1).strip()
        for grp in specs:
            for spec in grp.get("specs", []):
                if "warrant" in spec["key"].lower() or "guarantee" in spec["key"].lower():
                    warranty = spec["value"]
        product["warranty"] = warranty

        # ── Images (max 5) ────────────────────────────────────────────────
        images = []
        try:
            for img in page.query_selector_all(
                ".woocommerce-product-gallery__image img, "
                ".woocommerce-product-gallery img, "
                "figure.woocommerce-product-gallery__image img"
            ):
                src = (
                    img.get_attribute("data-large_image") or
                    img.get_attribute("data-src") or
                    img.get_attribute("data-lazy-src") or
                    img.get_attribute("src") or ""
                )
                if src and "lazy.svg" not in src and src.startswith("http") and src not in images:
                    images.append(src)
                    if len(images) == MAX_IMAGES:
                        break
        except Exception:
            pass

        # Fallback: any wp-content/uploads image
        if not images:
            try:
                for img in page.query_selector_all("img"):
                    for attr in ["data-large_image", "data-src", "data-lazy-src", "src"]:
                        src = img.get_attribute(attr) or ""
                        if (
                            src
                            and "wp-content/uploads" in src
                            and "lazy.svg" not in src
                            and "logo" not in src.lower()
                            and "favicon" not in src.lower()
                            and src not in images
                        ):
                            images.append(src)
                            break
                    if len(images) == MAX_IMAGES:
                        break
            except Exception:
                pass

        product["images"] = images[:MAX_IMAGES]

    except NetworkUnavailableError as e:
        print(f"       ⚠️  PDP network error for {url}: {e}")
        raise
    except Exception as e:
        print(f"       ⚠️  PDP error for {url}: {e}")

    return product


# ── Per-category runner ───────────────────────────────────────────────────────

def scrape_category(cat_def: dict) -> int:
    name          = cat_def["name"]
    cat_url       = cat_def["url"]
    json_file     = cat_def["json_file"]
    parent_folder = cat_def["parent_folder"]
    filter_keys   = cat_def["filters"]
    out_path      = os.path.join(OUTPUT_DIR, parent_folder, json_file)

    cat = {
        "name":          name,
        "category_slug": cat_def["category_slug"],
        "json_file":     json_file,
        "filter_keys":   filter_keys,
    }

    print(f"\n{'='*65}")
    print(f"  🗂️  {parent_folder} / {name}  [max {MAX_PRODUCTS_PER_CATEGORY} products]")
    print(f"       → {os.path.join(parent_folder, json_file)}")
    print(f"{'='*65}")

    products_dict = load_existing(out_path)
    already_done  = {u for u, p in products_dict.items() if p.get("_scraped_from") == "pdp"}
    print(f"   ♻️  {len(already_done)} already scraped  |  cap = {MAX_PRODUCTS_PER_CATEGORY}")

    # If already at cap, skip entirely
    if len(already_done) >= MAX_PRODUCTS_PER_CATEGORY:
        print(f"   ✅ Already at cap — skipping")
        return len(products_dict)

    with sync_playwright() as pw:
        browser, ctx = make_browser_context(pw)
        page = ctx.new_page()

        try:
            # Phase 1 – discover links (respects remaining quota)
            print(f"\n   📋 Phase 1 — discovering product links...")
            try:
                links = get_product_links(page, cat_url, already_have=len(already_done))
            except NetworkUnavailableError:
                print(f"   ⏭️  Skipping category — check WiFi/VPN and re-run to resume")
                return len(products_dict)
            print(f"   ✅ {len(links)} new links to scrape")

            # Phase 2 – scrape PDPs (+ pipelined Cloudinary uploads)
            need = [l for l in links if l not in already_done]
            # Safety cap in case discovery over-fetched
            need = need[: MAX_PRODUCTS_PER_CATEGORY - len(already_done)]
            print(f"\n   📋 Phase 2 — scraping {len(need)} products...")
            if UPLOAD_TO_CLOUDINARY:
                print(f"   ☁️  Cloudinary uploads enabled ({MAX_UPLOAD_WORKERS} parallel / product)")

            cloudinary_cache = load_cloudinary_cache()
            cache_lock = threading.Lock()
            pending_upload = None

            with ThreadPoolExecutor(max_workers=1) as upload_executor:
                for idx, prod_url in enumerate(need, 1):
                    slug = slug_from_url(prod_url)
                    print(f"   [{idx:>2}/{len(need)}] {slug}")

                    try:
                        product = scrape_pdp(page, prod_url, cat)
                    except NetworkUnavailableError:
                        if pending_upload:
                            finalize_product_upload(
                                pending_upload, products_dict, out_path, cloudinary_cache
                            )
                        print(f"   ⏭️  Stopping category mid-scrape — check WiFi/VPN and re-run")
                        break

                    source_images = list(product.get("images", []))

                    # Previous product's uploads ran while this PDP was scraping
                    if pending_upload:
                        uploaded = finalize_product_upload(
                            pending_upload, products_dict, out_path, cloudinary_cache
                        )
                        if uploaded:
                            print(f"         ☁️  {uploaded} image(s) uploaded to Cloudinary")
                        pending_upload = None

                    if images_need_cloudinary_upload(source_images):
                        future = upload_executor.submit(
                            upload_product_images,
                            prod_url,
                            source_images,
                            cloudinary_cache,
                            cache_lock,
                        )
                        pending_upload = {
                            "prod_url": prod_url,
                            "product": product,
                            "source_urls": source_images,
                            "future": future,
                        }
                    else:
                        products_dict[prod_url] = product
                        save_products(out_path, products_dict)

                    total_done = len(already_done) + idx
                    title  = product.get("title") or "(no title)"
                    imgs   = len(source_images)
                    avail  = "✅" if product.get("availability") else "❌"
                    fcount = len(product.get("filters", {}).get("types", []))
                    pending_note = "  ☁️ uploading…" if pending_upload else ""
                    print(
                        f"         {avail} [{total_done}/{MAX_PRODUCTS_PER_CATEGORY}] "
                        f"{title[:45]}  "
                        f"| imgs={imgs}  "
                        f"| filters={fcount}{pending_note}"
                    )
                    human_sleep(1.5, 3.0)

                if pending_upload:
                    uploaded = finalize_product_upload(
                        pending_upload, products_dict, out_path, cloudinary_cache
                    )
                    if uploaded:
                        print(f"         ☁️  {uploaded} image(s) uploaded to Cloudinary")

        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback; traceback.print_exc()
        finally:
            browser.close()

    total = len(products_dict)
    print(f"\n   ✅ Done — {total} products saved → {out_path}")
    return total


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🛒  OfficeLand Nigeria Scraper  v4")
    print(f"    Output dir  : {OUTPUT_DIR}/")
    print(f"    Max products: {MAX_PRODUCTS_PER_CATEGORY} per category")
    print(f"    Max images  : {MAX_IMAGES} per product")
    print(f"    Cloudinary  : {'ON' if UPLOAD_TO_CLOUDINARY else 'OFF'}")
    print(f"    Categories  : {len(CATEGORIES)}\n")

    # Preview folder structure
    folders = sorted({c["parent_folder"] for c in CATEGORIES})
    for folder in folders:
        files = [c["json_file"] for c in CATEGORIES if c["parent_folder"] == folder]
        cats_in_folder = [c for c in CATEGORIES if c["parent_folder"] == folder]
        print(f"  📁 {folder}/  ({len(files)} files)")
        for c in cats_in_folder:
            fkeys = c["filters"]
            filters_preview = " + ".join(fkeys) if fkeys else "–"
            print(f"       └─ {c['json_file']:<35} filters: [{filters_preview}]")
    print()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not check_internet():
        print("❌ Cannot reach officelandng.com — check your WiFi/VPN, then re-run.")
        sys.exit(1)

    grand_total = 0
    for i, cat in enumerate(CATEGORIES, 1):
        print(f"\n[{i}/{len(CATEGORIES)}]", end=" ")
        count = scrape_category(cat)
        grand_total += count
        if i < len(CATEGORIES):
            time.sleep(random.uniform(2, 4))

    print(f"\n{'='*65}")
    print(f"🎉  All done!")
    print(f"    Products : {grand_total} total")
    print(f"    Max/cat  : {MAX_PRODUCTS_PER_CATEGORY}")
    print(f"    Output   : {OUTPUT_DIR}/")
    print(f"{'='*65}")