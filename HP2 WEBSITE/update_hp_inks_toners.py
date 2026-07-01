import json
import re
from pathlib import Path

INKS_TONERS_FILE = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/NewComputing/hp_inks_toners.json")

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
    return " ".join(parts).lower()

def map_ink_toner_type(text):
    """Determine if it's ink or toner cartridge."""
    if "laser" in text or "toner" in text:
        return "Laser toner"
    if "ink" in text:
        return "Ink cartridge"
    return "Ink cartridge"

def apply_ink_toner_filters(product):
    """Apply filters for HP ink and toner products."""
    text = flatten_text(product)
    brand = product.get("brand", "HP").strip() or "HP"
    cartridge_type = map_ink_toner_type(text)
    
    filters = {
        "types": [{"name": cartridge_type, "type": cartridge_type, "sub_category": "Printers, Scanners and Ink"}],
        "brands": [{"name": brand, "type": cartridge_type, "sub_category": "Printers, Scanners and Ink"}]
    }
    
    return filters

if INKS_TONERS_FILE.exists():
    with INKS_TONERS_FILE.open(encoding="utf-8") as f:
        products = json.load(f)
    
    for product in products:
        product["filters"] = apply_ink_toner_filters(product)
    
    with INKS_TONERS_FILE.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Updated {INKS_TONERS_FILE.name} with filters")
    
    # Verify
    with INKS_TONERS_FILE.open(encoding="utf-8") as f:
        products = json.load(f)
    
    print(f"\nSample products ({len(products)} total):")
    for p in products[:3]:
        print(f"  - {p['title']}: {p.get('filters', {}).get('types', [])}")
