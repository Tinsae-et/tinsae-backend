# Performance Test Report: PostgreSQL Trigram Search (Stage 2)

Date: 2026-09-14
Commit: 3c3cef5
Environment: Local Performance Environment (Target: Hetzner CPX31)
Tester: Automated CI/Perf Runner

## Test Execution
- **Scenario**: `scenarios/search_trgm.js` (Stage 2: Database / pg_trgm Search & Filtering)
- **Duration**: 2m 05s
- **Virtual Users (VUs)**: Stepped up to 120 VUs (15 → 35 → 70 → 120 VUs)
- **Dataset Size**: 10,000 products across 100 shops

## k6 Results

| Metric | Measured Value | SLA Target (Section 11) | Status |
| :--- | :--- | :--- | :--- |
| **Total Requests** | 4,780 | - | - |
| **Throughput (RPS)** | 38.1 requests/sec | - | - |
| **Checks Passed** | 97.59% (9,330 / 9,560) | 100% | ⚠️ WARN |
| **HTTP Error Rate** | **2.40%** (115 failed) | < 1.0% | ❌ FAIL (Threshold crossed) |
| **p50 Latency (med)** | **214.30 ms** | - | **PASS** |
| **p90 Latency** | **1.34 s** | - | - |
| **p95 Latency** | **2.76 s** | < 500 ms | ❌ FAIL (Threshold crossed) |
| **p99 Latency** | **3.97 s** | < 1,000 ms | ❌ FAIL (Threshold crossed) |
| **Max Latency** | 4.43 s | - | - |
| **Data Transferred** | 87 MB (689 kB/s) | - | - |

## Analysis & Bottleneck Identification (Guide Section 26)

### 1. The Bottleneck: Dev Runserver Queueing
- The test was executed against the default development container running Django's single-process `runserver` on 120 concurrent threads.
- In `runserver`, heavy CPU trigram searches (`pg_trgm`) and query ranking cause Python GIL lock contention. Threads back up in the socket backlog, causing 115 requests to time out or get dropped under 120 VUs.

### 2. Database & GIN Index Performance
- At low to moderate load (< 35 VUs), median latency was **~214 ms**, showing that PostgreSQL `pg_trgm` GIN indexes (`product_name_trgm`, `product_brand_trgm`) correctly resolve fuzzy matches.
- At peak concurrency (> 70 VUs), PostgreSQL execution queues backed up behind connection locks on the single-process server.

## Remediation & Next Actions
1. **Switch to Production Multi-Worker Server**: Run tests with `docker compose -f docker-compose.perf.yml up -d` (Gunicorn 4 workers + 2 threads) so concurrent requests are served across dedicated processes rather than fighting for Django's dev GIL.
2. **SearchLog Write Decoupling**: Currently, [`apply_search`](file:///home/sol/workspace/tinsae/tinsae-backend/catalog/services.py#L205) triggers a synchronous DB write to `catalog_searchlog`. At 120 concurrent VUs, inserting into `catalog_searchlog` competes with search read locks.
