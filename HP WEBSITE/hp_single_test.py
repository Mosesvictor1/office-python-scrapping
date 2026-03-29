import json
import re
import sys
import urllib.parse

import requests
from bs4 import BeautifulSoup


SCRAPER_API_KEY = "74516ff476f866b13129e249ca1fe471"
SCRAPER_API_URL = "http://api.scraperapi.com"

REQUEST_TIMEOUT = 90
ASYNC_TIMEOUT = 60


def fetch_via_scraperapi(url: str, render_js: bool, timeout: int) -> str | None:
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "render": "true" if render_js else "false",
        "country_code": "us",
        "keep_headers": "true",
    }
    try:
        r = requests.get(SCRAPER_API_URL, params=params, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.exceptions.RequestException as e:
        print(f"[fetch_via_scraperapi] error: {e}")
        return None


def fetch_direct(url: str, timeout: int) -> tuple[int, str | None]:
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        return r.status_code, r.text
    except requests.exceptions.RequestException as e:
        print(f"[fetch_direct] error: {e}")
        return 0, None


def _clean_html(text: str) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(separator=" ", strip=True)


def _normalise_spec_groups(raw) -> list[dict]:
    if not raw:
        return []

    groups: list[dict] = []

    if isinstance(raw, dict) and "sections" in raw:
        for section in raw.get("sections", []):
            title = (section.get("title") or "Specifications").strip()
            specs = []
            for i in section.get("items", []):
                if not isinstance(i, dict):
                    continue
                k = (i.get("label") or i.get("key") or i.get("name") or "").strip()
                if not k:
                    continue
                v = i.get("value", "")
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v)
                specs.append({"key": k, "value": _clean_html(str(v))})
            if specs:
                groups.append({"title": title, "specs": specs})
        return groups

    if isinstance(raw, dict):
        for group_title, items in raw.items():
            if not isinstance(items, list):
                continue
            specs = []
            for i in items:
                if not isinstance(i, dict):
                    continue
                k = (i.get("key") or i.get("name") or i.get("label") or "").strip()
                if not k:
                    continue
                v = i.get("value", "")
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v)
                specs.append({"key": k, "value": _clean_html(str(v))})
            if specs:
                groups.append({"title": str(group_title), "specs": specs})
        return groups

    if not isinstance(raw, list):
        return []

    if raw and isinstance(raw[0], dict) and (
        "label" in raw[0] or ("key" in raw[0] and "value" in raw[0])
    ) and "data" not in raw[0] and "specs" not in raw[0] and "categoryName" not in raw[0]:
        specs = []
        for i in raw:
            if not isinstance(i, dict):
                continue
            k = (i.get("label") or i.get("key") or i.get("name") or "").strip()
            if not k:
                continue
            v = i.get("value", "")
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            specs.append({"key": k, "value": _clean_html(str(v))})
        return [{"title": "Specifications", "specs": specs}] if specs else []

    for group in raw:
        if not isinstance(group, dict):
            continue
        title = (
            group.get("categoryName")
            or group.get("title")
            or group.get("groupTitle")
            or group.get("name")
            or "Specifications"
        )
        title = str(title).strip()
        items = group.get("specs") or group.get("data") or group.get("items") or group.get("attributes") or []
        specs = []
        for item in items:
            if not isinstance(item, dict):
                continue
            k = (
                item.get("name")
                or item.get("key")
                or item.get("label")
                or item.get("attributeName")
                or ""
            )
            k = str(k).strip()
            if not k:
                continue
            v = item.get("value") or item.get("attributeValue") or ""
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            specs.append({"key": k, "value": _clean_html(str(v))})
        if specs:
            groups.append({"title": title, "specs": specs})

    return groups


def _looks_like_specs(groups: list[dict]) -> bool:
    if not groups or sum(len(g.get("specs", [])) for g in groups) < 3:
        return False
    hardware_hints = {
        "processor",
        "memory",
        "storage",
        "display",
        "operating",
        "battery",
        "graphics",
        "weight",
        "dimension",
        "wireless",
        "bluetooth",
        "usb",
        "screen",
        "cpu",
        "ram",
        "ssd",
        "gpu",
        "resolution",
        "warranty",
    }
    for g in groups:
        for s in g.get("specs", []):
            if any(h in (s.get("key", "").lower()) for h in hardware_hints):
                return True
    return False


def _async_url_from_pdp(product_url: str) -> str | None:
    m = re.search(r"/pdp/([^/?#]+)", product_url)
    if not m:
        return None
    slug = m.group(1)
    encoded = urllib.parse.quote(f"pdp/{slug}", safe="")
    return f"https://www.hp.com/us-en/shop/app/api/web/graphql/page/{encoded}/async"


