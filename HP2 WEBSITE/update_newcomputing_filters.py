import json
import re
from pathlib import Path

NEWCOMPUTING_DIR = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/NewComputing")
CATEGORIES_FILE = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/HP2 WEBSITE/Hpoutput/categories.json")

def flatten_text(product):
    parts = []
    for key in ["title", "subtitle", "overview"]:
        value = product.get(key, "") or ""
        if value:
            parts.append(str(value))
    for section in product.get("specifications", []) or []:
        if isinstance(section, dict):
            for spec in section.get("specs", []) or []:
                for k in ["key", "value"]:
                    value = spec.get(k, "") or ""
                    if value:
                        parts.append(str(value))
        elif isinstance(section, str):
            parts.append(section)
    return " ".join(parts).lower()

def parse_memory_gb(text):
    match = re.search(r"(\d+)\s*(gb|tb)", text, re.I)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    return value * 1024 if unit == "tb" else value

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
    match = re.search(r"(\d{1,2}(?:\.\d)?)\s*(?:inch|inches|in|\")", text, re.I)
    if not match:
        return None
    size = float(match.group(1))
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

def apply_filters_by_category(product, filename):
    text = flatten_text(product)
    brand = product.get("brand", "").strip() or "Unknown"
    category = product.get("category", "").strip()
    
    # Map filename to category filters
    if "monitor" in filename:
        monitor_type = "Gaming monitors" if "gaming" in text else "4K monitors" if "4k" in text else "PC monitors"
        filters = {
            "types": [{"name": monitor_type, "type": monitor_type, "sub_category": "Computer Monitors"}],
            "brands": [{"name": brand, "type": monitor_type, "sub_category": "Computer Monitors"}]
        }
        screen = map_screen_size_filter(text)
        if screen:
            filters["screen_size"] = [{"name": screen, "type": monitor_type, "sub_category": "Computer Monitors"}]
        panel = map_panel_type(text)
        if panel:
            filters["panel_type"] = [{"name": panel, "type": monitor_type, "sub_category": "Computer Monitors"}]
        filters["refresh_rate"] = [{"name": map_refresh_rate(text), "type": monitor_type, "sub_category": "Computer Monitors"}]
        return filters
    
    elif "desktop" in filename:
        desktop_type = "All-in-one PCs" if "aio" in text or "all-in-one" in text else "Tower PCs"
        filters = {
            "types": [{"name": desktop_type, "type": desktop_type, "sub_category": "Desktop PCs"}],
            "brands": [{"name": brand, "type": desktop_type, "sub_category": "Desktop PCs"}]
        }
        storage = map_storage_filter(text)
        if storage:
            filters["storage_capacity"] = [{"name": storage, "type": desktop_type, "sub_category": "Desktop PCs"}]
        return filters
    
    elif "printer" in filename or "scanner" in filename:
        printer_type = "Laser printers" if "laser" in text else "Inkjet printers"
        filters = {
            "types": [{"name": printer_type, "type": printer_type, "sub_category": "Printers, Scanners and Ink"}],
            "brands": [{"name": brand, "type": printer_type, "sub_category": "Printers, Scanners and Ink"}]
        }
        return filters
    
    elif "storage" in filename:
        filters = {
            "types": [{"name": "Data storage", "type": "Data storage", "sub_category": "Data storage"}],
            "brands": [{"name": brand, "type": "Data storage", "sub_category": "Data storage"}]
        }
        return filters
    
    elif "bag" in filename or "case" in filename:
        filters = {
            "types": [{"name": "Laptop bags and cases", "type": "Laptop bags and cases", "sub_category": "Laptop bags and cases"}],
            "brands": [{"name": brand, "type": "Laptop bags and cases", "sub_category": "Laptop bags and cases"}]
        }
        return filters
    
    elif "ipad" in filename or "tablet" in filename or "ereader" in filename:
        device_type = "iPad" if "ipad" in text else "Tablets" if "tablet" in text else "eReaders"
        filters = {
            "types": [{"name": device_type, "type": device_type, "sub_category": "iPad, Tablets & eReaders"}],
            "brands": [{"name": brand, "type": device_type, "sub_category": "iPad, Tablets & eReaders"}]
        }
        return filters
    
    elif "component" in filename or "upgrade" in filename or "accessory" in filename:
        filters = {
            "types": [{"name": "Components", "type": "Components", "sub_category": "Components and upgrades"}],
            "brands": [{"name": brand, "type": "Components", "sub_category": "Components and upgrades"}]
        }
        return filters
    
    return {}

# Process all JSON files in NewComputing (excluding Laptops result folder)
for json_file in sorted(NEWCOMPUTING_DIR.glob("*.json")):
    with json_file.open(encoding="utf-8") as f:
        products = json.load(f)
    
    for product in products:
        # Remove ean and offers if present
        product.pop("ean", None)
        product.pop("offers", None)
        
        # Ensure specifications are properly formatted
        specs = product.get("specifications", [])
        if specs and isinstance(specs, list) and len(specs) > 0:
            if isinstance(specs[0], str):
                # Convert string specs to proper format
                structured = []
                for spec_str in specs:
                    spec_str = spec_str.strip()
                    if spec_str and ":" in spec_str:
                        spec_str = re.sub(r"^\[.*?\]\s*", "", spec_str)
                        key, value = spec_str.split(":", 1)
                        structured.append({"key": key.strip(), "value": value.strip()})
                product["specifications"] = [{"title": "Technical Specifications", "specs": structured}]
            elif isinstance(specs[0], dict) and "title" not in specs[0]:
                # Already key/value dicts, wrap in structure
                if all("key" in s and "value" in s for s in specs):
                    product["specifications"] = [{"title": "Technical Specifications", "specs": specs}]
        else:
            product["specifications"] = [{"title": "Technical Specifications", "specs": []}]
        
        # Apply filters
        product["filters"] = apply_filters_by_category(product, json_file.name.lower())
    
    with json_file.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Updated {json_file.name}")

print("\n✅ All NewComputing product files updated with filters!")
