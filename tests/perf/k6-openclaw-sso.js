import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  scenarios: {
    openclaw_sso: {
      executor: 'constant-vus',
      vus: 40,
      duration: '2m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<20000'],
    checks: ['rate>0.99'],
  },
};

const BASE = __ENV.BASE_URL || 'http://openclaw.company.internal';

export default function () {
  const res = http.get(`${BASE}/resolve?employee_id=u1001`);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
}
