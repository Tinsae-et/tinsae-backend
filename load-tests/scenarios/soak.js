import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

// Soak test matching Guide Section 18 (Default: 30 minutes sustained, can be extended for 2-4 hours)
export const options = {
  stages: [
    { duration: '2m', target: 50 },    // Ramp up
    { duration: '30m', target: 50 },   // Steady sustained soak
    { duration: '2m', target: 0 },     // Ramp down
  ],
  thresholds: CONFIG.thresholds.standard,
};

export default function () {
  const page = Math.floor(Math.random() * 5) + 1;
  const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=${page}`);

  check(res, {
    'soak response status is 200': (r) => r.status === 200,
  });

  sleep(1);
}
