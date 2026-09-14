import http from 'k6/http';
import { check, sleep } from 'k6';
import { CONFIG } from '../config/environments.js';

// Search terms matching Section 13 of the Guide + realistic Ethiopian marketplace patterns
const SEARCH_TERMS = [
  'Toyota',
  'Toyota Vitz',
  'Toyota Vit',
  'iPhone',
  'iPhone 14',
  'Samsung',
  'Samsung Ultra',
  'Laptop',
  'MacBook',
  'Dell XPS',
  'AirPods',
  'Bole',
  'Addis',
  'Clean',
];

const CATEGORY_SLUGS = ['phones', 'laptops', 'vehicles', 'audio', 'tablets'];

export const options = {
  stages: [
    { duration: '20s', target: 15 },
    { duration: '30s', target: 35 },
    { duration: '30s', target: 70 },
    { duration: '30s', target: 120 },
    { duration: '15s', target: 0 },
  ],
  thresholds: CONFIG.thresholds.standard,
};

export default function () {
  const term = SEARCH_TERMS[Math.floor(Math.random() * SEARCH_TERMS.length)];
  const roll = Math.random();

  let url = `${CONFIG.baseUrl}${CONFIG.endpoints.products}?q=${encodeURIComponent(term)}`;

  // 40% of requests combine search with filters/sorting
  if (roll < 0.20) {
    const cat = CATEGORY_SLUGS[Math.floor(Math.random() * CATEGORY_SLUGS.length)];
    url += `&category=${cat}`;
  } else if (roll < 0.35) {
    url += '&fresh=true';
  } else if (roll < 0.45) {
    url += '&verified_only=true';
  } else if (roll < 0.60) {
    url += '&min_price=20000&max_price=150000';
  } else if (roll < 0.70) {
    url += '&ordering=-price';
  }

  const res = http.get(url);

  check(res, {
    'search query status is 200': (r) => r.status === 200,
    'search response is valid JSON': (r) => {
      try {
        const data = JSON.parse(r.body);
        return data && (data.results !== undefined);
      } catch (e) {
        return false;
      }
    },
  });

  sleep(Math.random() * 0.5 + 0.5);
}
