"""
HP.com Nigeria Scraper — Fixed Image Gallery Extraction + Fixed Product Loading
================================================================================

PROBLEM #1 (images) — FIXED PREVIOUSLY:
  HP product pages use an Adobe-AEM hero CAROUSEL widget for the image
  gallery (visible prev/next arrows + dot pagination). Lazy-loaded slides
  keep their real image URL in `data-src`, `data-lazy`, `srcset`, or a
  `style="background-image:url(...)"` instead of `src` until clicked to.
  Fixed with a 3-layer extraction strategy (see extract_gallery_images).

PROBLEM #2 (only 9 products captured) — FIXED IN THIS VERSION:
  The category-page "load more" loop was unreliable for three reasons:

  1. `if i > 25 and current == last_count: break` only starts checking for
     a plateau after attempt 25 — but if the "Load More" click silently
     failed (wrong selector / not visible / covered by sticky header),
     the count would plateau immediately and the loop wasted cycles
     instead of failing loudly.
  2. `window.scrollTo(0, document.body.scrollHeight)` scrolls the *window*.
     Many AEM catalog grids use an inner scroll container or an
     IntersectionObserver watching a sentinel element near the bottom of
     the grid — scrolling the window doesn't trigger it.
  3. Fixed `time.sleep()` after clicking doesn't guarantee the new batch
     of products has actually finished loading over the network.

  FIX:
   - Scroll the *last visible product card* into view instead of the
     window — this works whether the grid uses window scroll or an inner
     scroll container, since it forces the actual last-card element to
     become visible in the viewport.
   - Broadened the "load more" button selector (Load More / Show more /
     View more, button or anchor, class-based fallback).
   - Wait on `networkidle` after a click instead of a fixed sleep, so we
     actually wait for the new product batch to arrive.
   - Replaced the `i > 25` gate with a stall counter: break only after
     N consecutive attempts with zero growth, regardless of which
     attempt number that happens to land on. This is both faster when
     the page is genuinely done, and more patient if a single click
     happens to lag.
   - Added a diagnostic dump of clickable "load more"/pagination-looking
     elements at the start of the loop (printed once) so you can quickly
     see the *actual* button text/class the site is using, in case HP
     changes their markup again in the future.
"""

import json
import re
import time
import os
from playwright.sync_api import sync_playwright

CATEGORIES = [
    {"name": "Laptops",       "url": "https://www.hp.com/ng-en/products/laptops/view-all-laptops-and-2-in-1s.html", "folder": "laptops", "json_file": "laptops.json"},
]

MAX_IMAGES = 10  # safety cap per product
STALL_LIMIT = 5  # consecutive no-growth attempts before we conclude "all products loaded"
MAX_LOAD_ATTEMPTS = 120  # hard safety cap so we never loop forever


def load_existing(output_path):
    if os.path.exists(output_path):
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                return {p.get("url"): p for p in json.load(f) if p.get("title")}
        except Exception:
            return {}
    return {}


def save_products(output_path, products_dict):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(list(products_dict.values()), f, indent=2, ensure_ascii=False)


# ── Helpers for image URL quality ──────────────────────────────────────────

def best_from_srcset(srcset: str) -> str:
    """
    srcset looks like: "url1 320w, url2 640w, url3 1024w"
    Return the URL with the largest width descriptor (highest resolution).
    """
    if not srcset:
        return ""
    candidates = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.rsplit(" ", 1)
        url = bits[0].strip()
        width = 0
        if len(bits) == 2 and bits[1].endswith("w"):
            try:
                width = int(bits[1][:-1])
            except ValueError:
                width = 0
        candidates.append((width, url))
    if not candidates:
        return ""
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


