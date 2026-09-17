import time
import json
import sys
import requests

BACKEND_URL = "https://opm-backend-p1i8.onrender.com"
CSV_FILE = "products.csv"

def run_live_measurement():
    print(f"==================================================")
    print(f"LIVE PRODUCTION MEASUREMENT: {BACKEND_URL}")
    print(f"File: {CSV_FILE}")
    print(f"==================================================")

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

    # 2. Upload products.csv and measure exact upload time
    print(f"\n[T1] Starting upload of {CSV_FILE} (87.2 MB) to {BACKEND_URL}/api/v1/imports...")
    t_upload_start = time.time()
    
    with open(CSV_FILE, "rb") as f:
        files = {"file": (CSV_FILE, f, "text/csv")}
        res = requests.post(f"{BACKEND_URL}/api/v1/imports", files=files, timeout=300)

    t_upload_end = time.time()
    upload_duration = t_upload_end - t_upload_start
    print(f"[T2] Upload finished with HTTP {res.status_code} in {upload_duration:.2f}s")
    
    if res.status_code != 202:
        print(f"Upload failed: {res.text}")
        return

    data = res.json()
    import_id = data["import_id"]
    print(f"[T3] Import Job Created: {import_id}")

    # 3. Connect to SSE progress stream
    sse_url = f"{BACKEND_URL}/api/v1/imports/{import_id}/progress"
    print(f"\n[T4] Connecting to SSE stream: {sse_url}...")
    t_sse_start = time.time()
    
    events = []
    
    with requests.get(sse_url, stream=True, timeout=300) as sse_res:
        print(f"SSE connection established: HTTP {sse_res.status_code}")
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
                        elapsed_since_upload = t_event - t_upload_end
                        events.append((t_event, elapsed_since_upload, parsed))
                        print(
                            f"  +{elapsed_since_upload:6.2f}s | "
                            f"[{parsed.get('status')}] {parsed.get('progress')}% | "
                            f"Rows: {parsed.get('processed_rows'):,}/{parsed.get('total_rows'):,} | "
                            f"{parsed.get('stage_message')}"
                        )
                        if parsed.get("status") in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                            break
                    except Exception as err:
                        print(f"  [Parse Error] {line}: {err}")
            if events and events[-1][2].get("status") in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
                break

    t_total_end = time.time()
    backend_processing_duration = t_total_end - t_upload_end

    print(f"\n==================================================")
    print(f"MEASUREMENT BREAKDOWN SUMMARY")
    print(f"==================================================")
    print(f"Upload Duration (network): {upload_duration:.2f}s")
    print(f"Backend Processing Duration: {backend_processing_duration:.2f}s")
    print(f"Total End-to-End Time: {t_total_end - t_upload_start:.2f}s")
    print(f"Number of SSE events captured: {len(events)}")
    
    # Analyze stages
    stage_durations = {}
    prev_time = t_upload_end
    for t_ev, elap, ev in events:
        stage = ev.get("stage_message", "")
        prog = ev.get("progress", 0)
        key = f"{prog}%: {stage}"
        if key not in stage_durations:
            stage_durations[key] = t_ev - prev_time
            prev_time = t_ev

    print("\nStage Timings:")
    for k, v in stage_durations.items():
        print(f"  - {k}: {v:.2f}s")

if __name__ == "__main__":
    run_live_measurement()
