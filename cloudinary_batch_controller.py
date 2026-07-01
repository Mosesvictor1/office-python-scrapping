#!/usr/bin/env python3
"""
Master Cloudinary Batch Upload Controller
Runs all batches sequentially with timing and statistics
"""
import json
import requests
from pathlib import Path
from typing import Optional, List, Tuple
import time
import subprocess
import sys

# Cloudinary credentials
CLOUDINARY_CLOUD_NAME = "decbrtduj"
CLOUDINARY_UPLOAD_PRESET = "unsigned_preset"
CLOUDINARY_UPLOAD_URL = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"

# Batch configurations
BATCH_CONFIGS = {
    1: {
        "name": "BATCH 1: MAIN LAPTOPS (laptops2, macbooks, 2-in-1, ai-laptops)",
        "script": "cloudinary_bulk_uploader_batch.py"
    },
    2: {
        "name": "BATCH 2: LAPTOP VARIANTS (asus, acer, chromebooks, surface)",
        "script": "cloudinary_bulk_uploader_batch2.py"
    },
    3: {
        "name": "BATCH 3: DESKTOPS & MONITORS (desktops, workstations, monitors, inks_toner, computer monitors, Desktop PCs)",
        "script": "cloudinary_bulk_uploader_batch3.py"
    },
    4: {
        "name": "BATCH 4: REMAINING NEWCOMPUTING (data storage, bags, printers, components, accessories, iPad)",
        "script": "cloudinary_bulk_uploader_batch4.py"
    }
}

def run_batch_script(batch_num: int, script_name: str) -> Tuple[bool, float]:
    """Run a batch script and return success status and elapsed time."""
    print(f"\n{'='*80}")
    print(f"▶ STARTING {BATCH_CONFIGS[batch_num]['name']}")
    print(f"{'='*80}\n")
    
    start_time = time.time()
    
    try:
        result = subprocess.run(
            [sys.executable, script_name],
            cwd=Path.cwd(),
            capture_output=False,
            text=True
        )
        
        elapsed = time.time() - start_time
        success = result.returncode == 0
        
        if success:
            print(f"\n✅ BATCH {batch_num} COMPLETED successfully")
            print(f"   Elapsed: {elapsed:.1f}s ({elapsed/60:.1f}m)")
        else:
            print(f"\n✗ BATCH {batch_num} FAILED (exit code {result.returncode})")
        
        return success, elapsed
    
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n✗ BATCH {batch_num} ERROR: {str(e)}")
        return False, elapsed

def main():
    start_time = time.time()
    
    print("=" * 80)
    print("CLOUDINARY BULK IMAGE UPLOAD CONTROLLER")
    print("=" * 80)
    print(f"Cloud: {CLOUDINARY_CLOUD_NAME}")
    print(f"Upload Preset: {CLOUDINARY_UPLOAD_PRESET}")
    print(f"Total batches: {len(BATCH_CONFIGS)}")
    print("=" * 80)
    
    results = {}
    
    # Run each batch
    for batch_num in sorted(BATCH_CONFIGS.keys()):
        batch_config = BATCH_CONFIGS[batch_num]
        script_name = batch_config["script"]
        
        script_path = Path(script_name)
        if not script_path.exists():
            print(f"\n⚠ Batch {batch_num} script not found: {script_name}")
            results[batch_num] = (False, 0)
            continue
        
        success, elapsed = run_batch_script(batch_num, script_name)
        results[batch_num] = (success, elapsed)
        
        # Small delay between batches
        if batch_num < len(BATCH_CONFIGS):
            time.sleep(3)
    
    # Summary
    total_elapsed = time.time() - start_time
    successful_batches = sum(1 for success, _ in results.values() if success)
    
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    
    for batch_num in sorted(results.keys()):
        success, elapsed = results[batch_num]
        status = "✅ SUCCESS" if success else "✗ FAILED"
        print(f"  Batch {batch_num}: {status} ({elapsed/60:.1f}m)")
    
    print(f"\n📊 Total batches completed: {successful_batches}/{len(BATCH_CONFIGS)}")
    print(f"⏱ Total time: {total_elapsed:.1f}s ({total_elapsed/60:.1f}m / {total_elapsed/3600:.2f}h)")
    print("=" * 80)
    
    if successful_batches == len(BATCH_CONFIGS):
        print("\n🎉 ALL BATCHES COMPLETED SUCCESSFULLY!")
        return 0
    else:
        print(f"\n⚠ {len(BATCH_CONFIGS) - successful_batches} batch(es) failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
