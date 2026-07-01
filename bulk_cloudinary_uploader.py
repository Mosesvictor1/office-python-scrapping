import json
import requests
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import time

# Cloudinary credentials
CLOUDINARY_CLOUD_NAME = "decbrtduj"
CLOUDINARY_UPLOAD_PRESET = "unsigned_preset"
CLOUDINARY_UPLOAD_URL = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"

# Directories
NEWCOMPUTING_DIR = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/NewComputing")
HP2_HPOUTPUT_DIR = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/HP2 WEBSITE/Hpoutput")

# Files to process: 4-6 files at once, starting from laptops
FILES_TO_PROCESS = [
    ("laptops2.json", NEWCOMPUTING_DIR),
    ("macbooks.json", NEWCOMPUTING_DIR),
    ("2-in-1-laptops.json", NEWCOMPUTING_DIR),
    ("ai-laptops.json", NEWCOMPUTING_DIR),
    ("asus-windows-laptops.json", NEWCOMPUTING_DIR),
    ("acer-windows-laptops.json", NEWCOMPUTING_DIR),
]

# Track uploaded URLs to avoid re-uploading
uploaded_cache = {}

def upload_image_to_cloudinary(image_url, product_title=""):
    """Upload a single image to Cloudinary and return the new URL."""
    if not image_url or not isinstance(image_url, str):
        return None
    
    # Skip if already uploaded
    if image_url in uploaded_cache:
        return uploaded_cache[image_url]
    
    # Skip if already a Cloudinary URL
    if "cloudinary.com" in image_url:
        uploaded_cache[image_url] = image_url
        return image_url
    
    try:
        # Download the image
        response = requests.get(image_url, timeout=10)
        if response.status_code != 200:
            print(f"  ⚠ Failed to download: {image_url[:60]}...")
            return None
        
        # Upload to Cloudinary
        files = {"file": response.content}
        data = {
            "upload_preset": CLOUDINARY_UPLOAD_PRESET,
            "resource_type": "auto",
            "tags": "product_images"
        }
        
        upload_response = requests.post(CLOUDINARY_UPLOAD_URL, files=files, data=data, timeout=30)
        
        if upload_response.status_code == 200:
            result = upload_response.json()
            cloudinary_url = result.get("secure_url")
            if cloudinary_url:
                uploaded_cache[image_url] = cloudinary_url
                print(f"  ✓ Uploaded: {image_url[:50]}... → {cloudinary_url[:50]}...")
                return cloudinary_url
        else:
            print(f"  ✗ Upload failed: {upload_response.status_code}")
            return None
    
    except Exception as e:
        print(f"  ✗ Error uploading {image_url[:50]}...: {str(e)}")
        return None

def process_product(product):
    """Process a single product: upload images and update URLs."""
    images = product.get("images", [])
    if not images:
        return product
    
    new_images = []
    for img_url in images:
        if isinstance(img_url, str):
            cloudinary_url = upload_image_to_cloudinary(img_url, product.get("title", ""))
            if cloudinary_url:
                new_images.append(cloudinary_url)
            else:
                new_images.append(img_url)  # Keep original if upload fails
        else:
            new_images.append(img_url)
    
    product["images"] = new_images
    return product

def process_file(filename, directory):
    """Process a single JSON file."""
    file_path = directory / filename
    
    if not file_path.exists():
        print(f"✗ File not found: {filename}")
        return False
    
    print(f"\n📄 Processing: {filename}")
    
    try:
        with file_path.open(encoding="utf-8") as f:
            products = json.load(f)
        
        print(f"  Found {len(products)} products")
        
        # Process each product
        for idx, product in enumerate(products):
            print(f"  [{idx+1}/{len(products)}] {product.get('title', 'Unknown')[:60]}")
            products[idx] = process_product(product)
        
        # Save back to file
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(products, f, indent=2, ensure_ascii=False)
        
        print(f"  ✅ Saved {filename}")
        return True
    
    except Exception as e:
        print(f"  ✗ Error processing {filename}: {str(e)}")
        return False

def main():
    print("=" * 80)
    print("BULK CLOUDINARY IMAGE UPLOADER")
    print("=" * 80)
    print(f"Cloudinary Cloud: {CLOUDINARY_CLOUD_NAME}")
    print(f"Upload Preset: {CLOUDINARY_UPLOAD_PRESET}")
    print(f"Files to process: {len(FILES_TO_PROCESS)}")
    print("=" * 80)
    
    start_time = time.time()
    processed_count = 0
    
    for filename, directory in FILES_TO_PROCESS:
        if process_file(filename, directory):
            processed_count += 1
        # Add delay to avoid rate limiting
        time.sleep(1)
    
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 80)
    print(f"✅ COMPLETED: {processed_count}/{len(FILES_TO_PROCESS)} files processed")
    print(f"⏱ Time elapsed: {elapsed:.1f}s")
    print(f"📊 Total images cached: {len(uploaded_cache)}")
    print("=" * 80)

if __name__ == "__main__":
    main()
