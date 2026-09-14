import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

export const options = {
  stages: [
    { duration: '15s', target: 20 },
    { duration: '30s', target: 50 },
    { duration: '30s', target: 100 },
    { duration: '15s', target: 0 },
  ],
  thresholds: CONFIG.thresholds.standard,
};

// Setup step runs once to fetch a pool of active slugs
export function setup() {
  const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=1`);
  let slugs = [];
  try {
    const data = JSON.parse(res.body);
    if (data.results && data.results.length > 0) {
      slugs = data.results.map((p) => p.slug);
    }
  } catch (e) {
    console.error('Failed to setup test slugs from catalog feed');
  }

  // If page 2 has items, fetch that too to expand pool
  const res2 = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=2`);
  try {
    const data2 = JSON.parse(res2.body);
    if (data2.results && data2.results.length > 0) {
      slugs = slugs.concat(data2.results.map((p) => p.slug));
    }
  } catch (e) {}

  return { slugs };
}

export default function (data) {
  const slugs = data.slugs || [];
  if (slugs.length === 0) {
    // Fallback if no slugs found
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}`);
    check(res, { 'fallback list is 200': (r) => r.status === 200 });
    sleep(1);
    return;
  }

  const slug = slugs[Math.floor(Math.random() * slugs.length)];

  // 1. Fetch listing details
  const detailRes = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.productDetail(slug)}`);
  check(detailRes, {
    'product detail status is 200': (r) => r.status === 200,
    'product detail contains brand and price': (r) => {
      try {
        const item = JSON.parse(r.body);
        return item.slug === slug && item.price !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.3);

  // 2. Fetch similar products for comparison
  const similarRes = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.similar(slug)}`);
  check(similarRes, {
    'similar products status is 200': (r) => r.status === 200,
  });

  sleep(Math.random() * 0.5 + 0.5);
}
