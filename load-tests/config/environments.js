export const CONFIG = {
  baseUrl: __ENV.BASE_URL || 'http://localhost:8000',
  endpoints: {
    categories: '/api/v1/catalog/categories/',
    products: '/api/v1/catalog/products/',
    productDetail: (slug) => `/api/v1/catalog/products/${slug}/`,
    similar: (slug) => `/api/v1/catalog/similar/${slug}/`,
    authToken: '/api/v1/auth/token/',
    tokenRefresh: '/api/v1/auth/token/refresh/',
    publicStats: '/api/v1/catalog/public-stats/',
  },
  thresholds: {
    // Engineering targets per Guide Section 11 & Section 16:
    // HTTP error rate < 1%
    // p95 read latency < 500ms
    // p99 read latency < 1000ms
    standard: {
      http_req_failed: ['rate<0.01'],
      http_req_duration: ['p(95)<500', 'p(99)<1000'],
    },
    strict: {
      http_req_failed: ['rate<0.005'],
      http_req_duration: ['p(95)<300', 'p(99)<600'],
    },
    stress: {
      http_req_failed: ['rate<0.05'],
      http_req_duration: ['p(95)<1500'],
    },
  },
};
