import json
import os
import re
import time
import requests
from pathlib import Path
from datetime import datetime

CLOUDINARY_CLOUD_NAME = "decbrtduj"
CLOUDINARY_UPLOAD_PRESET = "unsigned_preset"
CLOUDINARY_UPLOAD_URL = (
    f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
)

NEWCOMPUTING_DIR = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/NewComputing")
HP_WEBSITE_DIR = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/HP2 WEBSITE/Hpoutput")
PROGRESS_FILE = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping/cloudinary_progress.json")

# Batch configuration: 4-6 files per batch
BATCH_SIZE = 5

# Files to process (start with laptops, then others)
FILES_TO_PROCESS = [
    ("NewComputing", "laptops2.json"),
    ("NewComputing", "2-in-1-laptops.json"),
    ("NewComputing", "ai-laptops.json"),
    ("NewComputing", "chromebooks.json"),
    ("NewComputing", "macbooks.json"),
    ("HP2 WEBSITE/Hpoutput", "laptops.json"),
    ("HP2 WEBSITE/Hpoutput", "desktops.json"),
    ("HP2 WEBSITE/Hpoutput", "monitors.json"),
    ("HP2 WEBSITE/Hpoutput", "workstations.json"),
    ("HP2 WEBSITE/Hpoutput", "inks_toner.json"),
    ("NewComputing", "computer monitors.json"),
    ("NewComputing", "Desktop PCs.json"),
    ("NewComputing", "Printers, scanners and ink.json"),
]

def load_progress():
    """Load progress from file."""
    if PROGRESS_FILE.exists():
        with PROGRESS_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return {
        "current_batch": 0,
        "files_processed": [],
        "current_file": None,
        "current_product_index": 0,
        "total_uploads": 0,
        "failed_uploads": 0,
        "last_update": datetime.now().isoformat()
    }

