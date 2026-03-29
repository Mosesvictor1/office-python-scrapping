import json
import glob
import os

# Get all JSON files in the directory (excluding AllCategories.json)
json_files = [f for f in glob.glob('*.json') if f != 'AllCategories.json']

def convert_filename_to_category(filename):
    """Convert filename to category name, avoiding duplicate 'chair/chairs'"""
    # Remove .json extension
    name = filename.replace('.json', '')
    # Replace hyphens with spaces
    name = name.replace('-', ' ')
    
    # Check if it already ends with 'chair' or 'chairs'
    if name.endswith(' chair') or name.endswith(' chairs'):
        return name  # Don't add ' chairs' again
    elif name.endswith(' furniture'):
        return name  # Don't add ' chairs' for furniture
    else:
        return name + ' chairs'

def update_category_in_file(filepath, new_category):
    """Update the category field in all products within a file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Update category for each product
        for product in data:
            product['category'] = new_category
        
        # Write back to file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"✓ Updated {filepath} -> category: '{new_category}'")
        return True
        
    except Exception as e:
        print(f"✗ Error processing {filepath}: {str(e)}")
        return False

# Process all product JSON files
print("Step 1: Updating category names in all product files...")
print("=" * 60)

category_mapping = {}

for json_file in json_files:
    new_category = convert_filename_to_category(json_file)
    category_mapping[json_file] = new_category
    update_category_in_file(json_file, new_category)

print("\n" + "=" * 60)
print(f"\nTotal files processed: {len(json_files)}")

# Step 2: Create AllCategories.json
print("\nStep 2: Creating AllCategories.json...")
print("=" * 60)

# Organize categories by type
all_categories = {
    "Office Chairs": [],
    "Guest Seating": [],
    "Lounge Furniture": [],
    "Specialty Chairs": []
}

# Categorize the new category names
for filename, category in category_mapping.items():
    if filename.startswith('executive-'):
        all_categories["Office Chairs"].append(category)
    elif filename.startswith('guest-'):
        all_categories["Guest Seating"].append(category)
    elif filename.startswith('lounge-'):
        all_categories["Lounge Furniture"].append(category)
    elif filename in ['multipurpose-chair.json', 'stackable-chairs.json']:
        all_categories["Specialty Chairs"].append(category)

# Write AllCategories.json
with open('AllCategories.json', 'w', encoding='utf-8') as f:
    json.dump(all_categories, f, indent=2, ensure_ascii=False)

print("\n✓ Created AllCategories.json with the following structure:")
print(json.dumps(all_categories, indent=2))

print("\n" + "=" * 60)
print("Done! All categories have been updated successfully.")
