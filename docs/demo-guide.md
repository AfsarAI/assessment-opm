# Assessment OPM: Evaluator Demonstration Guide

This guide provides a step-by-step walkthrough for an evaluator assessing the system's compliance with all 21 assessment requirements.

---

## 1. System Health & Dashboard Overview

1. Open the application URL in your browser:
   - **Frontend UI**: [http://localhost:3000](http://localhost:3000) (or your deployed URL)
   - **Interactive API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
2. Verify the top header shows:
   - Green indicator: **System Ready** (database & Redis operational)
   - Stat Cards: Total Products, Active Count, Inactive Count, Total Imports

---

## 2. Real-Time CSV Ingestion (SSE Telemetry)

1. Click on the **Import CSV** tab in the top navigation.
2. Select or drag-and-drop a CSV file:
   - For an immediate 100-row demo: use `scripts/samples/products_100.csv` (<0.3s).
   - For a 10,000-row demo: use `scripts/samples/products_10000.csv` (~1.5s).
   - For the full 500,000-row benchmark: use `products.csv` (87.2 MB, ~24s).
3. Click **Start Ingestion**:
   - The API immediately returns `HTTP 202 Accepted` in <15ms.
   - The UI displays an animated progress bar connected to the live **Server-Sent Events (SSE)** stream.
   - Watch the real-time stage transitions:
     1. `[PARSING]` — Validating header and column structure.
     2. `[VALIDATING]` — Streaming 25,000-row chunks into PostgreSQL `UNLOGGED` staging table.
     3. `[IMPORTING]` — Executing set-based SQL deduplication (`DISTINCT ON (LOWER(sku))`) and atomic `UPSERT`.
     4. `[COMPLETED]` — Final telemetry event (processed rows: 500,000, failed: 0).
4. Inspect the **Recent Ingestion Jobs** table below to see historical import run durations and status.

---

## 3. Product Management, Filtering & Pagination

1. Click on the **Products** tab in the navigation.
2. Notice the paginated catalog table:
   - Pagination controls at the bottom navigate seamlessly across pages with limit selectors (25, 50, 100).
3. **Search by SKU**:
   - Type `ability-see` in the SKU filter box.
   - Notice the table filters instantly, demonstrating sub-second indexing over 466,693 rows.
4. **Filter by Status**:
   - Click the **Active** button: shows only active products.
   - Click the **Inactive** button: shows only deactivated products.
5. **Create a Product**:
   - Click **+ Add Product**.
   - Enter SKU: `DEMO-TEST-001`, Name: `Demo Evaluator Product`.
   - Click **Save Product**: product appears immediately.
6. **Case-Insensitive Uniqueness Enforcement**:
   - Click **+ Add Product** again.
   - Enter lowercase SKU: `demo-test-001`.
   - Click **Save Product**: the API rejects the insert with `409 Conflict: Product with SKU 'demo-test-001' already exists`.

---

## 4. Active Status Preservation Across Re-Imports

1. In the Products table, find a product (or product ID `1`) and toggle its status to **Inactive** (`active = false`).
2. Re-import a CSV containing that exact same SKU.
3. Refresh or search for the product:
   - Notice that the product name and description are updated, but `active` **remains false**!
   - This proves the application-owned active flag is strictly preserved.

---

## 5. Webhook System & SSRF Security

1. Click on the **Webhooks** tab.
2. Click **+ Add Webhook**:
   - Enter URL: `http://169.254.169.254/latest/meta-data` (Cloud Metadata SSRF test)
   - Click **Register Webhook**: The system immediately rejects the URL:
     `SSRF validation failed: Destination IP 169.254.169.254 falls within a restricted/private network range`.
   - Repeat with `http://127.0.0.1:8000` or `http://192.168.1.1`: rejected.
3. Now enter a valid public endpoint:
   - URL: `https://httpbin.org/post`
   - Secret: `demo-secret-key`
   - Events: Check `import.completed`
   - Click **Register Webhook**: registered successfully.
4. Click **Test Ping** on the registered webhook card:
   - The interactive tester sends a live request, measures the round-trip response time (e.g. `1,250 ms`), verifies HTTP 200, and validates the HMAC-SHA256 signature header.

---

## 6. Safe Bulk Deletion

1. On the Products tab, click **Delete All Products**.
2. A modal dialog appears requiring explicit typed confirmation (`DELETE ALL`).
3. Confirming executes a PostgreSQL table truncation in <10 ms, clearing the catalog cleanly.
