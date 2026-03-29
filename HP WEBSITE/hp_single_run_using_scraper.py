import json

import hp_scraper


URL = "https://www.hp.com/us-en/shop/pdp/hp-zbook-fury-g1i-16-inch-mobile-workstation-pc-wolf-pro-security-edition-p-c3fk4ua-aba-1"


def main():
    print("Scraping single product with hp_scraper.py")
    print(f"URL: {URL}")

    product = hp_scraper.scrape_one(URL)
    if not product:
        raise SystemExit("Failed to scrape product (got None). Check scrape_log.txt")

    total_specs = sum(len(g.get("specs", [])) for g in product.get("specifications", []))
    print(f"title: {product.get('title')}")
    print(f"spec groups: {len(product.get('specifications', []))}, total specs: {total_specs}")

    out_path = "hp_scraper_output/single_product_test.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(product, f, indent=2, ensure_ascii=False)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()

