import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "Hpoutput"
CATEGORY_FILE = OUTPUT_DIR / "categories.json"

with CATEGORY_FILE.open(encoding="utf-8") as f:
    categories_data = json.load(f)

# Build lookup for category name -> category block
category_lookup = {}
for parent in categories_data.get("Computing", {}).get("subCategories", []):
    category_lookup[parent.get("name")] = parent


def flatten_text(product):
    parts = []
    for key in ["title", "subtitle", "overview"]:
        value = product.get(key, "") or ""
        if value:
            parts.append(str(value))
    for section in product.get("specifications", []) or []:
        for spec in section.get("specs", []) or []:
            for key in ["key", "value"]:
                value = spec.get(key, "") or ""
                if value:
                    parts.append(str(value))
    return " ".join(parts).lower()


def parse_memory_gb(text):
    match = re.search(r"(\d+)\s*(gb|tb)", text, re.I)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "tb":
        return value * 1024
    return value


def map_memory_filter(text):
    gb = parse_memory_gb(text)
    if gb is None:
        return None
    if gb >= 64:
        return "64 GB"
    if gb >= 32:
        return "32 GB"
    if gb >= 16:
        return "16 GB"
    if gb >= 8:
        return "8 GB"
    return "4 GB"


def map_storage_filter(text):
    gb = parse_memory_gb(text)
    if gb is None:
        return None
    if gb >= 2048:
        return "2 TB"
    if gb >= 1024:
        return "1 TB"
    if gb >= 512:
        return "512 GB"
    return "256 GB"


def map_screen_size_filter(text):
    match = re.search(r"(\d{1,2})\s*(?:inch|inches|in|\")", text, re.I)
    if not match:
        return None
    size = int(match.group(1))
    if size <= 21:
        return "Up to 21"
    if size <= 24:
        return "22 - 24"
    if size <= 26:
        return "25 - 26"
    if size <= 31:
        return "27 - 31"
    if size <= 34:
        return "32 - 34"
    return "35 and above"


def map_laptop_type(text):
    if "chromebook" in text:
        return "Chromebooks"
    if "macbook" in text:
        return "MacBooks"
    if "gaming" in text or "rtx" in text or "nvidia" in text:
        return "Gaming laptops"
    if "touchscreen" in text or "2 in 1" in text or "2-in-1" in text or "convertible" in text:
        return "2-in-1 laptops"
    if "ai pc" in text or "copilot" in text or "snapdragon x" in text:
        return "AI laptops"
    return "Windows laptops"


def map_laptop_processor(text):
    if "snapdragon x" in text:
        return "Snapdragon X Elite"
    if "apple m5" in text:
        return "Apple M5"
    if "apple m4" in text:
        return "Apple M4"
    if "core ultra" in text or "ultra 7" in text or "core 7" in text:
        return "Intel Core Ultra 7"
    if "core i9" in text:
        return "Intel Core Ultra 7"
    if "core i7" in text:
        return "Intel Core Ultra 7"
    if "core i5" in text:
        return "Intel Core i5"
    if "ryzen 7" in text:
        return "AMD Ryzen 7"
    if "ryzen 5" in text:
        return "AMD Ryzen 5"
    return None


def map_laptop_os(text):
    if "chrome os" in text:
        return "Chrome OS"
    if "macos" in text:
        return "macOS"
    if "windows 11 pro" in text:
        return "Windows 11 Pro"
    if "windows 11" in text:
        return "Windows 11 Home"
    return "Windows 11 Home"


def map_desktop_type(text):
    if "all-in-one" in text or "aio" in text:
        return "All-in-one PCs"
    if "gaming" in text or "omen" in text:
        return "Gaming PCs"
    if "mac mini" in text or "imac" in text:
        return "iMac / Mac mini"
    return "Tower PCs"


def map_desktop_processor(text):
    if "core ultra" in text or "ultra 7" in text or "core 7" in text:
        return "Intel Core i7"
    if "core i9" in text:
        return "Intel Core i9"
    if "core i7" in text:
        return "Intel Core i7"
    if "core i5" in text:
        return "Intel Core i5"
    if "ryzen 7" in text:
        return "AMD Ryzen 7"
    if "ryzen 5" in text:
        return "AMD Ryzen 5"
    if "apple m4" in text:
        return "Apple M4"
    return None


