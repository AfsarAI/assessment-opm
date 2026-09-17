import csv
import sys
import chardet
from collections import Counter

def inspect_csv(file_path):
    print(f"=== INSPECTING {file_path} ===")
    
    # 1. Inspect raw bytes / encoding / line endings
    with open(file_path, "rb") as f:
        raw_start = f.read(100000)
        encoding_detect = chardet.detect(raw_start)
        print(f"Detected encoding sample: {encoding_detect}")
        
        # Check newline behavior
        has_crlf = b"\r\n" in raw_start
        has_lf = b"\n" in raw_start and not has_crlf
        has_cr = b"\r" in raw_start and not has_crlf
        print(f"Newline behavior: CRLF={has_crlf}, LF_only={has_lf}, CR_only={has_cr}")

    # 2. Streaming CSV parse
    total_rows = 0
    header = None
    empty_counts = {"name": 0, "sku": 0, "description": 0}
    max_lengths = {"name": 0, "sku": 0, "description": 0}
    
    # Track SKUs using a set of hashes or lowercase set
    # 500k strings in a set is ~30-50MB RAM, perfectly fine for inspection script
    seen_exact_skus = set()
    seen_lower_skus = {}
    exact_duplicates = 0
    case_insensitive_duplicates = 0
    malformed_rows = 0
    
    sample_duplicates = []
    
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except Exception as e:
            print(f"Failed to read header: {e}")
            return
            
        print(f"Headers: {header}")
        expected_cols = len(header)
        
        for row_idx, row in enumerate(reader, start=1):
            total_rows += 1
            if len(row) != expected_cols:
                malformed_rows += 1
                if malformed_rows <= 5:
                    print(f"Malformed row {row_idx}: expected {expected_cols} cols, got {len(row)}: {row[:5]}")
                continue
                
            name, sku, description = row[0].strip(), row[1].strip(), row[2].strip()
            
            # Check empty
            if not name:
                empty_counts["name"] += 1
            if not sku:
                empty_counts["sku"] += 1
            if not description:
                empty_counts["description"] += 1
                
            # Max lengths
            if len(name) > max_lengths["name"]:
                max_lengths["name"] = len(name)
            if len(sku) > max_lengths["sku"]:
                max_lengths["sku"] = len(sku)
            if len(description) > max_lengths["description"]:
                max_lengths["description"] = len(description)
                
            # SKU uniqueness
            sku_lower = sku.lower()
            if sku in seen_exact_skus:
                exact_duplicates += 1
                if len(sample_duplicates) < 10:
                    sample_duplicates.append((row_idx, sku, "Exact match with previous"))
            elif sku_lower in seen_lower_skus:
                case_insensitive_duplicates += 1
                if len(sample_duplicates) < 10:
                    prev_idx, prev_sku = seen_lower_skus[sku_lower]
                    sample_duplicates.append((row_idx, sku, f"Case-variant match with row {prev_idx}: '{prev_sku}' vs '{sku}'"))
                    
            seen_exact_skus.add(sku)
            if sku_lower not in seen_lower_skus:
                seen_lower_skus[sku_lower] = (row_idx, sku)
                
            if total_rows % 100000 == 0:
                print(f"Processed {total_rows} rows...")
                
    print("\n=== SUMMARY RESULTS ===")
    print(f"Total rows (excluding header): {total_rows}")
    print(f"Malformed rows: {malformed_rows}")
    print(f"Empty counts: {empty_counts}")
    print(f"Max field lengths: {max_lengths}")
    print(f"Unique exact SKUs: {len(seen_exact_skus)}")
    print(f"Unique case-insensitive SKUs: {len(seen_lower_skus)}")
    print(f"Exact SKU duplicate occurrences: {exact_duplicates}")
    print(f"Case-insensitive SKU duplicate occurrences (different casing): {case_insensitive_duplicates}")
    print(f"Total duplicate SKU rows encountered: {exact_duplicates + case_insensitive_duplicates}")
    print(f"Net unique products if deduplicated: {len(seen_lower_skus)}")
    print(f"Sample duplicates: {sample_duplicates}")

if __name__ == "__main__":
    inspect_csv("/home/afsarai/assessment-opm/products.csv")