def looks_like_product_image(url: str) -> bool:
    """
    Broadened heuristic for 'is this a real product photo' rather than a
    UI icon, logo, or tracking pixel. HP serves product imagery from a
    handful of CDN domains/paths — we match any of them instead of just
    two fixed substrings.
    """
    if not url or not url.startswith("http"):
        return False
    lowered = url.lower()
    bad_markers = ["icon", "logo", "sprite", "placeholder", "1x1", "pixel.gif", "spacer"]
    if any(b in lowered for b in bad_markers):
        return False
    good_markers = [
        "hp.widen.net",        # HP's DAM/CDN for product photography
        "widen.net",
        "product-images",
        "/content/dam/",       # AEM asset path pattern
        "h20195.www2.hp.com",  # HP image CDN alt host
        ".widencdn.net",
    ]
    return any(g in lowered for g in good_markers)


def extract_gallery_images(page) -> list:
    """
    Three-layer extraction strategy. Returns a deduped, ordered list of
    image URLs (best-quality variant chosen where srcset is available).
    """
    found = []
    seen = set()

    def add(url):
        if url and url not in seen and looks_like_product_image(url):
            seen.add(url)
            found.append(url)

    # ── Layer 1: scan all <img> inside any carousel/gallery-ish container,
    #             reading every plausible attribute, not just src ──────────
    try:
        candidates = page.eval_on_selector_all(
            "img",
            """
            els => els.map(e => ({
                src: e.getAttribute('src') || '',
                dataSrc: e.getAttribute('data-src') || '',
                dataLazy: e.getAttribute('data-lazy') || e.getAttribute('data-lazy-src') || '',
                dataOriginal: e.getAttribute('data-original') || '',
                srcset: e.getAttribute('srcset') || e.getAttribute('data-srcset') || ''
            }))
            """
        )
        for c in candidates:
            add(c.get("src", ""))
            add(c.get("dataSrc", ""))
            add(c.get("dataLazy", ""))
            add(c.get("dataOriginal", ""))
            best = best_from_srcset(c.get("srcset", ""))
            if best:
                add(best)
    except Exception as e:
        print(f"     ⚠️  Layer 1 (img scan) error: {e}")

    # ── Layer 2: click through the carousel's "next" control to force
    #             lazy slides to mount, re-scanning after each click ──────
    try:
        # Count dot indicators to know how many slides exist
        dots = page.locator(
            "[class*='carousel'] [class*='dot'], "
            "[class*='Carousel'] button[aria-label*='slide' i], "
            "[role='tablist'] [role='tab']"
        )
        dot_count = dots.count()

        next_btn = page.locator(
            "[class*='carousel'] button[aria-label*='next' i], "
            "button[aria-label*='Next' i], "
            "[class*='Carousel'] [class*='next' i]"
        ).first

        clicks = max(dot_count, 4)  # at least a handful of clicks as fallback
        if next_btn.count() > 0:
            for _ in range(clicks):
                try:
                    if not next_btn.is_visible(timeout=1500):
                        break
                    next_btn.click(timeout=2000)
                    time.sleep(0.6)  # let the slide transition / lazy-load fire

                    # Re-scan after each click
                    more = page.eval_on_selector_all(
                        "img",
                        """
                        els => els.map(e => ({
                            src: e.getAttribute('src') || '',
                            dataSrc: e.getAttribute('data-src') || '',
                            srcset: e.getAttribute('srcset') || ''
                        }))
                        """
                    )
                    for c in more:
                        add(c.get("src", ""))
                        add(c.get("dataSrc", ""))
                        best = best_from_srcset(c.get("srcset", ""))
                        if best:
                            add(best)
                except Exception:
                    continue
    except Exception as e:
        print(f"     ⚠️  Layer 2 (carousel click) error: {e}")

    # ── Layer 3: background-image style fallback (some HP slides render
    #             the photo as a CSS background instead of an <img>) ──────
    try:
        bg_urls = page.eval_on_selector_all(
            "[style*='background-image']",
            """
            els => els.map(e => {
                const m = (e.getAttribute('style')||'').match(/url\\((['"]?)(.*?)\\1\\)/);
                return m ? m[2] : '';
            })
            """
        )
        for u in bg_urls:
            add(u)
    except Exception as e:
        print(f"     ⚠️  Layer 3 (bg-image) error: {e}")

    return found[:MAX_IMAGES]


