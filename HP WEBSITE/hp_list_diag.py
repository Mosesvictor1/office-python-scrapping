import re
import requests


SCRAPER_API_KEY = "74516ff476f866b13129e249ca1fe471"
SCRAPER_API_URL = "http://api.scraperapi.com"


LISTING_URL = "https://www.hp.com/us-en/shop/vwa/laptops/segm=Home"


def fetch_listing(url: str) -> str:
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "render": "false",
        "country_code": "us",
        "keep_headers": "true",
    }
    r = requests.get(SCRAPER_API_URL, params=params, timeout=90)
    r.raise_for_status()
    return r.text


def main():
    html = fetch_listing(LISTING_URL)
    print("html_len:", len(html))

    vals = sorted(set(re.findall(r'data-page="(\d+)"', html)))
    print("data-page values (unique):", vals[:30], "count:", len(vals))

    # Find if any page index is referenced as 2+
    more = [v for v in vals if int(v) > 1]
    print("data-page values > 1:", more[:30], "count:", len(more))

    # also detect any next-page link candidates
    # (not perfect, just quick heuristic)
    next_candidates = re.findall(r'href="([^"]*(?:page|p)=\d+[^"]*)"', html)
    print("href with page/p digit candidates sample:", next_candidates[:20])

    # Extract facet URLs (these usually create new listing slices).
    segm_vals = sorted(set(re.findall(r'segm=([^&"#/]+)', html)))
    form_vals = sorted(set(re.findall(r'form=([^&"#/]+)', html)))
    # category-like filters may exist as well, but we keep it minimal for now.
    print("segm facet values (sample):", segm_vals[:30], "count:", len(segm_vals))
    print("form facet values (sample):", form_vals[:30], "count:", len(form_vals))


if __name__ == "__main__":
    main()

