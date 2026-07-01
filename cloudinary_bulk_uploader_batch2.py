import json
import requests
from pathlib import Path
from typing import Optional, Dict
import time

# Cloudinary credentials
CLOUDINARY_CLOUD_NAME = "decbrtduj"
CLOUDINARY_UPLOAD_PRESET = "unsigned_preset"
CLOUDINARY_UPLOAD_URL = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"

# Directories
NEWCOMPUTING_DIR = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/NewComputing")
NEWCOMPUTING_LAPTOPS_DIR = NEWCOMPUTING_DIR / "Laptops result"

# Files to process: Batch 2
BATCH_2_FILES = [
    ("asus-windows-laptops.json", NEWCOMPUTING_LAPTOPS_DIR),
    ("acer-windows-laptops.json", NEWCOMPUTING_LAPTOPS_DIR),
    ("chromebooks.json", NEWCOMPUTING_LAPTOPS_DIR),
    ("surface.json", NEWCOMPUTING_LAPTOPS_DIR),
]

def upload_image_url_to_cloudinary(image_url: str) -> Optional[str]:
    """Upload image from URL directly to Cloudinary."""
    if not image_url or not isinstance(image_url, str):
        return None
    
    # Skip if already a Cloudinary URL
    if "cloudinary.com" in image_url:
        return image_url
    
    try:
        data = {
            "file": image_url,
            "upload_preset": CLOUDINARY_UPLOAD_PRESET,
            "resource_type": "auto"
        }
        
        response = requests.post(CLOUDINARY_UPLOAD_URL, data=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            cloudinary_url = result.get("secure_url")
            if cloudinary_url:
                return cloudinary_url
        else:
            print(f"    Error: {response.status_code} - {response.text[:100]}")
            return None
    
    except Exception as e:
        print(f"    Exception: {str(e)}")
        return None

def process_file_optimized(filename: str, directory: Path) -> bool:
    """Process a single JSON file with optimized image uploading."""
    file_path = directory / filename
    
    if not file_path.exists():
        print(f"✗ File not found: {filename}")
        return False
    
    print(f"\n📄 Processing: {filename}")
    
    try:
        with file_path.open(encoding="utf-8") as f:
            products = json.load(f)
        
        total_products = len(products)
        total_images = 0
        uploaded_images = 0
        
        print(f"  Total products: {total_products}")
        
        # Process each product
        for idx, product in enumerate(products):
            images = product.get("images", [])
            if not images:
                continue
            
            product_title = product.get("title", f"Product {idx+1}")[:50]
            print(f"  [{idx+1}/{total_products}] {product_title}")
            
            new_images = []
            for img_url in images:
                if isinstance(img_url, str):
                    total_images += 1
                    # Try to upload
                    print(f"      Uploading image {total_images}...", end=" ")
                    cloudinary_url = upload_image_url_to_cloudinary(img_url)
                    
                    if cloudinary_url:
                        new_images.append(cloudinary_url)
                        uploaded_images += 1
                        print("✓")
                    else:
                        new_images.append(img_url)  # Keep original if upload fails
                        print("✗ (kept original)")
                    
                    # Small delay to avoid rate limiting
                    time.sleep(0.5)
                else:
                    new_images.append(img_url)
            
            product["images"] = new_images
        
        # Save back to file
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(products, f, indent=2, ensure_ascii=False)
        
        print(f"  ✅ Saved {filename}")
        print(f"     Uploaded: {uploaded_images}/{total_images} images")
        return True
    
    except Exception as e:
        print(f"  ✗ Error processing {filename}: {str(e)}")
        return False

def main():
    print("=" * 80)
    print("CLOUDINARY BULK IMAGE UPLOADER (BATCH 2: LAPTOP VARIANTS)")
    print("=" * 80)
    print(f"Cloud: {CLOUDINARY_CLOUD_NAME}")
    print(f"Upload Preset: {CLOUDINARY_UPLOAD_PRESET}")
    print(f"Files to process: {len(BATCH_2_FILES)}")
    print("=" * 80)
    
    start_time = time.time()
    processed_count = 0
    
    for filename, directory in BATCH_2_FILES:
        if process_file_optimized(filename, directory):
            processed_count += 1
        # Delay between files
        time.sleep(2)
    
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 80)
    print(f"✅ BATCH 2 COMPLETED: {processed_count}/{len(BATCH_2_FILES)} files processed")
    print(f"⏱ Time elapsed: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print("=" * 80)

if __name__ == "__main__":
    main()
