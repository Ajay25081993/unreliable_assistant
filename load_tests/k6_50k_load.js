import http from 'k6/http';
import { check, sleep } from 'k6';

// Override for local smoke runs:  k6 run -e SMOKE=1 -e BASE=http://localhost:8000 load_tests/k6_50k_load.js
const SMOKE = __ENV.SMOKE === '1';
const BASE = __ENV.BASE || 'http://localhost:8000';

export const options = SMOKE ? {
  stages: [{ duration: '5s', target: 50 }, { duration: '15s', target: 100 }, { duration: '5s', target: 0 }],
  thresholds: { 'http_req_duration': ['p(95)<450'], 'http_req_failed': ['rate<0.0005'] },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
} : {
  stages: [
    { duration: '2m', target: 10000 },  // Ramp to 10K VUs
    { duration: '3m', target: 50000 },  // Ramp to 50K concurrent VUs
    { duration: '5m', target: 50000 },  // Sustain 50K VUs peak
    { duration: '2m', target: 0 },      // Graceful ramp-down
  ],
  thresholds: {
    'http_req_duration': ['p(50)<250', 'p(95)<450', 'p(99)<600'], // Hard 200-600ms SLA
    'http_req_failed': ['rate<0.0005'],                           // < 0.05% error rate
    'checks': ['rate>0.999'],
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const vuId = __VU;
  const payload = JSON.stringify({
    tenant_id: `tenant_${vuId % 50}`,
    user_id: `user_${vuId}`,
    query: 'Verify batch LOT-88392 safety protocol and return triage status'
  });
  const params = { headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': `tenant_${vuId % 50}` } };
  const res = http.post(`${BASE}/api/v1/agent/query`, payload, params);
  check(res, {
    'status is 200': (r) => r.status === 200,
    'latency within 200-600ms SLA': (r) => r.timings.duration <= 600,
    'no cross-tenant leak': (r) => r.json('tenant_id') === `tenant_${vuId % 50}`,
  });
  sleep(1);
}

// Machine-readable summary consumed by eval/ingest_load.py
export function handleSummary(data) {
  const m = data.metrics, d = m.http_req_duration.values;
  const out = {
    vus: m.vus_max ? m.vus_max.values.max : 0,
    p50: d.med, p95: d['p(95)'], p99: d['p(99)'],
    err_pct: (m.http_req_failed ? m.http_req_failed.values.rate : 0) * 100,
    rps: m.http_reqs.values.rate,
    duration_s: data.state.testRunDurationMs / 1000,
    source: SMOKE ? 'k6-smoke-local' : 'k6-full',
  };
  return { [SMOKE ? 'eval/results/load_smoke.json' : 'eval/results/load_summary.json']: JSON.stringify(out, null, 1), stdout: JSON.stringify(out) + '\n' };
}