# ── Helpers for the product-listing "load more" loop ────────────────────────

LOAD_MORE_SELECTOR = (
    'button:has-text("Load More"), button:has-text("Load more"), '
    'button:has-text("Show more"), button:has-text("Show More"), '
    'button:has-text("View more"), button:has-text("View More"), '
    'a:has-text("Load more"), a:has-text("Load More"), '
    '[class*="load-more" i], [class*="loadmore" i], [data-testid*="load-more" i]'
)

# Set to True to click "Load more" manually yourself in the visible browser
# window instead of relying on the script to find/click it automatically.
# This sidesteps any selector/overlay/timing issues entirely — you just
# click as many times as needed in the browser, then tell the script (in
# the terminal) when you're done, and it scrapes whatever is on screen.
MANUAL_LOAD_MORE = True


def debug_dump_clickables(page):
    """
    One-time diagnostic dump of every button/anchor-like element whose
    text or class hints at pagination/load-more. Printed once so you can
    see exactly what selector HP is actually using if this loop ever
    stops matching in the future (site markup changes over time).
    """
    try:
        candidates = page.eval_on_selector_all(
            "button, a[role='button'], [class*='load' i], [class*='pagination' i], [class*='more' i]",
            """
            els => els.slice(0, 40).map(e => ({
                tag: e.tagName,
                text: (e.innerText || '').trim().slice(0, 40),
                cls: (e.className || '').toString().slice(0, 80)
            }))
            """
        )
        interesting = [c for c in candidates if c.get("text")]
        if interesting:
            print("   🔎 Clickable candidates on page (diagnostic):")
            for c in interesting[:15]:
                print(f"      <{c['tag']}> text='{c['text']}' class='{c['cls']}'")
    except Exception as e:
        print(f"   ⚠️  Diagnostic dump failed: {e}")


def describe_load_more(page):
    """
    Diagnostic snapshot of the 'Load more' button's actual DOM state:
    does it exist at all, is it disabled, is it visible, and what does
    its outerHTML look like. This removes the guesswork of "did it
    disappear because we're done, or because we're missing it".
    """
    try:
        info = page.eval_on_selector_all(
            LOAD_MORE_SELECTOR,
            """
            els => els.map(e => ({
                exists: true,
                disabled: e.disabled === true || e.getAttribute('disabled') !== null ||
                          e.getAttribute('aria-disabled') === 'true',
                visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length),
                outerHTML: e.outerHTML.slice(0, 300)
            }))
            """
        )
        return info
    except Exception as e:
        return [{"error": str(e)}]


def load_all_products_manual(page):
    """
    Manual mode: the browser window is already open (headless=False).
    You click "Load more" yourself, as many times as you want, at your
    own pace. The script polls the current unique-product count every
    2 seconds and prints it so you can watch it climb in real time.
    When you're satisfied all products are loaded, switch back to the
    terminal and press Enter to continue.

    This completely sidesteps selector mismatches, overlay/cookie-banner
    interception, disabled-state timing, etc. — the kind of thing that
    caused the auto-click version to stall early.
    """
    def unique_count():
        links = page.eval_on_selector_all(
            'a[href*="/product-details/"]',
            'els => [...new Set(els.map(el => el.href))]'
        )
        return len(links)

    print("\n" + "=" * 70)
    print("   👉 MANUAL MODE: a browser window should be open on the page.")
    print("      Click 'Load more' yourself as many times as you like.")
    print("      This terminal will show the live unique-product count")
    print("      updating every couple seconds as you click.")
    print("      When you're happy all products are loaded, come back")
    print("      here and press ENTER to continue scraping.")
    print("=" * 70 + "\n")

    import threading

    stop_flag = {"stop": False}

    def poll_loop():
        last = -1
        while not stop_flag["stop"]:
            try:
                current = unique_count()
                if current != last:
                    print(f"   📈 Unique products on page: {current}")
                    last = current
            except Exception:
                pass
            time.sleep(2)

    poller = threading.Thread(target=poll_loop, daemon=True)
    poller.start()

    input("\n   ⏸  Press ENTER here once you've finished clicking 'Load more'...\n")
    stop_flag["stop"] = True
    poller.join(timeout=3)

    final = unique_count()
    print(f"   ✅ Proceeding with {final} unique products found on page.\n")
    return final


