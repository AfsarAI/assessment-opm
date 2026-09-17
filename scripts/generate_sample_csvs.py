#!/usr/bin/env python3
"""
Generate smaller benchmark and edge-case CSVs:
- 100 rows (for rapid integration tests)
- 10,000 rows (medium benchmark)
- 100,000 rows (stress test)
- edge_cases.csv (duplicate casing, special characters, max lengths)
"""

import os
import csv

def generate_samples(source_csv="products.csv", output_dir="scripts/samples"):
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Read first N rows from source
    with open(source_csv, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        
        rows_100 = []
        rows_10k = []
        rows_100k = []
        
        for idx, row in enumerate(reader, 1):
            if idx <= 100:
                rows_100.append(row)
            if idx <= 10000:
                rows_10k.append(row)
            if idx <= 100000:
                rows_100k.append(row)
            if idx == 100000:
                break

    for count, data in [(100, rows_100), (10000, rows_10k), (100000, rows_100k)]:
        out_path = os.path.join(output_dir, f"products_{count}.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(header)
            writer.writerows(data)
        print(f"Generated {out_path} ({len(data)} rows)")

    # 2. Generate explicit edge cases CSV
    edge_cases = [
        ["Widget Standard", "SKU-001", "Initial product"],
        ["Widget Duplicate Exact", "SKU-001", "Exact duplicate SKU - should replace previous"],
        ["Gadget Upper", "GADGET-999", "Upper case SKU"],
        ["Gadget Lower", "gadget-999", "Lower case SKU - should replace GADGET-999"],
        ["Gadget Mixed", "GaDgEt-999", "Mixed case SKU - final replacement winner"],
        ["Product With Quotes", 'QUOTED-"SPECIAL"-01', 'Description with "quotes" and commas, in text.'],
        ["Product With Unicode", "UNICODE-ÜBER-01", "Café, naïve, façade, résumé characters"],
        ["Product Max Length Name " + "X" * 10, "SKU-MAX-LEN-1234567", "Normal description"],
    ]
    edge_path = os.path.join(output_dir, "edge_cases.csv")
    with open(edge_path, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(edge_cases)
    print(f"Generated {edge_path} ({len(edge_cases)} rows)")

if __name__ == "__main__":
    generate_samples()
