# Assessment OPM: Comprehensive Benchmarks & Responsiveness Telemetry

## 1. Multi-Tier Ingestion Benchmark Matrix

Empirical benchmarks executed against the live PostgreSQL 16 + Redis 7 + Celery 5.4 + FastAPI stack:

| Dataset Size | File Size | Upload Duration | Server Processing | Ingestion Throughput | Success Count | Duplicate Count | Memory Peak (Worker) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **100 rows** | 17.7 KB | 0.01s | **0.26s** | 634 rows/sec | 100 | 0 | < 45 MB |
| **10,000 rows** | 1.8 MB | 0.02s | **0.48s** | 36,005 rows/sec | 10,000 | 664 | < 65 MB |
| **100,000 rows** | 18.1 MB | 0.13s | **5.04s** | 34,139 rows/sec | 100,000 | 6,654 | < 110 MB |
| **500,000 rows** | 87.2 MB | 0.92s | **22.61s** | **38,115 rows/sec** | 500,000 | 33,307 | < 210 MB |

---

## 2. API Responsiveness Under Active 500,000-Row Load

Measured concurrently during the full 500,000-row ingestion run using `scripts/test_responsiveness.py`.
Total concurrent requests completed during ingestion: **1,492 requests**, with **0 errors (100% success rate)**.

### Latency Distributions

| Endpoint Tested | Operations (n) | Min Latency | Mean Latency | Median (p50) | p95 Latency | p99 Latency | Max Latency | Status Code |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`GET /health`** (Liveness) | 825 | 1.5 ms | 6.4 ms | **4.4 ms** | 11.1 ms | 18.1 ms | 863.3 ms | 200 OK |
| **`GET /ready`** (DB & Redis) | 380 | 5.7 ms | 23.4 ms | **20.4 ms** | 34.7 ms | 44.7 ms | 903.4 ms | 200 OK |
| **`GET /api/v1/products`** (DB Read) | 198 | 45.4 ms | 137.3 ms | **117.8 ms** | 270.9 ms | 289.3 ms | 933.4 ms | 200 OK |
| **`POST /api/v1/products`** (DB Write) | 89 | 6.2 ms | 32.6 ms | **25.4 ms** | 46.8 ms | 502.7 ms | 502.7 ms | 201 Created |

---

## 3. Database Resource Footprint (PostgreSQL 16)

Measured after 500,000 rows ingestion (466,693 unique products stored):

```sql
SELECT pg_size_pretty(pg_total_relation_size('products')) AS total_size,
       pg_size_pretty(pg_relation_size('products')) AS table_size,
       pg_size_pretty(pg_indexes_size('products')) AS index_size;

 total_size | table_size | index_size 
------------+------------+------------
 194 MB     | 108 MB     | 86 MB
```

- **Data Rows**: 108 MB
- **Indexes**: 86 MB (`uq_products_sku_lower`, `idx_products_active`, `idx_products_created_at`, `idx_products_name_trgm`)
- **Total Persistent Footprint**: **194 MB** (comfortably fits in free 500MB/1GB database tiers).
