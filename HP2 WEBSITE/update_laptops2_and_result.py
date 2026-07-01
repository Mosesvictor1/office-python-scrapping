import json
import re
from pathlib import Path

# ==============================================================================
# Part 1: Update laptops2.json with multiple types for each product
# ==============================================================================

LAPTOPS2_PATH = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/HP2 WEBSITE/Hpoutput/laptops2.json")

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
    if size <= 13:
        return "13"
    if size == 14:
        return "14"
    if size == 15:
        return "15"
    if size >= 16:
        return "16"
    return "14"

def map_laptop_types(text):
    """Return multiple types for a laptop."""
    types = []
    if "chromebook" in text:
        types.append("Chromebooks")
    if "macbook" in text:
        types.append("MacBooks")
    if "gaming" in text or "rtx" in text or "nvidia" in text:
        types.append("Gaming laptops")
    if "touchscreen" in text or "2 in 1" in text or "2-in-1" in text or "convertible" in text:
        types.append("2-in-1 laptops")
    if "ai pc" in text or "copilot" in text or "snapdragon x" in text or "elite" in text or "probook" in text:
        types.append("AI laptops")
    if not types or "windows" in text or "pro" in text:
        types.append("Windows laptops")
    return list(set(types))

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

def apply_laptop_filters(product):
    text = flatten_text(product)
    brand = (product.get("brand") or "HP").strip() or "HP"
    laptop_types = map_laptop_types(text)
    
    filters = {
        "types": [
            {"name": lt, "type": lt, "sub_category": "Laptops"}
            for lt in laptop_types
        ]
    }
    
    # Brands: add for each type
    filters["brands"] = [
        {"name": brand, "type": lt, "sub_category": "Laptops"}
        for lt in laptop_types
    ]
    
    memory = map_memory_filter(text)
    if memory:
        filters["memory"] = [
            {"name": memory, "type": lt, "sub_category": "Laptops"}
            for lt in laptop_types
        ]
    
    processor = map_laptop_processor(text)
    if processor:
        filters["processor"] = [
            {"name": processor, "type": lt, "sub_category": "Laptops"}
            for lt in laptop_types
        ]
    
    screen = map_screen_size_filter(text)
    if screen:
        filters["screen_size"] = [
            {"name": screen, "type": lt, "sub_category": "Laptops"}
            for lt in laptop_types
        ]
    
    storage = map_storage_filter(text)
    if storage:
        filters["storage_capacity"] = [
            {"name": storage, "type": lt, "sub_category": "Laptops"}
            for lt in laptop_types
        ]
    
    os_name = map_laptop_os(text)
    filters["operating_system"] = [
        {"name": os_name, "type": lt, "sub_category": "Laptops"}
        for lt in laptop_types
    ]
    
    return filters

# Update laptops2.json
if LAPTOPS2_PATH.exists():
    with LAPTOPS2_PATH.open(encoding="utf-8") as f:
        products = json.load(f)
    
    for product in products:
        product["filters"] = apply_laptop_filters(product)
    
    with LAPTOPS2_PATH.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Updated {LAPTOPS2_PATH.name} with multiple-type filters")

# ==============================================================================
# Part 2: Fix macbooks.json structure - convert specifications format
# ==============================================================================

MACBOOKS_PATH = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/NewComputing/Laptops result/macbooks.json")

def parse_macbooks_specs(spec_strings):
    """Convert string specs like '[OVERVIEW] Type: MacBook' to proper format."""
    structured = {"Technical Specifications": []}
    
    for spec_str in spec_strings:
        spec_str = spec_str.strip()
        if not spec_str or ":" not in spec_str:
            continue
        
        # Remove category prefix like [OVERVIEW], [SCREEN], etc.
        spec_str = re.sub(r"^\[.*?\]\s*", "", spec_str)
        
        # Split by first colon
        if ":" in spec_str:
            key, value = spec_str.split(":", 1)
            structured["Technical Specifications"].append({
                "key": key.strip(),
                "value": value.strip()
            })
    
    return [{"title": "Technical Specifications", "specs": structured["Technical Specifications"]}]

