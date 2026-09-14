import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

// Scenario designed to push ALL 8 host CPU cores to 80% - 95% sustained utilization
export const options = {
  stages: [
    { duration: '30s', target: 100 }, // Concurrency tier 1: 100 concurrent users
    { duration: '1m',  target: 100 }, // Steady 100 VUs
    { duration: '30s', target: 200 }, // Concurrency tier 2: 200 concurrent users
    { duration: '1m',  target: 200 }, // Steady 200 VUs
    { duration: '30s', target: 350 }, // Concurrency tier 3: 350 concurrent users
    { duration: '1m',  target: 350 }, // Steady 350 VUs (Max 8-core burn)
    { duration: '20s', target: 0 },   // Cool-down
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],
  },
};

const SEARCH_TERMS = [
  'Toyota Vitz Automatic Low Mileage',
  'Samsung Galaxy S24 Ultra 512GB Gray',
  'Apple MacBook Pro 14 M3 16GB',
  'Sony WH-1000XM5 Wireless Noise Canceling',
  'Dell XPS 13 Plus Core i7 16GB',
  'Toyota Corolla Executive 2021',
  'Infinix Note 30 Pro Variable Gold',
  'iPad Pro 12.9 M2 Wi-Fi Space Gray',
];

export function setup() {
  // Warm up authentication endpoint
  http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, JSON.stringify({
    username: 'perf_merchant',
    password: 'perfpass123',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
  return {};
}

export default function () {
  const roll = Math.random();

  if (roll < 0.45) {
    // 1. PostgreSQL multi-core CPU burn (Trigram fuzzy similarity & dynamic sorting)
    const term = SEARCH_TERMS[Math.floor(Math.random() * SEARCH_TERMS.length)];
    const filter = (Math.random() < 0.5) ? '&ordering=-price' : '&fresh=true';
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?q=${encodeURIComponent(term)}${filter}`);
    check(res, { 'search ok': (r) => r.status === 200 });

  } else if (roll < 0.85) {
    // 2. Python / Django multi-core CPU burn (PBKDF2 Password Hashing across 9 Gunicorn workers)
    const payload = JSON.stringify({
      username: 'perf_merchant',
      password: 'perfpass123',
    });
    const res = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    check(res, { 'auth ok': (r) => r.status === 200 || r.status === 400 });

  } else {
    // 3. Network & serialization throughput
    const page = Math.floor(Math.random() * 8) + 1;
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=${page}`);
    check(res, { 'browse ok': (r) => r.status === 200 });
  }

  // Ultra-tight pacing (10ms) to ensure continuous CPU core saturation
  sleep(0.01);
}
