import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

// Spike pattern matching Guide Section 17
export const options = {
  stages: [
    { duration: '20s', target: 30 },   // Warm-up / baseline
    { duration: '10s', target: 300 },  // Immediate spike!
    { duration: '30s', target: 300 },  // Sustained spike peak
    { duration: '10s', target: 30 },   // Recovery drop
    { duration: '30s', target: 30 },   // Observation post-spike
    { duration: '10s', target: 0 },    // Cool-down
  ],
  thresholds: CONFIG.thresholds.stress,
};

export default function () {
  const page = Math.floor(Math.random() * 3) + 1;
  const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=${page}`);

  check(res, {
    'spike response is 200': (r) => r.status === 200,
  });

  sleep(0.3);
}