if MACBOOKS_PATH.exists():
    with MACBOOKS_PATH.open(encoding="utf-8") as f:
        products = json.load(f)
    
    for product in products:
        # Remove ean and offers
        product.pop("ean", None)
        product.pop("offers", None)
        
        # Convert specifications
        old_specs = product.get("specifications", [])
        if old_specs and isinstance(old_specs, list):
            product["specifications"] = parse_macbooks_specs(old_specs)
        else:
            product["specifications"] = [{"title": "Technical Specifications", "specs": []}]
        
        # Apply filters for MacBooks
        text = flatten_text(product)
        brand = product.get("brand", "APPLE").upper()
        
        product["filters"] = {
            "types": [{"name": "MacBooks", "type": "MacBooks", "sub_category": "Laptops"}],
            "brands": [{"name": brand, "type": "MacBooks", "sub_category": "Laptops"}]
        }
        
        memory = map_memory_filter(text)
        if memory:
            product["filters"]["memory"] = [
                {"name": memory, "type": "MacBooks", "sub_category": "Laptops"}
            ]
        
        processor = map_laptop_processor(text)
        if processor or "apple" in text.lower():
            if "m4" in text.lower():
                processor = "Apple M4"
            elif "m5" in text.lower():
                processor = "Apple M5"
            elif "m1" in text.lower():
                processor = "Apple M1"
            if processor:
                product["filters"]["processor"] = [
                    {"name": processor, "type": "MacBooks", "sub_category": "Laptops"}
                ]
        
        screen = map_screen_size_filter(text)
        if screen:
            product["filters"]["screen_size"] = [
                {"name": screen, "type": "MacBooks", "sub_category": "Laptops"}
            ]
        
        storage = map_storage_filter(text)
        if storage:
            product["filters"]["storage_capacity"] = [
                {"name": storage, "type": "MacBooks", "sub_category": "Laptops"}
            ]
        
        product["filters"]["operating_system"] = [
            {"name": "macOS", "type": "MacBooks", "sub_category": "Laptops"}
        ]
    
    with MACBOOKS_PATH.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Fixed {MACBOOKS_PATH.name} structure and applied filters")

# ==============================================================================
# Part 3: Clean up and fix Laptops result files
# ==============================================================================

LAPTOPS_RESULT_DIR = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/NewComputing/Laptops result")

for json_file in LAPTOPS_RESULT_DIR.glob("*.json"):
    if json_file.name == "macbooks.json":
        continue  # Already handled
    
    with json_file.open(encoding="utf-8") as f:
        products = json.load(f)
    
    for product in products:
        # Remove ean and offers
        product.pop("ean", None)
        product.pop("offers", None)
        
        # Ensure specifications are properly formatted
        specs = product.get("specifications", [])
        if specs and isinstance(specs, list) and len(specs) > 0:
            if isinstance(specs[0], str):
                # Convert string specs
                product["specifications"] = parse_macbooks_specs(specs)
            elif isinstance(specs[0], dict) and "title" not in specs[0]:
                # Already a list of dicts with key/value, wrap in structure
                if all("key" in s and "value" in s for s in specs):
                    product["specifications"] = [{"title": "Technical Specifications", "specs": specs}]
        else:
            product["specifications"] = [{"title": "Technical Specifications", "specs": []}]
        
        # Add filters intelligently
        text = flatten_text(product)
        brand = product.get("brand", "").strip() or "Unknown"
        category = product.get("category", "Laptops")
        
        # Determine laptop type based on filename
        if "2-in-1" in json_file.name:
            laptop_type = "2-in-1 laptops"
        elif "acer" in json_file.name:
            laptop_type = "Windows laptops"
        elif "ai" in json_file.name:
            laptop_type = "AI laptops"
        elif "asus" in json_file.name:
            laptop_type = "Windows laptops"
        elif "chromebook" in json_file.name:
            laptop_type = "Chromebooks"
        elif "macbook" in json_file.name:
            laptop_type = "MacBooks"
        elif "surface" in json_file.name:
            laptop_type = "2-in-1 laptops"
        else:
            laptop_type = "Windows laptops"
        
        product["filters"] = {
            "types": [{"name": laptop_type, "type": laptop_type, "sub_category": "Laptops"}],
            "brands": [{"name": brand, "type": laptop_type, "sub_category": "Laptops"}]
        }
        
        memory = map_memory_filter(text)
        if memory:
            product["filters"]["memory"] = [
                {"name": memory, "type": laptop_type, "sub_category": "Laptops"}
            ]
        
        processor = map_laptop_processor(text)
        if processor:
            product["filters"]["processor"] = [
                {"name": processor, "type": laptop_type, "sub_category": "Laptops"}
            ]
        
        screen = map_screen_size_filter(text)
        if screen:
            product["filters"]["screen_size"] = [
                {"name": screen, "type": laptop_type, "sub_category": "Laptops"}
            ]
        
        storage = map_storage_filter(text)
        if storage:
            product["filters"]["storage_capacity"] = [
                {"name": storage, "type": laptop_type, "sub_category": "Laptops"}
            ]
        
        os_name = "macOS" if "macbook" in json_file.name else map_laptop_os(text)
        product["filters"]["operating_system"] = [
            {"name": os_name, "type": laptop_type, "sub_category": "Laptops"}
        ]
    
    with json_file.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Fixed {json_file.name} - removed ean/offers, structured specs, applied filters")

print("\n✅ All laptops2.json and Laptops result files updated successfully!")