def load_all_products(page):
    """
    Robust product-loading loop:
      - scrolls the LAST product card into view (works for both window
        scroll and inner-scroll-container grids)
      - clicks a broadened "load more" selector if present, then waits
        for network idle instead of a fixed sleep
      - uses a stall counter (N consecutive no-growth attempts) rather
        than a fixed "only start checking after attempt 25" gate
      - on the FIRST stall, dumps the real DOM state of the load-more
        button (exists? disabled? visible? outerHTML) so you can verify
        with your own eyes whether the catalog is truly exhausted or the
        button is just being missed
    """
    print("   📜 Loading all products...")
    debug_dump_clickables(page)

    last_count = 0
    stall = 0
    dumped_on_stall = False

    for i in range(MAX_LOAD_ATTEMPTS):
        # Scroll the actual last product card into view (handles inner
        # scroll containers and IntersectionObserver-based lazy loading)
        cards = page.query_selector_all('a[href*="/product-details/"]')
        if cards:
            try:
                cards[-1].scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
        else:
            # No cards yet at all — fall back to window scroll
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        page.wait_for_timeout(1200)

        # Also explicitly scroll the load-more button itself into view
        # before checking visibility — some sites lazily mount it only
        # once it's near the viewport.
        try:
            lm = page.locator(LOAD_MORE_SELECTOR).first
            if lm.count() > 0:
                lm.scroll_into_view_if_needed(timeout=2000)
                page.wait_for_timeout(300)
        except Exception:
            pass

        clicked = False
        try:
            load_more = page.locator(LOAD_MORE_SELECTOR)
            if load_more.count() > 0 and load_more.first.is_visible(timeout=1500):
                load_more.first.click(timeout=3000)
                clicked = True
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    time.sleep(2)  # fallback wait if networkidle never settles
        except Exception:
            pass

        current = len(page.query_selector_all('a[href*="/product-details/"]'))
        print(f"   Attempt {i+1:2d} | Products: {current} | clicked_load_more={clicked}")

        if current == last_count:
            stall += 1
        else:
            stall = 0
            dumped_on_stall = False  # reset so we dump again on the *next* stall streak
        last_count = current

        # On the very first attempt of a stall streak, print the real DOM
        # state of the load-more button so it's obvious whether it's gone
        # (catalog exhausted) or just temporarily not matching/visible.
        if stall == 1 and not dumped_on_stall:
            info = describe_load_more(page)
            if info:
                print(f"   🔎 Load-more button DOM state on first stall: {info}")
            else:
                print("   🔎 Load-more button DOM state on first stall: NOT FOUND (0 matches) — button removed from DOM.")
            dumped_on_stall = True

        if stall >= STALL_LIMIT:
            print(f"   ✅ No growth for {STALL_LIMIT} consecutive attempts — assuming all products loaded.")
            break
    else:
        print(f"   ⚠️  Hit MAX_LOAD_ATTEMPTS ({MAX_LOAD_ATTEMPTS}) without a clean stall — proceeding anyway.")

    return last_count



# ── Main scrape loop ─────────────────────────────────────────────────────

