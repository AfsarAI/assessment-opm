#!/usr/bin/env python3
"""
Benchmark suite testing multiple CSV sizes against live Render production backend.
Tests: 100 rows, 1,000 rows, 10,000 rows, 100,000 rows.
"""

import os
import sys
import time
import json
import requests

API_BASE = "https://opm-backend-p1i8.onrender.com"

FILES = [
    ("100 rows", "sample_100.csv"),
    ("1,000 rows", "sample_1000.csv"),
    ("10,000 rows", "sample_10000.csv"),
    ("100,000 rows", "sample_100000.csv"),
]

def benchmark_file(label, filename):
    file_path = os.path.abspath(filename)
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found")
        return None

    file_size_bytes = os.path.getsize(file_path)
    file_size_mb = file_size_bytes / (1024 * 1024)

    print(f"\n==================================================")
    print(f"BENCHMARKING: {label} ({filename})")
    print(f"File Size: {file_size_bytes:,} bytes ({file_size_mb:.2f} MB)")
    print(f"==================================================")

    # 1. Upload
    t_start = time.time()
    t_upload_start = time.time()
    with open(file_path, "rb") as f:
        upload_res = requests.post(
            f"{API_BASE}/api/v1/imports",
            files={"file": (os.path.basename(file_path), f, "text/csv")},
            timeout=180
        )
    t_upload_finish = time.time()
    upload_duration = t_upload_finish - t_upload_start

    if upload_res.status_code not in (200, 202):
        print(f"Upload failed: HTTP {upload_res.status_code} - {upload_res.text}")
        return None

    job_data = upload_res.json()
    job_id = job_data.get("import_id") or job_data.get("job_id") or job_data.get("id")
    print(f"Upload completed in {upload_duration:.2f}s (HTTP {upload_res.status_code}). Job ID: {job_id}")

    # 2. Track SSE
    sse_url = f"{API_BASE}/api/v1/imports/{job_id}/progress"
    events = []
    t_sse_start = time.time()
    t_importing_start = None
    t_completed = None

    with requests.get(sse_url, stream=True, timeout=300) as sse_res:
        buffer = ""
        for chunk in sse_res.iter_content(chunk_size=1024, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            while "\n\n" in buffer:
                raw_event, buffer = buffer.split("\n\n", 1)
                t_event = time.time()
                for line in raw_event.strip().split("\n"):
                    if line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            payload = json.loads(data_str)
                            events.append((t_event, payload))
                            status = payload.get("status")
                            prog = payload.get("progress")
                            msg = payload.get("stage_message", "")
                            if status == "IMPORTING" and t_importing_start is None:
                                t_importing_start = t_event
                            if status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                                t_completed = t_event
                                print(f"  -> Terminal state reached: [{status}] {prog}% - {msg}")
                                break
                        except Exception:
                            pass
            if t_completed:
                break

    t_end = time.time()
    total_time = t_end - t_start
    backend_time = t_end - t_upload_finish
    total_rows = events[-1][1].get("total_rows", 0) if events else 0
    throughput = total_rows / backend_time if backend_time > 0 and total_rows > 0 else 0

    metrics = {
        "label": label,
        "filename": filename,
        "file_size_bytes": file_size_bytes,
        "upload_time_s": round(upload_duration, 2),
        "backend_time_s": round(backend_time, 2),
        "total_time_s": round(total_time, 2),
        "total_rows": total_rows,
        "rows_per_sec": round(throughput, 1),
        "events_count": len(events),
        "status": events[-1][1].get("status") if events else "UNKNOWN"
    }

    print(f"Summary for {label}:")
    print(f"  Upload: {metrics['upload_time_s']}s")
    print(f"  Backend Import: {metrics['backend_time_s']}s")
    print(f"  Total Duration: {metrics['total_time_s']}s")
    print(f"  Throughput: {metrics['rows_per_sec']} rows/s")
    print(f"  Events Delivered: {metrics['events_count']}")
    print(f"  Final Status: {metrics['status']}")

    return metrics

def main():
    results = []
    for label, filename in FILES:
        m = benchmark_file(label, filename)
        if m:
            results.append(m)
        time.sleep(2)

    print("\n" + "="*70)
    print("MULTI-SIZE PRODUCTION BENCHMARK RESULTS")
    print("="*70)
    print(f"{'Size':<12} | {'File Size':<12} | {'Upload':<8} | {'Backend':<9} | {'Total':<8} | {'Rows/Sec':<10} | {'Status'}")
    print("-" * 70)
    for r in results:
        size_str = f"{r['file_size_bytes']:,} B"
        print(f"{r['label']:<12} | {size_str:<12} | {r['upload_time_s']:>6.2f}s | {r['backend_time_s']:>7.2f}s | {r['total_time_s']:>6.2f}s | {r['rows_per_sec']:>9.1f} | {r['status']}")

    with open("docs/benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
