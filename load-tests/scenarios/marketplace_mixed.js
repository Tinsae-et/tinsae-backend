import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

const SEARCH_TERMS = [
  'iPhone',
  'Samsung',
  'Toyota',
  'Laptop',
  'Vitz',
  'Bole',
  'AirPods',
  'Dell',
];

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '1m', target: 50 },
    { duration: '1m', target: 100 },
    { duration: '1m', target: 150 },
    { duration: '30s', target: 0 },
  ],
  thresholds: CONFIG.thresholds.standard,
};

export function setup() {
  // Obtain authentication token for write requests
  const authPayload = JSON.stringify({
    username: 'perf_merchant',
    password: 'perfpass123',
  });
  const authRes = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, authPayload, {
    headers: { 'Content-Type': 'application/json' },
  });

  let token = null;
  let refreshToken = null;
  try {
    const authData = JSON.parse(authRes.body);
    token = authData.access;
    refreshToken = authData.refresh;
  } catch (e) {
    console.warn('Authentication setup failed - write tests will run unauthenticated');
  }

  // Fetch initial pool of products
  const prodRes = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=1`);
  let products = [];
  try {
    const prodData = JSON.parse(prodRes.body);
    if (prodData.results) {
      products = prodData.results.map((p) => ({
        slug: p.slug,
        shopSlug: p.shop ? p.shop.slug : null,
      }));
    }
  } catch (e) {}

  return { token, refreshToken, products };
}

export default function (data) {
  const roll = Math.random();
  const products = data.products || [];

  if (roll < 0.50) {
    // 50% Browse listings / categories
    const page = Math.floor(Math.random() * 5) + 1;
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?page=${page}`);
    check(res, { 'browse status is 200': (r) => r.status === 200 });

  } else if (roll < 0.70) {
    // 20% Search / filter
    const term = SEARCH_TERMS[Math.floor(Math.random() * SEARCH_TERMS.length)];
    const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.products}?q=${encodeURIComponent(term)}`);
    check(res, { 'search status is 200': (r) => r.status === 200 });

  } else if (roll < 0.85) {
    // 15% Listing details
    if (products.length > 0) {
      const p = products[Math.floor(Math.random() * products.length)];
      const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.productDetail(p.slug)}`);
      check(res, { 'detail status is 200': (r) => r.status === 200 });
    } else {
      const res = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.categories}`);
      check(res, { 'cat status is 200': (r) => r.status === 200 });
    }

  } else if (roll < 0.95) {
    // 10% Authentication / token refresh
    if (data.refreshToken) {
      const refreshPayload = JSON.stringify({ refresh: data.refreshToken });
      const res = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.tokenRefresh}`, refreshPayload, {
        headers: { 'Content-Type': 'application/json' },
      });
      // In rotating refresh tokens, some could be 200 or 401 if already rotated
      check(res, { 'refresh responds': (r) => r.status === 200 || r.status === 401 });
    } else {
      const authPayload = JSON.stringify({
        username: 'perf_merchant',
        password: 'perfpass123',
      });
      const res = http.post(`${CONFIG.baseUrl}${CONFIG.endpoints.authToken}`, authPayload, {
        headers: { 'Content-Type': 'application/json' },
      });
      check(res, { 'auth status is 200 or 400': (r) => r.status === 200 || r.status === 400 });
    }

  } else {
    // 5% Writes: Merchant product confirmation or state check
    if (data.token && products.length > 0) {
      const p = products[Math.floor(Math.random() * products.length)];
      if (p.shopSlug) {
        const confirmUrl = `${CONFIG.baseUrl}/api/v1/shops/${p.shopSlug}/products/${p.slug}/confirm/`;
        const res = http.post(confirmUrl, JSON.stringify({ is_available: true }), {
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${data.token}`,
          },
        });
        check(res, { 'confirm write completed': (r) => r.status === 200 || r.status === 403 || r.status === 404 });
      }
    } else {
      const statsRes = http.get(`${CONFIG.baseUrl}${CONFIG.endpoints.publicStats}`);
      check(statsRes, { 'public stats is 200': (r) => r.status === 200 });
    }
  }

  sleep(Math.random() * 0.5 + 0.5);
}
