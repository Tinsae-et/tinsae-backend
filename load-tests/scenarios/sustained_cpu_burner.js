import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

// Sustained Heavy CPU Load Scenario
// Designed to keep Django and PostgreSQL container CPU between 80% - 95% for several minutes
export const options = {
  stages: [
    { duration: '30s', target: 80 },   // Warm-up ramp
    { duration: '4m',  target: 160 },  // Sustained steady CPU plateau (4 full minutes!)
    { duration: '20s', target: 0 },    // Cool-down
  ],
  thresholds: {
    // We expect higher response times under 80-90% CPU saturation
    http_req_duration: ['p(95)<5000'],
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
  'iPad Pro 12.9 M2 Wi-Fi',
];

export function setup() {
  // Ensure perf_merchant credentials exist
  const res = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, JSON.stringify({
    username: 'perf_merchant',
    password: 'perfpass123',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
  return {};
}

export default function () {
  const roll = Math.random();

  if (roll < 0.50) {
    // 1. PostgreSQL CPU: Multi-term trigram calculations & similarity ranking
    const term = SEARCH_TERMS[Math.floor(Math.random() * SEARCH_TERMS.length)];
    const filter = (Math.random() < 0.5) ? '&ordering=-price' : '&fresh=true';
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?q=${encodeURIComponent(term)}${filter}`);
    check(res, { 'search ok': (r) => r.status === 200 });

  } else if (roll < 0.85) {
    // 2. Django CPU: PBKDF2 Password Hashing (Crypto-heavy Python CPU burner)
    const payload = JSON.stringify({
      username: 'perf_merchant',
      password: 'perfpass123',
    });
    const res = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    check(res, { 'auth ok': (r) => r.status === 200 || r.status === 400 });

  } else {
    // 3. Network & Serialization CPU: Large product listings
    const page = Math.floor(Math.random() * 8) + 1;
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=${page}`);
    check(res, { 'browse ok': (r) => r.status === 200 });
  }

  // Minimal sleep (15ms) so worker threads are continuously saturated
  sleep(0.015);
}
