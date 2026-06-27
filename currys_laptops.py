"""
Currys.co.uk Laptop Scraper — Fixed based on actual HTML source
Key findings from page source:
  1. Product links: href="/products/..." (plural, with full slug + ID)
  2. Product cards: <div class="product" data-pid="...">
  3. Product links on cards: <a class="link ... pdpLink" href="/products/...">
  4. Price: <span class="value" content="949.00">
  5. Title: <h2 class="pdp-grid-product-name"> on listing, <h1> on PDP
  6. Key features on listing: <ul class="list-of-features"> <li>
  7. Pagination: default 20 items shown, page shows 84 total for Apple
  8. Images: src="https://media.currys.biz/i/currysprod/..."
  9. The product datalayer JSON contains ALL product data inline in the listing!

PDP-specific selectors (verified from page source):
  - Overview:       #collapseOne .card-body p  (first <p> inside Product information accordion)
  - Specifications: #collapseTwo .tech-specification-body .row  (th col-4 + td col-8 pairs)
  - Main images:    #pdpCarousel-{pid} img  (only the primary carousel, avoids recommended-product images)
"""

import json
import time
import os
import re
import random
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CATEGORIES = [
    {
        "name": "MacBook",
        "url": "https://www.currys.co.uk/computing/laptops/laptops/apple",
        "json_file": "macbooks.json",
    },
    {
        "name": "Chromebooks",
        "url": "https://www.currys.co.uk/computing/laptops/laptops/chromebooks",
        "json_file": "chromebooks.json",
    },
    {
        "name": "Microsoft Surface",
        "url": "https://www.currys.co.uk/computing/laptops/laptops/microsoft/surface",
        "json_file": "surface.json",
    },
    {
        "name": "2-in-1 Laptops",
        "url": "https://www.currys.co.uk/computing/laptops/laptops/2-in-1-laptops",
        "json_file": "2-in-1-laptops.json",
    },
    {
        "name": "AI Laptops",
        "url": "https://www.currys.co.uk/computing/laptops/laptops/ai-laptops",
        "json_file": "ai-laptops.json",
    },
    {
        "name": "Acer Windows Laptops",
        "url": "https://www.currys.co.uk/computing/laptops/laptops/acer/windows-laptops",
        "json_file": "acer-windows-laptops.json",
    },
    {
        "name": "Asus Windows Laptops",
        "url": "https://www.currys.co.uk/computing/laptops/laptops/asus/windows-laptops",
        "json_file": "asus-windows-laptops.json",
    },
    # oppo phone
    {
        "name": "Oppo",
        "url": "https://www.currys.co.uk/phones/mobile-phones/mobile-phones/oppo",
        "json_file": "oppo-phones.json",
    }

]

OUTPUT_DIR = "output/currys"
BASE_URL = "https://www.currys.co.uk"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def load_existing(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {p["url"]: p for p in data if "url" in p}
        except Exception:
            pass
    return {}


def save_products(path, products):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(products.values()), f, indent=2, ensure_ascii=False)


def human_sleep(lo=1.5, hi=3.5):
    time.sleep(random.uniform(lo, hi))