def save_progress(progress):
    """Save progress to file."""
    progress["last_update"] = datetime.now().isoformat()
    with PROGRESS_FILE.open("w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)

def upload_image_to_cloudinary(image_url):
    """Upload image URL to Cloudinary and return the new URL."""
    try:
        # Validate URL
        if not image_url or not image_url.startswith("http"):
            return None
        
        payload = {
            "file": image_url,
            "upload_preset": CLOUDINARY_UPLOAD_PRESET
        }
        
        response = requests.post(CLOUDINARY_UPLOAD_URL, data=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        if "secure_url" in result:
            print(f"  ✓ Uploaded: {image_url[:60]}... → {result['secure_url'][:60]}...")
            return result["secure_url"]
        else:
            print(f"  ✗ No URL in response for {image_url[:60]}...")
            return None
    except requests.exceptions.RequestException as e:
        print(f"  ✗ Upload failed for {image_url[:60]}...: {str(e)[:100]}")
        return None
    except Exception as e:
        print(f"  ✗ Unexpected error uploading {image_url[:60]}...: {str(e)[:100]}")
        return None

def process_product_images(product, product_index, file_path):
    """Process images for a single product and update URLs in place."""
    images = product.get("images", [])
    if not images:
        return 0, 0  # no images, no uploads/failures
    
    uploaded_count = 0
    failed_count = 0
    new_images = []
    
    for idx, img_url in enumerate(images, 1):
        if not img_url or not isinstance(img_url, str):
            new_images.append(img_url)
            continue
        
        # Check if already Cloudinary URL
        if "cloudinary.com" in img_url:
            print(f"    Image {idx}/{len(images)}: Already Cloudinary URL, skipping")
            new_images.append(img_url)
            continue
        
        # Upload to Cloudinary
        print(f"    Image {idx}/{len(images)}: Uploading...")
        cloudinary_url = upload_image_to_cloudinary(img_url)
        
        if cloudinary_url:
            new_images.append(cloudinary_url)
            uploaded_count += 1
        else:
            # Keep original URL if upload fails
            new_images.append(img_url)
            failed_count += 1
        
        time.sleep(0.5)  # Rate limiting
    
    # Update product with new URLs and save immediately
    product["images"] = new_images
    
    # Save the file immediately after updating this product
    with file_path.open("w", encoding="utf-8") as f:
        if isinstance(products_data, list):
            json.dump(products_data, f, indent=2, ensure_ascii=False)
    
    return uploaded_count, failed_count

def process_batch(batch_num, file_list):
    """Process a batch of files."""
    print(f"\n{'='*80}")
    print(f"BATCH {batch_num + 1}: Processing {len(file_list)} files")
    print(f"{'='*80}\n")
    
    progress = load_progress()
    
    for file_info in file_list:
        folder, filename = file_info
        
        # Construct full path
        if folder == "NewComputing":
            file_path = NEWCOMPUTING_DIR / filename
        else:
            file_path = Path("/Users/newupdate/Campushut/untitled folder/ScrappingProduct/office-python-scrapping") / folder / filename
        
        if not file_path.exists():
            print(f"⚠ File not found: {file_path}")
            continue
        
        print(f"\n📂 Processing: {filename}")
        
        # Load JSON
        with file_path.open(encoding="utf-8") as f:
            global products_data
            products_data = json.load(f)
        
        if not isinstance(products_data, list):
            print(f"⚠ {filename} is not a list, skipping")
            continue
        
        # Determine starting product index
        start_idx = 0
        if progress["current_file"] == filename:
            start_idx = progress["current_product_index"]
            print(f"  Resuming from product index {start_idx}")
        
        # Process each product
        batch_uploads = 0
        batch_failed = 0
        
        for prod_idx in range(start_idx, len(products_data)):
            product = products_data[prod_idx]
            title = product.get("title", f"Product {prod_idx}")
            print(f"\n  [{prod_idx + 1}/{len(products_data)}] {title[:70]}")
            
            uploaded, failed = process_product_images(product, prod_idx, file_path)
            batch_uploads += uploaded
            batch_failed += failed
            
            # Update progress after each product
            progress["current_file"] = filename
            progress["current_product_index"] = prod_idx + 1
            progress["total_uploads"] += uploaded
            progress["failed_uploads"] += failed
            save_progress(progress)
            
            if uploaded > 0:
                print(f"    → Uploaded {uploaded} images, {failed} failed")
        
        # Mark file as processed
        if filename not in progress["files_processed"]:
            progress["files_processed"].append(filename)
        progress["current_file"] = None
        progress["current_product_index"] = 0
        save_progress(progress)
        
        print(f"\n  ✅ File complete: {batch_uploads} uploaded, {batch_failed} failed")
    
    progress["current_batch"] = batch_num + 1
    save_progress(progress)

def main():
    """Main entry point."""
    print("\n" + "="*80)
    print("CLOUDINARY BULK IMAGE UPLOADER - Real-time URL Replacement")
    print("="*80)
    
    progress = load_progress()
    print(f"\n📊 Progress Summary:")
    print(f"  Batch: {progress['current_batch']}")
    print(f"  Total Uploads: {progress['total_uploads']}")
    print(f"  Failed: {progress['failed_uploads']}")
    print(f"  Last Update: {progress['last_update']}")
    
    # Determine which batch to process
    start_batch = progress["current_batch"]
    
    # Process batches sequentially
    for batch_idx in range(start_batch, len(FILES_TO_PROCESS), BATCH_SIZE):
        batch_files = FILES_TO_PROCESS[batch_idx : batch_idx + BATCH_SIZE]
        
        try:
            process_batch(batch_idx // BATCH_SIZE, batch_files)
        except KeyboardInterrupt:
            print("\n\n⚠ Interrupted by user. Progress saved. Run again to resume.")
            break
        except Exception as e:
            print(f"\n\n❌ Error in batch: {e}")
            break
        
        # Small delay between batches
        time.sleep(2)
    
    # Final summary
    progress = load_progress()
    print(f"\n\n{'='*80}")
    print(f"FINAL SUMMARY")
    print(f"{'='*80}")
    print(f"Total Uploads: {progress['total_uploads']}")
    print(f"Failed: {progress['failed_uploads']}")
    print(f"Files Processed: {len(progress['files_processed'])}")
    print(f"Files Remaining: {len(FILES_TO_PROCESS) - len(progress['files_processed'])}")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
