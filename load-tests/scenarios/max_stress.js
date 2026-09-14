import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { CONFIG } from '../config/environments.js';

// Custom latency tracking for heavy CPU/Network tracks
const searchLatency = new Trend('search_latency');
const authLatency = new Trend('auth_cpu_latency');
const browseLatency = new Trend('network_browse_latency');

export const options = {
  stages: [
    { duration: '30s', target: 50 },   // Warmup
    { duration: '1m',  target: 150 },  // Moderate load
    { duration: '1m',  target: 350 },  // High load (Postgres & Django CPU ramp)
    { duration: '2m',  target: 700 },  // Maximum Stress (Saturation / Breaking Point)
    { duration: '1m',  target: 1000 }, // Absolute Peak Stress
    { duration: '30s', target: 0 },    // Cool-down
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'], // Allow under 5% errors at extreme breaking edge
    http_req_duration: ['p(95)<2500'],
  },
};

// Complex search queries to force heavy PostgreSQL CPU & trigram calculations
const HEAVY_SEARCH_TERMS = [
  'Toyota Vitz Automatic Low Mileage',
  'Samsung Galaxy S24 Ultra 512GB Gray',
  'Apple MacBook Pro 14 M3 16GB 512GB',
  'Sony WH-1000XM5 Wireless Noise Canceling',
  'Dell XPS 13 Plus Core i7 16GB SSD',
  'Infinix Note 30 Pro Variable Gold',
  'Toyota Corolla Executive 2021 Bole',
  'iPad Pro 12.9 M2 Wi-Fi Space Gray',
];

export function setup() {
  // Ensure the perf_merchant exists or fallback user can be authenticated
  const res = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, JSON.stringify({
    username: 'perf_merchant',
    password: 'perfpass123',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });

  let token = null;
  try {
    token = JSON.parse(res.body).access;
  } catch (e) {}

  // Fetch product slugs for details testing
  let slugs = [];
  const prodRes = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=1`);
  try {
    const data = JSON.parse(prodRes.body);
    if (data.results) slugs = data.results.map((p) => p.slug);
  } catch (e) {}

  return { token, slugs };
}

export default function (data) {
  const roll = Math.random();

  if (roll < 0.45) {
    // =========================================================================
    // 1. HEAVY NETWORK LOAD: Paginated Catalog Feeds & Large Serialized Payloads
    // =========================================================================
    const page = Math.floor(Math.random() * 10) + 1;
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=${page}`);
    browseLatency.add(res.timings.duration);

    check(res, {
      'browse status is 200': (r) => r.status === 200,
      'payload received': (r) => r.body && r.body.length > 200,
    });

  } else if (roll < 0.80) {
    // =========================================================================
    // 2. HEAVY DATABASE & POSTGRES CPU: Multi-term Trigram Similarity & Ordering
    // =========================================================================
    const term = HEAVY_SEARCH_TERMS[Math.floor(Math.random() * HEAVY_SEARCH_TERMS.length)];
    const filterRoll = Math.random();

    let url = `${CONFIG.baseUrl}${CONFIG.endpoints.products}?q=${encodeURIComponent(term)}`;
    if (filterRoll < 0.33) {
      url += '&ordering=-price&min_price=10000';
    } else if (filterRoll < 0.66) {
      url += '&ordering=-last_confirmed_at&fresh=true';
    } else {
      url += '&verified_only=true';
    }

    const res = http.get(url);
    searchLatency.add(res.timings.duration);

    check(res, {
      'search status is 200': (r) => r.status === 200,
    });

  } else if (roll < 0.95) {
    // =========================================================================
    // 3. HEAVY DJANGO CPU: PBKDF2 Password Hashing (Crypto CPU intensive)
    // =========================================================================
    const authPayload = JSON.stringify({
      username: 'perf_merchant',
      password: 'perfpass123',
    });

    const res = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, authPayload, {
      headers: { 'Content-Type': 'application/json' },
    });
    authLatency.add(res.timings.duration);

    check(res, {
      'auth status is 200 or 400': (r) => r.status === 200 || r.status === 400,
    });

  } else {
    // =========================================================================
    // 4. DEEP DETAILS & RELATION JOINS
    // =========================================================================
    const slugs = data.slugs || [];
    if (slugs.length > 0) {
      const slug = slugs[Math.floor(Math.random() * slugs.length)];
      const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.productDetail(slug)}`);
      check(res, { 'detail status is 200': (r) => r.status === 200 });
    }
  }

  // Very short pacing to generate intense throughput and sustained socket pressure
  sleep(0.05);
}
