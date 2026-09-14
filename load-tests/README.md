# Tinsae Marketplace — Performance Engineering & Load Testing Suite

This suite implements the methodology defined in the [Marketplace Load Testing & Performance Engineering Guide](/home/sol/Downloads/Marketplace%20Load%20Testing%20&%20Performance%20Engineering%20Guide.md).

It provides reproducible, actionable load testing across all key marketplace user journeys and database query paths.

---

## 1. Quick Start

### Step 1: Generate Realistic Benchmark Data
Generate 10,000+ realistic products, categories, and shops to properly exercise PostgreSQL `pg_trgm` GIN indexes and sorting:

```bash
# In your backend container or local virtual environment:
python manage.py generate_perf_data --count 15000 --shops 100
```

*(For full-scale search benchmarks matching Section 4 of the guide, pass `--count 100000`)*

### Step 2: Start the Production-Like Stack (Gunicorn + CPX31 Limits)
Do **not** benchmark against Django's development `runserver`. Launch the production-like multi-worker stack:

```bash
docker compose -f docker-compose.perf.yml up -d --build
```

This starts:
- **Gunicorn**: 4 workers + 2 threads (`CONN_MAX_AGE=60`, throttles bypassed via `config.settings.perf`).
- **Resource Constraints**: Capped to emulate a **Hetzner CPX31** (4 vCPU / 8 GB RAM target).

### Step 3: Run Test Scenarios
You do **not** need `k6` installed locally. The included runner automatically falls back to Docker (`grafana/k6`):

```bash
# Stage 1: Endpoint baseline
./load-tests/run-k6.sh scenarios/baseline_catalog.js

# Stage 2: Database / pg_trgm search and filter test
./load-tests/run-k6.sh scenarios/search_trgm.js

# Stage 3: Product detail & similarity test
./load-tests/run-k6.sh scenarios/detail.js

# Stage 4: Realistic mixed marketplace workload (50% browse, 20% search, 15% detail, 10% auth, 5% write)
./load-tests/run-k6.sh scenarios/marketplace_mixed.js

# Stage 5: Progressive stress test (stepping to find max sustainable RPS)
./load-tests/run-k6.sh scenarios/stress.js

# Stage 6: Sudden traffic spike test
./load-tests/run-k6.sh scenarios/spike.js

# Stage 7: Sustained soak test (memory & connection leaks)
./load-tests/run-k6.sh scenarios/soak.js
```

---

## 2. Directory Structure

```text
load-tests/
├── README.md                    # This guide
├── run-k6.sh                    # Dual-mode runner (host binary or Docker)
├── config/
│   └── environments.js          # URLs, endpoints, and baseline thresholds
├── scenarios/
│   ├── baseline_catalog.js      # Stage 1: Categories & paginated catalog
│   ├── search_trgm.js           # Stage 2: Trigram fuzzy search, filters, ordering
│   ├── detail.js                # Stage 3: Listing details & similar products
│   ├── marketplace_mixed.js     # Stage 4: 50% browse, 20% search, 15% detail, 10% auth, 5% write
│   ├── stress.js                # Stage 5: Progressive stepping (25 -> 500 RPS)
│   ├── spike.js                 # Stage 6: 30 -> 300 -> 30 VU traffic spike
│   └── soak.js                  # Stage 7: Sustained 30+ min endurance test
└── reports/                     # Saved benchmark results and run logs
```

---

## 3. Real-Time Observability During Runs

Never evaluate RPS without monitoring the infrastructure. While a scenario is running, monitor in separate terminal panes:

### Docker Container Utilization
```bash
docker stats
```
Watch `tinsae-backend-1` and `tinsae-backend-db-1` for CPU and memory saturation.

### PostgreSQL Query Diagnostics
When testing search or detail endpoints:
```bash
docker exec -it tinsae-backend-db-1 psql -U tinsae -d tinsae -c "
SELECT query, calls, total_exec_time, mean_exec_time
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 5;
"
```

---

## 4. Test Report Template (Per Section 27 of Guide)

Save every significant test run to `load-tests/reports/YYYY-MM-DD_<scenario>_<commit>.md`:

```markdown
# Performance Test Report

Date: YYYY-MM-DD
Git Commit: $(git rev-parse --short HEAD)
Environment: Local Docker (Hetzner CPX31 emulated: 4 vCPU, 8 GB RAM)
Scenario: <scenario_name>

## k6 Results
- Total Requests:
- Sustained RPS:
- p50 Latency:
- p90 Latency:
- p95 Latency:
- p99 Latency:
- HTTP Error Rate:

## Infrastructure Metrics
- Django / Gunicorn CPU:
- PostgreSQL CPU:
- PostgreSQL Active Connections:
- Host RAM / Swap:

## Conclusion
- Result: PASS / FAIL
- Max Sustainable RPS:
- Primary Bottleneck (Django CPU / Postgres CPU / Locks / I/O):
- Recommended Remediation:
```
