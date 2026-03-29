"""
test_deli_scraperapi.py
────────────────────────
Tests if ScraperAPI can fetch deliworld.com product pages with JS rendering.
"""
import requests
import re
from bs4 import BeautifulSoup

SCRAPER_API_KEY = "3fd4cde19617248ed9384720a92cca28"  # ← paste your key
SCRAPER_API_URL = "https://api.scraperapi.com/"

TEST_URLS = [
    ("Category page - Highlighter",  "https://www.deliworld.com/product/fluorescent-marker-pen/"),
    ("Category page - Calculator",   "https://www.deliworld.com/product/calculator/"),
    ("Product page - Highlighter",   "https://www.deliworld.com/product/deli-eu350-highlighter.html"),
]

print("🔍  Testing ScraperAPI on deliworld.com...\n")

for label, url in TEST_URLS:
    print(f"Testing: {label}")
    print(f"URL: {url}")

    resp = requests.get(SCRAPER_API_URL, params={
        "api_key":      SCRAPER_API_KEY,
        "url":          url,
        "render":       "true",
        "country_code": "us",
        "wait":         "5000",
    }, timeout=120)

    print(f"  Status: {resp.status_code} | Size: {len(resp.text):,} bytes")

    if resp.status_code == 200:
        soup = BeautifulSoup(resp.text, "lxml")

        # Check for product links ending in .html
        product_links = [
            a["href"] for a in soup.find_all("a", href=True)
            if a["href"].endswith(".html") and "/product/" in a["href"]
        ]
        print(f"  Product .html links found: {len(product_links)}")
        for link in product_links[:5]:
            print(f"    → {link.split('/')[-1]}")

        # Check for product images
        product_imgs = [
            img["src"] for img in soup.find_all("img", src=True)
            if "/uploads/image/" in img.get("src", "")
            and "logo" not in img.get("src", "")
        ]
        print(f"  Product images found: {len(product_imgs)}")
        for img in product_imgs[:3]:
            print(f"    → {img.split('/')[-1]}")

        # Check for SAP numbers
        sap_matches = re.findall(r'\b1[0-9]{8}\b', resp.text)
        print(f"  SAP numbers found: {len(sap_matches)} → {sap_matches[:3]}")

        # Check for key product text
        has_color   = "Color:" in resp.text
        has_spec    = "Specification:" in resp.text
        has_feature = any(x in resp.text for x in ["smooth writing", "easy to", "designed for"])
        print(f"  Has Color field:   {'✅' if has_color else '❌'}")
        print(f"  Has Spec field:    {'✅' if has_spec else '❌'}")
        print(f"  Has feature text:  {'✅' if has_feature else '❌'}")

    print()

print("✅  Test complete — share the output!")