#!/usr/bin/env python3
"""
API Responsiveness Benchmark under heavy 500,000-row CSV ingestion load.
Measures latency (min, max, p50, p95, p99) of:
- GET /health (liveness)
- GET /ready (readiness with DB and Redis checks)
- GET /api/v1/products?limit=10 (database read load)
- POST /api/v1/products (concurrent single insert)
while the Celery worker is performing bulk COPY and SQL UPSERT.
"""

import sys
import time
import json
import statistics
import threading
import urllib.request
import urllib.error

API_BASE = "http://localhost:8000"

latencies_health = []
latencies_ready = []
latencies_products_get = []
latencies_product_create = []
errors = []

stop_event = threading.Event()

def worker_ping_health():
    while not stop_event.is_set():
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{API_BASE}/health")
            with urllib.request.urlopen(req, timeout=5) as res:
                if res.status == 200:
                    latencies_health.append((time.time() - t0) * 1000)
        except Exception as e:
            errors.append(f"health: {e}")
        time.sleep(0.05)

def worker_ping_ready():
    while not stop_event.is_set():
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{API_BASE}/ready")
            with urllib.request.urlopen(req, timeout=5) as res:
                if res.status == 200:
                    latencies_ready.append((time.time() - t0) * 1000)
        except Exception as e:
            errors.append(f"ready: {e}")
        time.sleep(0.1)

def worker_ping_products():
    while not stop_event.is_set():
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{API_BASE}/api/v1/products?limit=10")
            with urllib.request.urlopen(req, timeout=5) as res:
                if res.status == 200:
                    latencies_products_get.append((time.time() - t0) * 1000)
        except Exception as e:
            errors.append(f"products_get: {e}")
        time.sleep(0.1)

def worker_create_product():
    counter = 0
    while not stop_event.is_set():
        counter += 1
        t0 = time.time()
        try:
            sku = f"RESPONSIVENESS-TEST-{counter}-{int(time.time()*1000)}"
            body = json.dumps({
                "sku": sku,
                "name": f"Concurrent Product {counter}",
                "description": "Created concurrently during 500K ingestion"
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{API_BASE}/api/v1/products",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as res:
                if res.status in (200, 201):
                    latencies_product_create.append((time.time() - t0) * 1000)
        except Exception as e:
            errors.append(f"product_create: {e}")
        time.sleep(0.5)

def upload_and_monitor(file_path):
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
        f"{API_BASE}/api/v1/imports",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST"
    )

    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
        import_id = data["import_id"]
        print(f"✓ Upload accepted in {(time.time() - t0):.2f}s (Job: {import_id})")

    sse_url = f"{API_BASE}/api/v1/imports/{import_id}/progress"
    req_sse = urllib.request.Request(sse_url, headers={"Accept": "text/event-stream"})
    
    with urllib.request.urlopen(req_sse, timeout=300) as stream:
        for line in stream:
            line_str = line.decode("utf-8").strip()
            if line_str.startswith("data:"):
                raw_json = line_str[5:].strip()
                if not raw_json:
                    continue
                payload = json.loads(raw_json)
                status = payload.get("status")
                progress = payload.get("progress", 0)
                stage = payload.get("stage_message", "")
                if progress % 25 == 0 or status in ("COMPLETED", "FAILED"):
                    print(f"  [Load Progress] {progress}% | {status} | {stage}")
                if status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                    return payload

def compute_stats(name, values):
    if not values:
        print(f"  {name}: No requests completed.")
        return
    values_sorted = sorted(values)
    p50 = statistics.median(values_sorted)
    p95 = values_sorted[int(len(values_sorted) * 0.95)] if len(values_sorted) >= 20 else max(values_sorted)
    p99 = values_sorted[int(len(values_sorted) * 0.99)] if len(values_sorted) >= 100 else max(values_sorted)
    print(f"  {name} (n={len(values)}):")
    print(f"    Min: {min(values):.1f}ms | Mean: {statistics.mean(values):.1f}ms | Median(p50): {p50:.1f}ms | p95: {p95:.1f}ms | p99: {p99:.1f}ms | Max: {max(values):.1f}ms")

if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "products.csv"
    print("================================================================")
    print("Starting API Responsiveness Benchmark under 500K Import Load")
    print("================================================================")

    # Start polling threads
    t_health = threading.Thread(target=worker_ping_health, daemon=True)
    t_ready = threading.Thread(target=worker_ping_ready, daemon=True)
    t_products = threading.Thread(target=worker_ping_products, daemon=True)
    t_create = threading.Thread(target=worker_create_product, daemon=True)

    t_health.start()
    t_ready.start()
    t_products.start()
    t_create.start()

    start_time = time.time()
    try:
        final_payload = upload_and_monitor(csv_file)
    finally:
        stop_event.set()
        total_duration = time.time() - start_time

    print("\n================================================================")
    print(f"Responsiveness Results during {total_duration:.2f}s 500K Ingestion")
    print("================================================================")
    print(f"Errors encountered: {len(errors)}")
    if errors:
        for err in errors[:5]:
            print(f"  Sample error: {err}")

    compute_stats("GET /health (Liveness)", latencies_health)
    compute_stats("GET /ready (Readiness - DB & Redis)", latencies_ready)
    compute_stats("GET /api/v1/products (DB Read Load)", latencies_products_get)
    compute_stats("POST /api/v1/products (Concurrent Write Load)", latencies_product_create)
    print("================================================================")
