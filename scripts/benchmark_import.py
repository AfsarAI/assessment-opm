#!/usr/bin/env python3
"""
Benchmark script for Assessment OPM.
Tests 500,000-row CSV import end-to-end against the running FastAPI backend.
Measures:
- Upload duration
- Server-side processing time (via SSE progress stream)
- Throughput (rows/second)
- Status transitions and final record count
"""

import sys
import time
import json
import urllib.request
import urllib.error
from datetime import datetime

API_BASE = "http://localhost:8000/api/v1"

def check_health():
    try:
        req = urllib.request.Request("http://localhost:8000/health")
        with urllib.request.urlopen(req, timeout=5) as res:
            if res.status == 200:
                print("✓ Backend health check passed.")
                return True
    except Exception as e:
        print(f"✗ Backend not reachable at http://localhost:8000: {e}")
        return False

def upload_csv(file_path):
    print(f"\n[1/3] Uploading {file_path} to {API_BASE}/imports...")
    start_time = time.time()
    
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    filename = file_path.split("/")[-1]
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{API_BASE}/imports",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            upload_duration = time.time() - start_time
            import_id = data["import_id"]
            print(f"✓ Upload succeeded in {upload_duration:.2f}s.")
            print(f"  Import Job ID: {import_id} (Status: {data['status']})")
            return import_id, upload_duration
    except urllib.error.HTTPError as e:
        print(f"✗ Upload failed with status {e.code}: {e.read().decode()}")
        sys.exit(1)

def track_progress_sse(import_id):
    print(f"\n[2/3] Listening to real-time SSE progress stream...")
    sse_url = f"{API_BASE}/imports/{import_id}/progress"
    req = urllib.request.Request(sse_url, headers={"Accept": "text/event-stream"})
    
    start_time = time.time()
    last_reported_stage = None

    try:
        with urllib.request.urlopen(req, timeout=600) as stream:
            for line in stream:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data:"):
                    raw_json = line_str[5:].strip()
                    if not raw_json:
                        continue
                    payload = json.loads(raw_json)
                    status = payload.get("status")
                    progress = payload.get("progress", 0)
                    processed = payload.get("processed_rows", 0)
                    total = payload.get("total_rows", 0)
                    stage = payload.get("stage_message", "")

                    if (status, stage) != last_reported_stage or progress % 20 == 0:
                        last_reported_stage = (status, stage)
                        print(f"  [{status}] {progress}% | Processed: {processed:,} / {total:,} rows | {stage}")

                    if status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                        duration = time.time() - start_time
                        print(f"\n[3/3] Import finished with terminal status: {status}")
                        print(f"  Server Processing Duration: {duration:.2f}s")
                        print(f"  Successful rows: {payload.get('successful_rows', 0):,}")
                        print(f"  Failed rows: {payload.get('failed_rows', 0):,}")
                        if total > 0:
                            throughput = total / duration if duration > 0 else 0
                            print(f"  Throughput: {throughput:,.0f} rows/sec")
                        return payload, duration
    except Exception as e:
        print(f"✗ SSE connection error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "products.csv"
    if not check_health():
        sys.exit(1)
    import_id, upload_time = upload_csv(csv_file)
    result, proc_time = track_progress_sse(import_id)