def map_monitor_type(text):
    if "gaming" in text:
        return "Gaming monitors"
    if "4k" in text or "ultra hd" in text:
        return "4K monitors"
    if "curved" in text:
        return "Curved monitors"
    if "dual" in text:
        return "Dual mode monitors"
    return "PC monitors"


def map_panel_type(text):
    if "oled" in text:
        return "OLED"
    if "mini-led" in text or "mini led" in text:
        return "Mini-LED"
    if "ips" in text:
        return "IPS"
    if "va" in text:
        return "VA"
    if "tn" in text:
        return "TN"
    return None


def map_refresh_rate(text):
    if "360" in text and "hz" in text:
        return "360 Hz"
    if "240" in text and "hz" in text:
        return "240 Hz"
    if "144" in text and "hz" in text:
        return "144 Hz"
    if "75" in text and "hz" in text:
        return "75 Hz"
    return "60 Hz"


def build_filter_entry(name, type_name, sub_category):
    return {"name": name, "type": type_name, "sub_category": sub_category}


def apply_filters(product, category_name):
    text = flatten_text(product)
    brand = (product.get("brand") or "HP").strip() or "HP"
    sub_category = category_name

    if category_name == "Laptops":
        type_name = map_laptop_type(text)
        filters = {"types": [build_filter_entry(type_name, type_name, sub_category)]}
        filters["brands"] = [build_filter_entry(brand, type_name, sub_category)]
        memory = map_memory_filter(text)
        if memory:
            filters["memory"] = [build_filter_entry(memory, type_name, sub_category)]
        processor = map_laptop_processor(text)
        if processor:
            filters["processor"] = [build_filter_entry(processor, type_name, sub_category)]
        screen = map_screen_size_filter(text)
        if screen:
            filters["screen_size"] = [build_filter_entry(screen, type_name, sub_category)]
        storage = map_storage_filter(text)
        if storage:
            filters["storage_capacity"] = [build_filter_entry(storage, type_name, sub_category)]
        os_name = map_laptop_os(text)
        filters["operating_system"] = [build_filter_entry(os_name, type_name, sub_category)]
        return filters

    if category_name == "Desktop PCs":
        type_name = map_desktop_type(text)
        filters = {"types": [build_filter_entry(type_name, type_name, sub_category)]}
        filters["brands"] = [build_filter_entry(brand, type_name, sub_category)]
        processor = map_desktop_processor(text)
        if processor:
            filters["processor"] = [build_filter_entry(processor, type_name, sub_category)]
        storage = map_storage_filter(text)
        if storage:
            filters["storage_capacity"] = [build_filter_entry(storage, type_name, sub_category)]
        return filters

    if category_name == "Computer Monitors":
        type_name = map_monitor_type(text)
        filters = {"types": [build_filter_entry(type_name, type_name, sub_category)]}
        filters["brands"] = [build_filter_entry(brand, type_name, sub_category)]
        screen = map_screen_size_filter(text)
        if screen:
            filters["screen_size"] = [build_filter_entry(screen, type_name, sub_category)]
        panel = map_panel_type(text)
        if panel:
            filters["panel_type"] = [build_filter_entry(panel, type_name, sub_category)]
        refresh = map_refresh_rate(text)
        filters["refresh_rate"] = [build_filter_entry(refresh, type_name, sub_category)]
        return filters

    if category_name == "Printers, Scanners and Ink":
        type_name = "Printer ink cartridges"
        filters = {"types": [build_filter_entry(type_name, type_name, sub_category)]}
        filters["brands"] = [build_filter_entry(brand, type_name, sub_category)]
        return filters

    return {}


for filename, category_name in [
    ("laptops.json", "Laptops"),
    ("desktops.json", "Desktop PCs"),
    ("workstations.json", "Desktop PCs"),
    ("monitors.json", "Computer Monitors"),
    ("inks_toner.json", "Printers, Scanners and Ink"),
]:
    path = OUTPUT_DIR / filename
    if not path.exists():
        continue
    with path.open(encoding="utf-8") as f:
        products = json.load(f)

    for product in products:
        product["filters"] = apply_filters(product, category_name)

    with path.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)

    print(f"Updated {filename} with filters")
