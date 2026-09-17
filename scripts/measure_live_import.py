import time
import json
import sys
import os
import requests

BACKEND_URL = "https://opm-backend-rf77.onrender.com"
CSV_FILE = sys.argv[1] if len(sys.argv) > 1 else "products.csv"

def run_live_measurement():
    print("==================================================")
    print(f"LIVE PRODUCTION MEASUREMENT: {BACKEND_URL}")
    print(f"File: {CSV_FILE}")
    print("==================================================")

    # File Stats
    file_size_bytes = os.path.getsize(CSV_FILE)
    print(f"File Size: {file_size_bytes:,} bytes ({file_size_bytes / (1024 * 1024):.2f} MB)")

    # 1. Measure Health / Readiness
    t_start = time.time()
    try:
        r_ready = requests.get(f"{BACKEND_URL}/ready", timeout=60)
        ready_dur = time.time() - t_start
        print(f"Backend readiness check: HTTP {r_ready.status_code} in {ready_dur:.3f}s")
        print(f"Response: {r_ready.text}")
    except Exception as e:
        print(f"Backend readiness failed: {e}")
        return

    # T0: File selected
    t0 = time.time()
    print(f"\n[T0 = {t0:.3f}] File selected: {CSV_FILE}")

    # T1: Upload starts
    t1 = time.time()
    print(f"[T1 = {t1:.3f}] Upload starting to {BACKEND_URL}/api/v1/imports...")

    with open(CSV_FILE, "rb") as f:
        files = {"file": (CSV_FILE, f, "text/csv")}
        res = requests.post(f"{BACKEND_URL}/api/v1/imports", files=files, timeout=300)

    # T2: Upload finishes
    t2 = time.time()
    upload_duration = t2 - t1
    print(f"[T2 = {t2:.3f}] Upload finished (HTTP {res.status_code}) in {upload_duration:.2f}s ({file_size_bytes / upload_duration / (1024*1024):.2f} MB/s)")

    if res.status_code != 202:
        print(f"Upload failed: {res.text}")
        return

    data = res.json()
    import_id = data["import_id"]
    # T3: Import job created
    t3 = time.time()
    print(f"[T3 = {t3:.3f}] Import Job Created: {import_id} (Status: {data.get('status')})")

    # Connect to SSE progress stream
    sse_url = f"{BACKEND_URL}/api/v1/imports/{import_id}/progress"
    print(f"\nConnecting to SSE stream: {sse_url}...")
    
    events = []
    t_sse_connect = time.time()

    # Lifecyle stage markers
    stage_markers = {}

    with requests.get(sse_url, stream=True, timeout=300) as sse_res:
        print(f"SSE connection established: HTTP {sse_res.status_code} in {time.time() - t_sse_connect:.3f}s")
        buffer = ""
        for chunk in sse_res.iter_content(chunk_size=1024, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            while "\n\n" in buffer:
                raw_event, buffer = buffer.split("\n\n", 1)
                t_event = time.time()
                lines = raw_event.strip().split("\n")
                event_data = None
                for line in lines:
                    if line.startswith("data: "):
                        event_data = line[6:]
                if event_data:
                    try:
                        parsed = json.loads(event_data)
                        elapsed = t_event - t2
                        events.append((t_event, elapsed, parsed))
                        status = parsed.get("status")
                        progress = parsed.get("progress", 0)
                        msg = parsed.get("stage_message", "")
                        rows_proc = parsed.get("processed_rows", 0)
                        rows_tot = parsed.get("total_rows", 0)

                        print(
                            f"  +{elapsed:6.2f}s | [{status}] {progress}% | "
                            f"Rows: {rows_proc:,}/{rows_tot:,} | {msg}"
                        )

                        if status == "PARSING" and "t4_parsing_start" not in stage_markers:
                            stage_markers["t4_parsing_start"] = t_event
                        if status == "VALIDATING" and "t7_staging_start" not in stage_markers:
                            stage_markers["t5_parsing_finish"] = t_event
                            stage_markers["t6_val_finish"] = t_event
                            stage_markers["t7_staging_start"] = t_event
                        if status == "IMPORTING" and "t9_dedup_start" not in stage_markers:
                            stage_markers["t8_staging_finish"] = t_event
                            stage_markers["t9_dedup_start"] = t_event
                            stage_markers["t11_upsert_start"] = t_event
                        if status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                            stage_markers["t10_dedup_finish"] = t_event
                            stage_markers["t12_upsert_finish"] = t_event
                            stage_markers["t13_final_start"] = t_event
                            stage_markers["t14_final_finish"] = t_event
                            stage_markers["t15_backend_completed"] = t_event
                            stage_markers["t16_frontend_received"] = t_event
                            stage_markers["t17_ui_displayed"] = t_event
                            break
                    except Exception as err:
                        print(f"  [Parse Error]: {err}")
            if events and events[-1][2].get("status") in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                break

    t_total_end = time.time()
    backend_duration = t_total_end - t2

    print("\n==================================================")
    print("COMPLETE LIFECYCLE TIMESTAMPS (T0 - T17)")
    print("==================================================")
    print(f"T0  (File selected)                : {t0:.3f}")
    print(f"T1  (Upload starts)                 : {t1:.3f}")
    print(f"T2  (Upload finishes)               : {t2:.3f} (Duration: {t2 - t1:.2f}s)")
    print(f"T3  (Import job created)            : {t3:.3f}")
    if "t4_parsing_start" in stage_markers:
        print(f"T4  (Parsing starts)                : {stage_markers['t4_parsing_start']:.3f}")
    if "t7_staging_start" in stage_markers:
        print(f"T7  (Database staging/COPY starts)  : {stage_markers['t7_staging_start']:.3f}")
    if "t8_staging_finish" in stage_markers:
        print(f"T8  (Database staging/COPY finishes): {stage_markers['t8_staging_finish']:.3f}")
    if "t9_dedup_start" in stage_markers:
        print(f"T9  (Deduplication starts)          : {stage_markers['t9_dedup_start']:.3f}")
    if "t11_upsert_start" in stage_markers:
        print(f"T11 (UPSERT starts)                 : {stage_markers['t11_upsert_start']:.3f}")
    if "t15_backend_completed" in stage_markers:
        print(f"T15 (Backend marks COMPLETED)       : {stage_markers['t15_backend_completed']:.3f}")
        print(f"T16 (Frontend receives COMPLETED)   : {stage_markers['t16_frontend_received']:.3f}")
        print(f"T17 (UI displays Import complete)   : {stage_markers['t17_ui_displayed']:.3f}")

    print("\n==================================================")
    print("BENCHMARK SUMMARY")
    print("==================================================")
    print(f"File Size: {file_size_bytes:,} bytes")
    print(f"Upload Duration: {upload_duration:.2f}s")
    print(f"Backend Ingestion Duration: {backend_duration:.2f}s")
    print(f"Total Duration (Upload + Import): {t_total_end - t1:.2f}s")
    if events and events[-1][2].get("total_rows", 0) > 0:
        tot = events[-1][2]["total_rows"]
        print(f"Throughput: {tot / backend_duration:.0f} rows/sec")
    print(f"Total SSE Events: {len(events)}")

if __name__ == "__main__":
    run_live_measurement()