def scrape_category(cat):
    print(f"\n=== Scraping {cat['name']} ===")
    output_path = f"output/{cat['folder']}/{cat['json_file']}"
    products_dict = load_existing(output_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        print(f"   Opening {cat['name']} page...")
        page.goto(cat["url"], wait_until="domcontentloaded", timeout=180000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        if MANUAL_LOAD_MORE:
            final_count = load_all_products_manual(page)
        else:
            final_count = load_all_products(page)

        links = page.eval_on_selector_all(
            'a[href*="/product-details/"]',
            'els => [...new Set(els.map(el => el.href))]'
        )
        pending_links = [link for link in links if link not in products_dict]
        print(f"   ✅ Found {len(links)} products (grid reported {final_count}) | {len(pending_links)} new")

        for idx, link in enumerate(pending_links):
            try:
                print(f"   [{idx+1}/{len(pending_links)}] Scraping...")
                page.goto(link, wait_until="domcontentloaded", timeout=60000)
                time.sleep(2)   # let hero carousel mount before we touch it

                title = page.locator('h1').first.inner_text().strip()
                sku_match = re.search(r'\(([A-Z0-9-]+)\)', title)

                product = {
                    "title": title,
                    "subtitle": "",
                    "brand": "HP",
                    "sku": sku_match.group(1) if sku_match else "",
                    "product_id": link.split('/')[-1],
                    "url": link,
                    "category": cat["name"],
                    "overview": "",
                    "price": "N/A",
                    "currency": "NGN",
                    "key_features": [],
                    "specifications": [],
                    "images": [],
                    "warranty": "N/A",
                    "is_active": True
                }

                # ── Images: robust 3-layer extraction (see function docstring) ──
                product["images"] = extract_gallery_images(page)
                print(f"     → Found {len(product['images'])} images")

                # Overview
                overview_el = page.locator('.c-product-details__desc, .product-description, p').first
                if overview_el.count() > 0:
                    product["overview"] = overview_el.inner_text().strip()[:1500]

                # Click View All Tech Specs
                try:
                    specs_btn = page.locator(
                        'a:has-text("View all tech specs"), button:has-text("View all tech specs"), '
                        '.view-all-specs, [href*="product-specifications"]'
                    )
                    if specs_btn.count() > 0 and specs_btn.is_visible(timeout=8000):
                        specs_btn.click()
                        print("     → Clicked View All Tech Specs")
                        time.sleep(4)
                except Exception:
                    pass

                # Specifications
                specs = []
                spec_rows = page.locator(
                    'tr.c-product-all-details-table__tr, tr, .spec-row, .technical-specs tr, table tr'
                ).all()
                for row in spec_rows:
                    cells = row.locator('td, th, dt').all()
                    if len(cells) >= 2:
                        key = cells[0].inner_text().strip()
                        value = cells[1].inner_text().strip()
                        if key and value and len(key) < 100:
                            specs.append({"key": key, "value": value})
                    else:
                        text = row.inner_text().strip()
                        if ':' in text and len(text) < 300:
                            try:
                                k, v = [x.strip() for x in text.split(':', 1)]
                                if k and v:
                                    specs.append({"key": k, "value": v})
                            except Exception:
                                continue

                if specs:
                    product["specifications"] = [{"title": "Technical Specifications", "specs": specs[:50]}]
                    print(f"     → Extracted {len(specs)} specs")

                # Key Features
                features = page.locator('.feature-item, .benefits li, .key-features li, .c-feature').all_inner_texts()
                product["key_features"] = [f.strip() for f in features if len(f.strip()) > 20]

                products_dict[link] = product
                save_products(output_path, products_dict)

                print(f"     ✅ Saved: {title[:70]}  |  {len(product['images'])} images  |  {len(specs)} specs")
                time.sleep(2)

            except Exception as e:
                print(f"     ❌ Error: {e}")
                continue

        browser.close()

    print(f"✅ Finished {cat['name']}\n")


if __name__ == "__main__":
    for cat in CATEGORIES:
        scrape_category(cat)
    print("🎉 FULL HP SCRAPING COMPLETED!")