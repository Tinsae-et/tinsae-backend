import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

// Progression matching Guide Section 15
export const options = {
  stages: [
    { duration: '30s', target: 25 },
    { duration: '30s', target: 50 },
    { duration: '30s', target: 100 },
    { duration: '45s', target: 200 },
    { duration: '45s', target: 300 },
    { duration: '45s', target: 500 },
    { duration: '30s', target: 0 },
  ],
  thresholds: CONFIG.thresholds.stress,
};

const TERMS = ['Toyota', 'iPhone', 'Samsung', 'Laptop', 'Bole'];

export default function () {
  const roll = Math.random();

  if (roll < 0.65) {
    // Browse page with fresh ordering
    const page = Math.floor(Math.random() * 4) + 1;
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=${page}&ordering=-last_confirmed_at`);
    check(res, { 'status is 200': (r) => r.status === 200 });
  } else {
    // Search query
    const term = TERMS[Math.floor(Math.random() * TERMS.length)];
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?q=${encodeURIComponent(term)}`);
    check(res, { 'status is 200': (r) => r.status === 200 });
  }

  // Small pacing to avoid unrealistic unthrottled loop
  sleep(0.2);
}
