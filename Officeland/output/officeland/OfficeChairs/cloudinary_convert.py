"""
Cloudinary Bulk Image Converter — v5 FINAL
==========================================
Key fixes in v5:
  • Deduplicates products by URL before processing (duplicate entries
    were causing converted URLs to be overwritten by stale originals)
  • Writes the deduplicated list back to disk (no more ghost duplicates)
  • Sequential product processing + parallel image uploads per product
  • Fully resumable — already-converted products skipped on re-run
  • Correct is_cloudinary check (res.cloudinary.com OR cloudinary.com)

Usage:
  python3 cloudinary_convert_fixed.py desks.json
  python3 cloudinary_convert_fixed.py /full/path/to/desks.json
  python3 cloudinary_convert_fixed.py /full/path/to/Office\ Chairs/
  python3 cloudinary_convert_fixed.py          ← all JSONs in CWD
"""

import copy
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

# ── Config ────────────────────────────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME    = "decbrtduj"
CLOUDINARY_UPLOAD_PRESET = "unsigned_preset"
CLOUDINARY_UPLOAD_URL    = (
    f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
)

IMAGE_WORKERS  = 5    # parallel image uploads per product
MAX_IMAGES     = 5    # hard cap per product
UPLOAD_RETRIES = 3    # retries on server/timeout errors

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    print(msg, flush=True)


def slug_from_url(product_url: str) -> str:
    path = urlparse(product_url).path
    slug = path.rstrip("/").split("/")[-1]
    return re.sub(r"[^\w\-]", "_", slug)


def make_public_id(file_stem: str, product_slug: str, img_index: int) -> str:
    safe_stem = re.sub(r"[^\w\-]", "_", file_stem)
    return f"officeland/{safe_stem}/{product_slug}/img_{img_index}"


def is_cloudinary(url: str) -> bool:
    """True if the URL is already hosted on Cloudinary."""
    return bool(url) and (
        "res.cloudinary.com" in url or
        ".cloudinary.com" in url
    )


def all_cloudinary(product: dict) -> bool:
    """True only when every non-empty image URL is already on Cloudinary."""
    imgs = [u for u in product.get("images", []) if u]
    return bool(imgs) and all(is_cloudinary(u) for u in imgs)


def deduplicate(products: list) -> tuple[list, int]:
    """
    Keep only the FIRST occurrence of each product URL.
    Returns (deduped_list, num_removed).
    """
    seen = set()
    deduped = []
    for p in products:
        key = p.get("url", id(p))   # fall back to object id if no url
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped, len(products) - len(deduped)


