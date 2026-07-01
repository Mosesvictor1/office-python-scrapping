import json
import re
import time
from playwright.sync_api import sync_playwright

def scrape_hp_ink_toner():
    base_url = "https://www.hp.com/ng-en/products/ink-toner/view-all-ink-and-toner.html"
    products = []
    CATEGORIES = [
    {"name": "Laptops",       "url": "https://www.hp.com/ng-en/products/laptops/view-all-laptops-and-2-in-1s.html", "folder": "laptops", "json_file": "laptops.json"},
    # {"name": "Desktops",      "url": "https://www.hp.com/ng-en/products/desktops/view-all-desktop-computers.html", "folder": "desktops", "json_file": "desktops.json"},
    # {"name": "Workstations",  "url": "https://www.hp.com/ng-en/products/workstations/view-all-workstation-computers.html", "folder": "workstations", "json_file": "workstations.json"},
    # {"name": "Monitors",      "url": "https://www.hp.com/ng-en/products/monitors/view-all-monitors.html", "folder": "monitors", "json_file": "monitors.json"},
    # {"name": "Printers",      "url": "https://www.hp.com/ng-en/products/printers/view-all-printers.html", "folder": "printers", "json_file": "printers.json"},
    # {"name": "Scanners",      "url": "https://www.hp.com/ng-en/products/scanners/view-all-scanners.html", "folder": "scanners", "json_file": "scanners.json"},
    # {"name": "Ink and Toner", "url": "https://www.hp.com/ng-en/products/ink-toner/view-all-ink-and-toner.html", "folder": "ink-toner", "json_file": "inks_toner.json"},
]
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        
        print("Opening HP page...")
        page.goto(base_url, wait_until="networkidle", timeout=60000)
        
        page.wait_for_selector('a[href*="/product-details/"]', timeout=30000)
        
        print("Scrolling to load all products...")
        for _ in range(25):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
        
        links = page.eval_on_selector_all('a[href*="/product-details/"]', 
                                        'els => [...new Set(els.map(el => el.href))]')
        
        print(f"Found {len(links)} products. Scraping...")
        
        for idx, link in enumerate(links):
            try:
                print(f"[{idx+1}/{len(links)}] {link}")
                page.goto(link, wait_until="networkidle", timeout=30000)
                
                product = {
                    "title": page.locator('h1').inner_text().strip() if page.locator('h1').count() > 0 else "",
                    "subtitle": "",
                    "brand": "HP",
                    "sku": "",
                    "product_id": link.split("/")[-1],
                    "url": link,
                    "category": "Ink and Toner",
                    "overview": "",
                    "price": "N/A",
                    "currency": "NGN",
                    "key_features": [],
                    "specifications": [],
                    "images": [],
                    "warranty": "N/A",
                    "is_active": True,
                    "filters": {
                        "types": [{"name": "Ink and Toner", "type": "Ink and Toner", "sub_category": "Printers, scanners and ink"}],
                        "brands": [{"name": "HP", "type": "Ink and Toner", "sub_category": "Printers, scanners and ink"}]
                    }
                }
                
                product["overview"] = product["title"]
                
                # SKU
                title = product["title"]
                sku_match = re.search(r'\(([A-Z0-9]{4,10})\)', title)
                if sku_match:
                    product["sku"] = sku_match.group(1)
                
                # Key Features
                features = page.locator('ul li, .feature, .benefits li').all_inner_texts()
                product["key_features"] = [f.strip() for f in features if len(f.strip()) > 20]
                
                # Images
                imgs = page.eval_on_selector_all('img[src*="hp.widen.net"], img[src*="product-images"]', 
                                               'imgs => imgs.map(i => i.src)')
                product["images"] = list(dict.fromkeys([i for i in imgs if i]))
                
                # Specifications (basic extraction)
                specs = []
                rows = page.locator('tr, .spec-row, dt, .technical-specs div').all()
                for row in rows:
                    text = row.inner_text().strip()
                    if ':' in text:
                        k, v = [x.strip() for x in text.split(':', 1)]
                        specs.append({"key": k, "value": v})
                
                if specs:
                    product["specifications"] = [{"title": "Technical Specifications", "specs": specs[:25]}]
                
                products.append(product)
                time.sleep(1)
                
            except Exception as e:
                print(f"Error: {e}")
                continue
        
        browser.close()
    
    with open('output/inks_toner.json', 'w', encoding='utf-8') as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    
    print(f"Done! Saved {len(products)} products.")

if __name__ == "__main__":
    scrape_hp_ink_toner()