def _extract_store_json(html: str) -> tuple[dict, str]:
    soup = BeautifulSoup(html, "html.parser")
    data_div = soup.find("div", {"id": "data"})
    if data_div:
        # HP often stores JSON as an HTML comment node inside this div.
        # Using get_text() can lose/merge comment boundaries, so try comment-first.
        for child in data_div.children:
            s = str(child).strip()
            if s.startswith("<!--") and s.endswith("-->"):
                raw = re.sub(r"^<!--\s*", "", s)
                raw = re.sub(r"\s*-->$", "", raw)
                try:
                    return json.loads(raw), "div#data (comment node)"
                except Exception:
                    pass

        raw = data_div.get_text().strip()
        raw = re.sub(r"^<!--\s*", "", raw)
        raw = re.sub(r"\s*-->$", "", raw)
        try:
            return json.loads(raw), "div#data (text)"
        except Exception as e:
            return {}, f"div#data (json parse failed: {e})"
    return {}, "not found"


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            yield ("key", p, k, v)
            yield from _walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{path}[{i}]"
            yield ("idx", p, i, v)
            yield from _walk(v, p)


def _find_first(obj, predicate):
    for _, p, k, v in _walk(obj):
        if predicate(k, v, p):
            return p, k, v
    return None, None, None


def main():
    url = (
        sys.argv[1].strip()
        if len(sys.argv) > 1
        else "https://www.hp.com/us-en/shop/pdp/hp-zbook-fury-g1i-16-inch-mobile-workstation-pc-wolf-pro-security-edition-p-c3fk4ua-aba-1"
    )

    print(f"URL: {url}")

    async_url = _async_url_from_pdp(url)
    print(f"\n[1] Async endpoint: {async_url}")

    if async_url:
        code, direct_body = fetch_direct(async_url, timeout=ASYNC_TIMEOUT)
        print(f"  direct status: {code}, body_len: {len(direct_body or '')}")
        if direct_body:
            try:
                data = json.loads(direct_body)
                components = (
                    data.get("slugInfo", {}).get("components")
                    or data.get("components")
                    or data.get("data", {}).get("components")
                    or {}
                )
                print(f"  direct json ok. components keys: {len(components)}")
                for k in ("pdpTechSpecs", "techSpecs", "specifications", "productDetails", "productTechSpecs"):
                    if k in components:
                        groups = _normalise_spec_groups(components.get(k))
                        print(f"  direct components['{k}']: groups={len(groups)}, looks_like_specs={_looks_like_specs(groups)}")
            except Exception as e:
                print(f"  direct json parse failed: {e}")

        scraper_body = fetch_via_scraperapi(async_url, render_js=False, timeout=ASYNC_TIMEOUT)
        print(f"  scraperapi body_len: {len(scraper_body or '')}")
        if scraper_body:
            try:
                data = json.loads(scraper_body)
                top_keys = list(data.keys()) if isinstance(data, dict) else [type(data).__name__]
                print(f"  scraperapi json ok. top_keys_sample: {top_keys[:20]}")

                components = (
                    (data.get("slugInfo", {}) or {}).get("components")
                    or data.get("components")
                    or (data.get("data", {}) or {}).get("components")
                    or {}
                )
                print(f"  scraperapi components keys: {len(components)}")

                # Find spec-shaped keys anywhere in the async response
                spec_key_set = {
                    "pdpTechSpecs",
                    "techSpecs",
                    "specifications",
                    "productDetails",
                    "productTechSpecs",
                    "techSpecifications",
                }
                p, k, v = _find_first(
                    data,
                    lambda kk, vv, pp: isinstance(kk, str) and kk in spec_key_set and vv,
                )
                if p:
                    groups = _normalise_spec_groups(v)
                    total_specs = sum(len(g["specs"]) for g in groups)
                    print(
                        f"  FOUND '{k}' at {p}: groups={len(groups)}, specs={total_specs}, looks_like_specs={_looks_like_specs(groups)}"
                    )
                else:
                    print("  No known spec keys found anywhere in async JSON.")
            except Exception as e:
                print(f"  scraperapi json parse failed: {e}")

    print("\n[2] Rendered PDP HTML (ScraperAPI render=true)")
    html = fetch_via_scraperapi(url, render_js=True, timeout=REQUEST_TIMEOUT)
    print(f"  html_len: {len(html or '')}")
    if not html:
        return

    store, store_src = _extract_store_json(html)
    print(f"  store json source: {store_src}, store keys: {len(store)}")
    components = store.get("slugInfo", {}).get("components", {})
    print(f"  store components keys: {len(components)}")
    print("  has productInitial:", "productInitial" in components)

    soup = BeautifulSoup(html, "html.parser")
    dl_count = len(soup.find_all("dl"))
    table_count = len(soup.find_all("table"))
    print(f"  DOM dl_count={dl_count}, table_count={table_count}")


if __name__ == "__main__":
    main()