def load_json(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(abs_path: str, products: list):
    """Atomic write — tmp then rename, never corrupts the original."""
    tmp = abs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    os.replace(tmp, abs_path)


# ── Single image uploader ─────────────────────────────────────────────────────

def upload_one(original_url: str, public_id: str) -> dict:
    """Upload one image; returns dict with cloudinary_url (falls back on failure)."""
    result = {
        "success":        False,
        "cloudinary_url": original_url,
        "original_url":   original_url,
        "error":          None,
    }

    if is_cloudinary(original_url):
        result["success"] = True
        return result

    payload = {
        "file":          original_url,
        "upload_preset": CLOUDINARY_UPLOAD_PRESET,
        "public_id":     public_id,
        # NOTE: "overwrite" is NOT allowed with unsigned presets
        "resource_type": "image",
    }

    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            resp = requests.post(CLOUDINARY_UPLOAD_URL, data=payload, timeout=60)
            if resp.status_code == 200:
                result["cloudinary_url"] = resp.json().get("secure_url", original_url)
                result["success"]        = True
                return result
            elif resp.status_code >= 500:
                result["error"] = f"HTTP {resp.status_code}"
                time.sleep(2 ** attempt)
            else:
                result["error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
                return result   # 4xx — no point retrying
        except requests.Timeout:
            result["error"] = f"Timeout attempt {attempt}"
            time.sleep(2 ** attempt)
        except Exception as e:
            result["error"] = str(e)
            return result

    return result


# ── Per-product image uploader ─────────────────────────────────────────────────

def upload_product_images(raw_urls: list, file_stem: str, prod_slug: str) -> list:
    """
    Upload all images for ONE product in parallel.
    Returns URLs in the SAME ORDER as raw_urls.
    """
    results    = list(raw_urls)   # start as originals (fallback)
    public_ids = [make_public_id(file_stem, prod_slug, i) for i in range(len(raw_urls))]

    workers = min(IMAGE_WORKERS, len(raw_urls))
    with ThreadPoolExecutor(max_workers=workers) as exe:
        future_map = {
            exe.submit(upload_one, raw_urls[i], public_ids[i]): i
            for i in range(len(raw_urls))
        }
        for future in as_completed(future_map):
            i = future_map[future]
            try:
                res        = future.result()
                results[i] = res["cloudinary_url"]
                icon       = "☁️  ✅" if res["success"] else f"⚠️  {res['error']}"
                short      = res["cloudinary_url"][-70:]
                log(f"      img[{i}] {icon}  …{short}")
            except Exception as e:
                log(f"      img[{i}] ❌ exception: {e}")
                # results[i] already holds the original — safe

    return results


# ── Per-file processor ────────────────────────────────────────────────────────

def process_file(json_path: str):
    abs_path  = os.path.abspath(json_path)
    file_stem = os.path.splitext(os.path.basename(abs_path))[0]

    if not os.path.exists(abs_path):
        log(f"  ⚠️  File not found: {abs_path}")
        return

    raw_products = load_json(abs_path)

    # ── Step 1: Deduplicate by URL ─────────────────────────────────────────
    products, dupes_removed = deduplicate(raw_products)

    if dupes_removed:
        log(f"\n  ⚠️  Removed {dupes_removed} duplicate product(s) from {file_stem}.json")
        # Save the clean deduplicated list immediately so future runs don't hit this
        save_json(abs_path, products)
        log(f"  💾  Saved deduplicated list ({len(products)} unique products)")

    total        = len(products)
    need_convert = [i for i, p in enumerate(products) if not all_cloudinary(p)]
    skipped      = total - len(need_convert)

    log(f"\n{'─'*70}")
    log(f"  📄  {abs_path}")
    log(f"      {total} products  |  {skipped} already Cloudinary  |  {len(need_convert)} to convert")
    log(f"{'─'*70}")

    if not need_convert:
        log("  ✅  All products already converted — nothing to do.")
        return

    for seq, idx in enumerate(need_convert, 1):
        product  = products[idx]
        title    = product.get("title", "")[:60]
        prod_url = product.get("url", f"index_{idx}")

        raw_urls = [u for u in product.get("images", []) if u][:MAX_IMAGES]

        log(f"\n  🔄  [{seq}/{len(need_convert)}] {title}")

        if not raw_urls:
            log(f"      (no images — skipping)")
            continue

        prod_slug  = slug_from_url(prod_url)
        cloud_urls = upload_product_images(raw_urls, file_stem, prod_slug)

        # ── Update in-place and save immediately ───────────────────────────
        products[idx] = copy.deepcopy(product)
        products[idx]["images"] = cloud_urls

        save_json(abs_path, products)

        converted = sum(1 for u in cloud_urls if is_cloudinary(u))
        log(f"  ✅  [{seq}/{len(need_convert)}]  {converted}/{len(cloud_urls)} images converted")
        log(f"  💾  Saved → {abs_path}")

    log(f"\n  🎉  Done — {abs_path}")


# ── File discovery ─────────────────────────────────────────────────────────────

def resolve_targets(args: list) -> list:
    def jsons_in(folder: str) -> list:
        return sorted(
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.endswith(".json")
            and not f.endswith(".tmp")
            and os.path.isfile(os.path.join(folder, f))
        )

    if not args:
        found = jsons_in(os.getcwd())
        if found:
            return found
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir != os.getcwd():
            return jsons_in(script_dir)
        return []

    targets = []
    for arg in args:
        arg = os.path.expanduser(arg)
        if os.path.isfile(arg) and arg.endswith(".json"):
            targets.append(os.path.abspath(arg))
        elif os.path.isdir(arg):
            for root, _, files in os.walk(arg):
                for fname in sorted(files):
                    if fname.endswith(".json") and not fname.endswith(".tmp"):
                        targets.append(os.path.abspath(os.path.join(root, fname)))
    return targets


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    targets = resolve_targets(sys.argv[1:])

    if not targets:
        print("❌  No JSON files found.\n")
        print("Usage:")
        print("  python3 cloudinary_convert_fixed.py desks.json")
        print("  python3 cloudinary_convert_fixed.py /path/to/desks.json")
        print("  python3 cloudinary_convert_fixed.py /path/to/folder/")
        sys.exit(1)

    print(f"\n☁️   Cloudinary Bulk Image Converter — v5 FINAL")
    print(f"     Cloud  : {CLOUDINARY_CLOUD_NAME}")
    print(f"     Preset : {CLOUDINARY_UPLOAD_PRESET}")
    print(f"     Files  : {len(targets)}")
    print(f"     Images : up to {IMAGE_WORKERS} parallel per product\n")
    for t in targets:
        print(f"    • {t}")

    for i, path in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}]")
        try:
            process_file(path)
        except Exception as e:
            print(f"  ❌  Fatal: {path}: {e}")
            import traceback; traceback.print_exc()

    print(f"\n{'═'*70}")
    print(f"🎉  Done!  {len(targets)} file(s) processed.")
    print(f"{'═'*70}")