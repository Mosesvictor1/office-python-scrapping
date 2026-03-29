import json
import os
from pathlib import Path

# Get current directory
current_dir = Path(__file__).parent

# Create laptops folder if it doesn't exist
laptops_dir = current_dir / "laptops"
laptops_dir.mkdir(exist_ok=True)

# Get all JSON files in current directory (excluding hp.json and this script)
json_files = [f for f in current_dir.glob("*.json") 
              if f.name not in ["hp.json", "merge_json.py"]]

print(f"Found {len(json_files)} JSON files to process")

# Array to hold all products
all_products = []

# Read each JSON file and update category
for json_file in json_files:
    print(f"Processing: {json_file.name}")
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            product = json.load(f)
            
        # Update category to "hp laptop"
        product['category'] = 'hp laptop'
        
        # Add to array
        all_products.append(product)
        
    except Exception as e:
        print(f"Error processing {json_file.name}: {str(e)}")

# Save all products to hp.json in laptops folder
output_file = laptops_dir / "hp.json"
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(all_products, f, indent=4, ensure_ascii=False)

print(f"\nSuccessfully merged {len(all_products)} products into {output_file}")
print(f"Output file size: {output_file.stat().st_size:,} bytes")