def make_browser_context(playwright):
    browser = playwright.chromium.launch(
        headless=False,
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
        timezone_id="Europe/London",
        extra_http_headers={
            "Accept-Language": "en-GB,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        },
    )
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, ctx


def dismiss_cookie_banner(page):
    try:
        btn = page.locator(
            "button:has-text('Accept all'), button:has-text('Accept'), #onetrust-accept-btn-handler"
        ).first
        if btn.is_visible(timeout=5000):
            btn.click()
            human_sleep(1, 2)
            print("   🍪 Cookie banner dismissed")
    except Exception:
        pass


def extract_product_links_from_page(page):
    """
    Extract product links from the current page.
    Pattern: href="/products/<slug>-<10-digit-pid>.html"
    """
    links = set()
    try:
        hrefs = page.eval_on_selector_all(
            'a[href^="/products/"]',
            'els => [...new Set(els.map(e => e.getAttribute("href")).filter(h => h && h.includes(".html")))]'
        )
        for h in hrefs:
            if h.startswith("/products/"):
                links.add(BASE_URL + h)
    except Exception as e:
        print(f"   ⚠️  Link extraction error: {e}")
    return links


def extract_from_datalayer(page, category_name):
    """
    Currys embeds ALL product data in data-productdatalayer JSON attributes.
    Extract title, price, brand, and more directly from the listing page.
    """
    products = {}
    try:
        elements = page.query_selector_all('[data-productdatalayer]')
        for el in elements:
            try:
                raw = el.get_attribute('data-productdatalayer')
                if not raw:
                    continue
                data_list = json.loads(raw)
                if not data_list:
                    continue
                item = data_list[0]

                pid = item.get("id", "")
                name = item.get("name", "Unknown")
                brand = item.get("brand", category_name)
                ean = item.get("ean", [])

                # Price from payment array
                price = ""
                for p in item.get("payment", []):
                    if p.get("frequency") == "one off":
                        price = f"£{p.get('amount', '')}"
                        break

                # Key features from listing page
                key_features = []
                try:
                    feat_els = el.query_selector_all("li .curry-sansreg-headline, li .curry-sansreg-font")
                    for f in feat_els:
                        txt = f.inner_text().strip()
                        if txt:
                            key_features.append(txt)
                except Exception:
                    pass

                # Image from tile
                img_src = ""
                try:
                    img_el = el.query_selector("img.tile-image")
                    if img_el:
                        img_src = img_el.get_attribute("src") or ""
                except Exception:
                    pass

                # PDP URL from pdpLink
                pdp_url = ""
                try:
                    a_el = el.query_selector("a.pdpLink")
                    if a_el:
                        href = a_el.get_attribute("href") or ""
                        if href.startswith("/products/"):
                            pdp_url = BASE_URL + href
                        elif href.startswith("http"):
                            pdp_url = href
                except Exception:
                    pass

                if not pdp_url or not pid:
                    continue

                # Offers
                offers = []
                for p in item.get("payment", []):
                    for o in p.get("offer", []):
                        n = o.get("name", "")
                        if n and n not in offers:
                            offers.append(n)

                products[pdp_url] = {
                    "pid": pid,
                    "title": name,
                    "brand": brand,
                    "url": pdp_url,
                    "category": category_name,
                    "price": price,
                    "currency": "GBP",
                    "ean": ean,
                    "overview": "",
                    "key_features": key_features,
                    "offers": offers[:5],
                    "specifications": [],
                    "images": [img_src] if img_src else [],
                    "is_active": True,
                    "scraped_from": "listing",
                }
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️  Datalayer extraction error: {e}")
    return products


def load_all_products_on_page(page, category_url):
    """
    Load all products by changing the page size to maximum via the sz parameter.
    """
    all_products = {}

    page_sizes = [96, 50, 30, 20]

    for sz in page_sizes:
        url = f"{category_url}?sz={sz}&start=0"
        print(f"   📄 Loading with sz={sz}: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            human_sleep(3, 5)
            dismiss_cookie_banner(page)

            try:
                page.wait_for_selector('[data-productdatalayer]', timeout=20_000)
                count = len(page.query_selector_all('[data-productdatalayer]'))
                print(f"   ✅ Got {count} products with sz={sz}")
                break
            except PWTimeout:
                print(f"   ⚠️  No products with sz={sz}, trying smaller...")
                continue
        except Exception as e:
            print(f"   ❌ Error: {e}")
            continue

    products = extract_from_datalayer(page, "")
    print(f"   📦 Extracted {len(products)} products from page 1")

    total = 0
    try:
        count_el = page.query_selector('.page-result-count')
        if count_el:
            text = count_el.inner_text()
            nums = re.findall(r'\d+', text)
            if nums:
                total = int(nums[0])
                print(f"   📊 Total products reported: {total}")
    except Exception:
        pass

    all_products.update(products)

    if total > len(all_products) and len(all_products) > 0:
        loaded = len(all_products)
        sz_used = len(all_products)
        page_num = 1
        while loaded < total:
            start = loaded
            paginated_url = f"{category_url}?sz={sz_used}&start={start}"
            print(f"   📄 Loading page {page_num + 1}: start={start}")
            try:
                page.goto(paginated_url, wait_until="domcontentloaded", timeout=60_000)
                human_sleep(2, 4)
                try:
                    page.wait_for_selector('[data-productdatalayer]', timeout=15_000)
                except PWTimeout:
                    print("   ✅ No more pages")
                    break

                new_products = extract_from_datalayer(page, "")
                if not new_products:
                    print("   ✅ No more products found")
                    break

                new_count = len([p for p in new_products if p not in all_products])
                all_products.update(new_products)
                loaded = len(all_products)
                page_num += 1
                print(f"   📦 Got {new_count} new products, total: {loaded}")

                if new_count == 0:
                    break
            except Exception as e:
                print(f"   ❌ Pagination error: {e}")
                break

    return all_products


def clean_spec_value(raw: str) -> str:
    """
    Currys renders multi-item spec values with leading "- " on each line
    (from <br> tags in the HTML).  Convert them to a clean comma-separated
    string so the value reads naturally.

    Examples:
      "- Apple M4 chip\n- 10-core CPU\n- 10-core GPU"
        → "Apple M4 chip, 10-core CPU, 10-core GPU"

      "WiFi 6E 802.11ax"  (no bullets)
        → "WiFi 6E 802.11ax"
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return ""

    # If every non-empty line starts with "- ", strip the bullet and join
    if all(ln.startswith("- ") for ln in lines):
        parts = [ln[2:].strip() for ln in lines]
        return ", ".join(parts)

    # Mixed or plain — just collapse whitespace
    return " ".join(lines)


def enrich_from_pdp(page, url, product):
    """
    Visit the product detail page to get description, full specs, and main product images.

    Selectors verified from live Currys PDP HTML source:
      Title:   h1.product-name
      Price:   span.value[content]   (inside .price-info)
      Overview: #collapseOne .card-body p   (Product information accordion, first <p>)
      Key features: .pdp-item-features .item-title
      Specs:   #collapseTwo .tech-specification-body (th col-4 + td col-8 pairs)
      Images:  #pdpCarousel-{pid} .carouselitem img  (main slider ONLY, no recommended products)
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        human_sleep(1.5, 3)

        # ── Title ──────────────────────────────────────────────────────────────
        try:
            h1 = page.locator("h1.product-name").first
            if h1.count() > 0 and h1.is_visible(timeout=5000):
                product["title"] = h1.inner_text().strip()
        except Exception:
            pass

        # ── Price ──────────────────────────────────────────────────────────────
        try:
            # The <span class="value" content="949.00"> pattern
            price_el = page.locator('.price-info span.value[content]').first
            if price_el.count() > 0:
                val = price_el.get_attribute("content") or ""
                if val:
                    product["price"] = f"£{val.strip()}"
        except Exception:
            pass

        # ── Overview / Product Information ────────────────────────────────────
        # Source: <div id="collapseOne" ...><div class="card-body"><p>...</p>
        # We grab the full innerHTML of the first <p> and strip tags for clean text.
        try:
            # Make sure the accordion is expanded (it has class "show" by default on PDP)
            overview_text = ""
            paras = page.query_selector_all("#collapseOne .card-body p")
            for p_el in paras:
                txt = p_el.inner_text().strip()
                if txt and len(txt) > 50:          # skip tiny fragments
                    overview_text = txt
                    break
            if overview_text:
                product["overview"] = overview_text[:3000]
        except Exception:
            pass

        # ── Key Features (PDP icon bar) ────────────────────────────────────────
        # Source: <div class="pdp-item-features"><div class="item"><span class="item-title">macOS</span>
        try:
            features = []
            feat_els = page.query_selector_all(".pdp-item-features .item-title")
            for fi in feat_els:
                t = fi.inner_text().strip()
                if t:
                    features.append(t)
            if features:
                product["key_features"] = features
        except Exception:
            pass

        # ── Specifications ─────────────────────────────────────────────────────
        # Source: #collapseTwo .tech-specification-body .row
        #   <div class="tech-specification-th col-6 col-lg-4">RAM</div>
        #   <div class="tech-specification-td col-6 col-lg-8">16 GB unified memory</div>
        # Section headers: <h3 class="tech-specification-caption">OVERVIEW</h3>
        #
        # Output structure:
        # [
        #   { "title": "overview", "specs": [{"key": "RAM", "value": "16 GB"}, ...] },
        #   { "title": "screen",   "specs": [...] },
        # ]
        try:
            spec_container = page.query_selector("#collapseTwo")
            if not spec_container:
                # Accordion not expanded yet — click to open
                try:
                    toggle = page.locator('a[href="#collapseTwo"]').first
                    if toggle.count() > 0:
                        toggle.click()
                        page.wait_for_selector("#collapseTwo.show", timeout=5000)
                        spec_container = page.query_selector("#collapseTwo")
                except Exception:
                    pass

            if spec_container:
                specification_groups = []
                current_group = None  # {"title": str, "specs": [...]}

                tables = spec_container.query_selector_all(".tech-specification-table")
                for table in tables:
                    caption_el = table.query_selector(".tech-specification-caption")
                    if caption_el:
                        # Start a new group for this section
                        section_title = caption_el.inner_text().strip().lower()
                        current_group = {"title": section_title, "specs": []}
                        specification_groups.append(current_group)
                    elif current_group is None:
                        # Rows before the first caption — put them in a generic group
                        current_group = {"title": "general", "specs": []}
                        specification_groups.append(current_group)

                    rows = table.query_selector_all(".tech-specification-body")
                    for row in rows:
                        th_el = row.query_selector(".tech-specification-th")
                        td_el = row.query_selector(".tech-specification-td")
                        if th_el and td_el:
                            key = th_el.inner_text().strip()
                            # Clean up the value: collapse bullet-style line breaks
                            # (the HTML uses <br> between list items, which inner_text
                            #  renders as newlines starting with "- ")
                            raw_val = td_el.inner_text().strip()
                            value = clean_spec_value(raw_val)
                            if key and value:
                                current_group["specs"].append({"key": key, "value": value})

                # Drop any groups that ended up empty
                specification_groups = [g for g in specification_groups if g["specs"]]

                if specification_groups:
                    product["specifications"] = specification_groups

        except Exception as e:
            print(f"     ⚠️  Specs extraction error: {e}")

        # ── Main Product Images ────────────────────────────────────────────────
        # Source: <div id="pdpCarousel-{pid}" ...>
        #   <div class="carouselitem"><a><img src="https://media.currys.biz/i/currysprod/{pid}?$l-large$...">
        # We target ONLY the primary carousel to avoid pulling in recommended-product images,
        # mini-builder thumbnails, swatch images, etc.
        try:
            pid = product.get("pid", "")
            carousel_id = f"#pdpCarousel-{pid}"
            carousel = page.query_selector(carousel_id)

            images = []
            if carousel:
                img_els = carousel.query_selector_all(".carouselitem img")
                for img in img_els:
                    src = img.get_attribute("src") or ""
                    # Only keep the large-format CDN images for this product
                    if (
                        src
                        and "media.currys.biz" in src
                        and "$l-large$" in src
                        and "currysprod" in src
                        # exclude swatches / thumbnails that may sneak in
                        and "$t-thumbnail$" not in src
                        and "$swatch$" not in src
                        and "$s-swatch$" not in src
                    ):
                        images.append(src)

            # Fallback: derive image URLs from the JSON-LD schema on the page,
            # which lists exactly the gallery images for this product.
            if not images:
                try:
                    schemas = page.query_selector_all('script[type="application/ld+json"]')
                    for s in schemas:
                        raw = s.inner_text()
                        if '"@type":"Product"' in raw:
                            data = json.loads(raw)
                            imgs = data.get("image", [])
                            if isinstance(imgs, list):
                                images = [i for i in imgs if "currysprod" in i]
                            elif isinstance(imgs, str):
                                images = [imgs]
                            break
                except Exception:
                    pass

            if images:
                product["images"] = images[:12]

        except Exception as e:
            print(f"     ⚠️  Image extraction error: {e}")

        product["scraped_from"] = "pdp"

    except Exception as e:
        print(f"     ⚠️  PDP error for {url}: {e}")

    return product


def scrape_category(category):
    print(f"\n{'='*60}")
    print(f"  🔍 Category: {category['name']}")
    print(f"{'='*60}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, category["json_file"])
    products_dict = load_existing(output_path)
    print(f"   ♻️  Loaded {len(products_dict)} existing products")

    with sync_playwright() as pw:
        browser, ctx = make_browser_context(pw)
        page = ctx.new_page()

        try:
            # ── Phase 1: listing pages ─────────────────────────────────────────
            print(f"\n   📋 Phase 1: Extracting from listing pages...")
            listing_products = load_all_products_on_page(page, category["url"])

            for url, prod in listing_products.items():
                prod["category"] = category["name"]
                if not prod["brand"] or prod["brand"] == "":
                    prod["brand"] = category["name"]

            print(f"   ✅ Found {len(listing_products)} products on listing pages")

            # Merge — don't overwrite already-PDP-scraped entries
            already_have = {
                url: prod for url, prod in products_dict.items()
                if prod.get("scraped_from") == "pdp"
            }
            products_dict.update(listing_products)
            products_dict.update(already_have)
            save_products(output_path, products_dict)

            new_from_listing = len(listing_products) - len(already_have)
            print(f"   📊 {max(new_from_listing, 0)} new | {len(already_have)} already PDP-scraped")

            # ── Phase 2: PDP enrichment ────────────────────────────────────────
            need_pdp = [
                url for url, prod in products_dict.items()
                if prod.get("scraped_from") != "pdp"
            ]
            print(f"\n   📋 Phase 2: Enriching {len(need_pdp)} products from PDPs...")

            for idx, url in enumerate(need_pdp, 1):
                slug = url.split("/")[-1][:70]
                print(f"   [{idx:>3}/{len(need_pdp)}] {slug}")

                product = products_dict.get(url, {
                    "title": "Unknown",
                    "brand": category["name"],
                    "url": url,
                    "category": category["name"],
                    "price": "",
                    "currency": "GBP",
                    "overview": "",
                    "key_features": [],
                    "specifications": [],
                    "images": [],
                    "is_active": True,
                })

                product = enrich_from_pdp(page, url, product)
                products_dict[url] = product
                save_products(output_path, products_dict)

                specs = product.get("specifications", [])
                spec_count = sum(len(g.get("specs", [])) for g in specs) if specs else 0
                print(
                    f"         ✅ {product['title'][:55]}  |  {product['price']}"
                    f"  |  {len(specs)} groups / {spec_count} specs"
                    f"  |  {len(product.get('images', []))} imgs"
                )
                human_sleep(2, 4)

        except Exception as e:
            print(f"   ❌ Category error: {e}")
        finally:
            browser.close()

    total = len(products_dict)
    print(f"\n   ✅ {category['name']} done — {total} products → {output_path}")


if __name__ == "__main__":
    print("🛒 Currys Laptop Scraper — starting\n")
    for cat in CATEGORIES:
        scrape_category(cat)
    print("\n🎉 All categories completed!")