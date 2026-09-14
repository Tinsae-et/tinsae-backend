# Performance Test Report: Baseline Catalog

Date: 2026-09-14
Commit: 3c3cef5
Environment: Local Performance Environment (Target: Hetzner CPX31)
Tester: Automated CI/Perf Runner

## Test Execution
- **Scenario**: `scenarios/baseline_catalog.js` (Stage 1: Categories & Paginated Product Feed)
- **Duration**: 2m 00s
- **Virtual Users (VUs)**: Stepped up from 10 → 25 → 50 → 100 VUs
- **Dataset Size**: 10,000 products across 100 shops

## k6 Results

| Metric | Measured Value | SLA Target (Section 11) | Status |
| :--- | :--- | :--- | :--- |
| **Total Requests** | 5,850 | - | - |
| **Throughput (RPS)** | 48.5 requests/sec | - | - |
| **Checks Passed** | 100.00% (11,700 / 11,700) | 100% | PASS |
| **HTTP Error Rate** | **0.00%** (0 failed) | < 1.0% | **PASS** |
| **p50 Latency (med)** | **32.73 ms** | - | **PASS** |
| **p90 Latency** | **171.75 ms** | - | **PASS** |
| **p95 Latency** | **252.29 ms** | < 500 ms | **PASS** |
| **p99 Latency** | **394.42 ms** | < 1,000 ms | **PASS** |
| **Max Latency** | 752.05 ms | - | - |
| **Data Transferred** | 40 MB (335 kB/s) | - | - |

## Infrastructure & Database Behavior
- **PostgreSQL**: Stable connection pool with `CONN_MAX_AGE=60`. Queries completed under 15ms.
- **Django**: Gunicorn handled concurrent requests without dropping iterations.
- **Memory Pressure**: No unbounded memory growth observed.

## Conclusion & Verdict
- **Result**: **PASS**
- **Sustained Baseline**: Up to 100 concurrent Virtual Users serving ~50 RPS without any errors.
- **p95 Latency**: 252ms (well below the 500ms SLA target).
- **Next Stage**: Run Stage 2 (`scenarios/search_trgm.js`) to evaluate PostgreSQL `pg_trgm` GIN index behavior under fuzzy search traffic.
