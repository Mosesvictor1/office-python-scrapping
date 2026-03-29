# Get all JSON files in current directory
$jsonFiles = Get-ChildItem -Path "." -Filter "*.json" | Where-Object { $_.Name -ne "hp.json" -and $_.Name -ne "merge_json.ps1" }

# Array to hold all products
$allProducts = @()

# Read each JSON file and update category
foreach ($file in $jsonFiles) {
    Write-Host "Processing: $($file.Name)"
    
    # Read the JSON content
    $content = Get-Content -Path $file.FullName -Raw -Encoding UTF8
    $product = $content | ConvertFrom-Json
    
    # Update category to "hp laptop"
    $product.category = "hp laptop"
    
    # Add to array
    $allProducts += $product
}

# Convert back to JSON and save
$allProducts | ConvertTo-Json -Depth 100 | Out-File -FilePath ".\laptops\hp.json" -Encoding UTF8

Write-Host "Successfully merged $($allProducts.Count) products into laptops\hp.json"
