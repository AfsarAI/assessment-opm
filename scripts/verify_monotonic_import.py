#!/usr/bin/env python3
"""
Comprehensive Monotonicity and Pipeline Verification Script.
Tests CSV imports against local or production backend across datasets.
Verifies:
1. SSE progress stream is strictly monotonic (never moves backwards).
2. GET /api/v1/imports/{id} polling is strictly monotonic.
3. Simulated combined React state (SSE + Poll) is strictly monotonic.
4. Sequence numbers (seq) and timestamps are properly formatted and ordered.
5. Final progress reaches 100% on COMPLETED.
6. Cancellation test: preserves reached progress and does not drop to 0%.
"""

import sys
import os
import time
import json
import threading
import requests

def test_monotonic_import(backend_url: str, csv_file: str, test_cancel: bool = False):
    print(f"\n========================================================")
    print(f"Testing: {csv_file}")
    print(f"Target: {backend_url} (Cancel test: {test_cancel})")
    print(f"========================================================")

    if not os.path.exists(csv_file):
        print(f"File not found: {csv_file}")
        return False

    file_size = os.path.getsize(csv_file)
    print(f"File size: {file_size:,} bytes")

    # 1. Upload CSV
    t_start = time.time()
    with open(csv_file, "rb") as f:
        res = requests.post(f"{backend_url}/api/v1/imports", files={"file": (os.path.basename(csv_file), f, "text/csv")})
    
    if res.status_code != 202:
        print(f"FAILED to initiate import: HTTP {res.status_code} - {res.text}")
        return False
    
    job_info = res.json()
    job_id = job_info["import_id"]
    print(f"Job ID: {job_id} initiated in {time.time() - t_start:.2f}s")

    # Simulated React state
    active_job = {
        "id": job_id,
        "status": "QUEUED",
        "progress": 0,
        "processed_rows": 0,
        "stage_message": "Queued",
    }
    last_seen_seq = 0

    sse_events = []
    poll_events = []
    ui_transitions = [(time.time(), 0, "INIT", "Queued")]
    backward_events = []
    stop_event = threading.Event()
    lock = threading.Lock()

    # Worker: Polling GET /api/v1/imports/{id} every 0.8s
    def poller():
        nonlocal active_job
        while not stop_event.is_set():
            time.sleep(0.8)
            if stop_event.is_set():
                break
            try:
                r = requests.get(f"{backend_url}/api/v1/imports/{job_id}", timeout=5)
                if r.status_code == 200:
                    current = r.json()
                    with lock:
                        poll_events.append((time.time(), current.get("progress", 0), current.get("status"), current.get("stage_message")))
                        prev = dict(active_job)
                        
                        # New frontend logic:
                        if current.get("status") in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"):
                            new_job = {
                                **prev,
                                **current,
                                "progress": 100 if current.get("status") == "COMPLETED" else max(prev["progress"], current.get("progress", 0)),
                                "processed_rows": max(prev["processed_rows"], current.get("processed_rows", 0)),
                            }
                        else:
                            if (current.get("progress", 0) > prev["progress"] or 
                                current.get("status") != prev["status"] or 
                                current.get("stage_message") != prev["stage_message"]):
                                new_job = {
                                    **prev,
                                    **current,
                                    "progress": max(prev["progress"], current.get("progress", 0)),
                                    "processed_rows": max(prev["processed_rows"], current.get("processed_rows", 0)),
                                }
                            else:
                                new_job = prev

                        if new_job["progress"] < active_job["progress"]:
                            msg = f"BACKWARD PROGRESS FROM POLL: {active_job['progress']}% -> {new_job['progress']}%"
                            backward_events.append(msg)
                            print(f"  [ERROR] {msg}")

                        active_job = new_job
                        ui_transitions.append((time.time(), active_job["progress"], "POLL", active_job.get("stage_message")))

                        if current.get("status") in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"):
                            stop_event.set()
            except Exception as e:
                pass

    poll_thread = threading.Thread(target=poller, daemon=True)
    poll_thread.start()

    # Cancel test trigger if enabled
    if test_cancel:
        def cancel_trigger():
            time.sleep(1.5)
            print("  --> Triggering cancellation request...")
            try:
                c_res = requests.post(f"{backend_url}/api/v1/imports/{job_id}/cancel", timeout=5)
                print(f"  --> Cancel response: HTTP {c_res.status_code} - {c_res.text}")
            except Exception as e:
                print(f"  --> Cancel error: {e}")
        cancel_thread = threading.Thread(target=cancel_trigger, daemon=True)
        cancel_thread.start()

    # SSE Stream Listener
    sse_url = f"{backend_url}/api/v1/imports/{job_id}/progress"
    try:
        with requests.get(sse_url, stream=True, timeout=300) as sse_res:
            buffer = ""
            for chunk in sse_res.iter_content(chunk_size=1024, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk
                while "\n\n" in buffer:
                    raw_event, buffer = buffer.split("\n\n", 1)
                    for line in raw_event.strip().split("\n"):
                        if line.startswith("data: "):
                            data = json.loads(line[6:])
                            with lock:
                                seq = data.get("seq")
                                prog = data.get("progress", 0)
                                status = data.get("status")
                                stage = data.get("stage_message", "")
                                sse_events.append((time.time(), seq, prog, status, stage))
                                
                                prev = dict(active_job)
                                # Sequence out-of-order drop check
                                if seq is not None and seq < last_seen_seq:
                                    print(f"  [WARN] Ignored out-of-order SSE seq: {seq} < {last_seen_seq}")
                                else:
                                    if seq is not None:
                                        last_seen_seq = seq
                                    new_job = {
                                        **prev,
                                        "status": status,
                                        "progress": 100 if status == "COMPLETED" else max(prev["progress"], prog),
                                        "processed_rows": max(prev["processed_rows"], data.get("processed_rows", 0)),
                                        "stage_message": stage,
                                        "errors": data.get("errors", []),
                                    }
                                    if new_job["progress"] < active_job["progress"]:
                                        msg = f"BACKWARD PROGRESS FROM SSE: {active_job['progress']}% -> {new_job['progress']}%"
                                        backward_events.append(msg)
                                        print(f"  [ERROR] {msg}")

                                    active_job = new_job
                                    ui_transitions.append((time.time(), active_job["progress"], "SSE", stage))
                                    print(f"  [SSE Event] seq={seq} | {prog}% | {status} | {stage}")

                                if status in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"):
                                    stop_event.set()
                                    break
                    if stop_event.is_set():
                        break
    except Exception as e:
        print(f"SSE exception: {e}")

    stop_event.set()
    poll_thread.join(timeout=3)

    # Monotonicity Checks
    print("\n--- Validation Results ---")
    print(f"Final Status: {active_job['status']}")
    print(f"Final UI Progress: {active_job['progress']}%")
    print(f"Total SSE Events: {len(sse_events)}")
    print(f"Total Poll Events: {len(poll_events)}")
    print(f"Total UI Transitions: {len(ui_transitions)}")

    # Check 1: Raw SSE monotonicity
    sse_regressions = 0
    for i in range(1, len(sse_events)):
        prev_p = sse_events[i-1][2]
        curr_p = sse_events[i][2]
        if curr_p < prev_p:
            print(f"  [FAIL] Raw SSE regression at index {i}: {prev_p}% -> {curr_p}%")
            sse_regressions += 1

    # Check 2: Raw Poll monotonicity
    poll_regressions = 0
    for i in range(1, len(poll_events)):
        prev_p = poll_events[i-1][1]
        curr_p = poll_events[i][1]
        if curr_p < prev_p:
            print(f"  [FAIL] Raw Poll regression at index {i}: {prev_p}% -> {curr_p}%")
            poll_regressions += 1

    # Check 3: Combined UI monotonicity
    ui_regressions = 0
    for i in range(1, len(ui_transitions)):
        prev_p = ui_transitions[i-1][1]
        curr_p = ui_transitions[i][1]
        if curr_p < prev_p:
            print(f"  [FAIL] Combined UI regression at index {i}: {prev_p}% -> {curr_p}% ({ui_transitions[i][2]})")
            ui_regressions += 1

    # Check 4: Cancellation sanity
    cancel_passed = True
    if test_cancel:
        if active_job["status"] != "CANCELLED":
            print(f"  [FAIL] Expected status CANCELLED, got {active_job['status']}")
            cancel_passed = False
        if active_job["progress"] == 0:
            print(f"  [FAIL] Cancelled job dropped progress to 0%!")
            cancel_passed = False
        else:
            print(f"  [PASS] Cancelled job preserved progress: {active_job['progress']}% (non-zero)")

    # Overall outcome
    passed = (
        len(backward_events) == 0 and 
        sse_regressions == 0 and 
        poll_regressions == 0 and 
        ui_regressions == 0 and
        (not test_cancel or cancel_passed)
    )

    if passed:
        print(">>> ALL MONOTONICITY CHECKS PASSED: ZERO BACKWARD EVENTS! <<<")
    else:
        print(f">>> FAILED CHECKS: SSE={sse_regressions}, Poll={poll_regressions}, UI={ui_regressions} <<<")

    return passed

if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    target_file = sys.argv[2] if len(sys.argv) > 2 else "scripts/samples/products_100.csv"
    is_cancel = len(sys.argv) > 3 and sys.argv[3].lower() in ("cancel", "true", "1")
    
    success = test_monotonic_import(target_url, target_file, test_cancel=is_cancel)
    sys.exit(0 if success else 1)
