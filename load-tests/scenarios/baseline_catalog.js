import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

export const options = {
  stages: [
    { duration: '15s', target: 10 },
    { duration: '30s', target: 25 },
    { duration: '30s', target: 50 },
    { duration: '30s', target: 100 },
    { duration: '15s', target: 0 },
  ],
  thresholds: CONFIG.thresholds.standard,
};

export default function () {
  // 1. Fetch categories
  const catRes = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.categories}`);
  check(catRes, {
    'categories status is 200': (r) => r.status === 200,
    'categories has results': (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body) || (body.results && Array.isArray(body.results));
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.5);

  // 2. Fetch public product catalog feed (default freshness-ranked pagination)
  const prodRes = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=1`);
  check(prodRes, {
    'catalog list status is 200': (r) => r.status === 200,
    'catalog list returns products': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.results && body.results.length > 0;
      } catch (e) {
        return false;
      }
    },
  });

  sleep(1);
}
