import httpClient from 'k6/http'
import http from 'k6/http'
import { check, sleep } from 'k6'

const rate = Number(__ENV.RATE || 500)
const duration = __ENV.DURATION || '60s'
const prefix = __ENV.TASK_PREFIX || 'load'

export const options = {
  scenarios: {
    admission: {
      executor: 'ramping-arrival-rate',
      startRate: Math.min(20, rate),
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 500,
      stages: [
        { target: rate, duration },
        { target: 0, duration: '10s' },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<250'],
  },
}

const base = __ENV.BASE_URL || 'http://localhost:8080'
const apiKey = __ENV.API_KEY || ''
const iterationSleepSeconds = Number(__ENV.ITERATION_SLEEP_SECONDS || 0)
// Leave this unset for OIDC/API-key deployments. Only an integration test
// through a trusted proxy may provide the header name (normally X-Tenant-ID).
const tenantHeaderName = __ENV.TENANT_HEADER_NAME || ''
const tenantID = __ENV.TENANT_ID || `tenant-${__VU % Number(__ENV.TENANT_COUNT || 20)}`

export default function () {
  const id = `${__VU}-${__ITER}-${Date.now()}`
  const response = httpClient.post(
    `${base}/api/v1/tasks`,
    JSON.stringify({ title: `${prefix}-${id}`, description: 'Admission-path load test' }),
    {
      headers: {
        'Content-Type': 'application/json',
        ...(tenantHeaderName ? { [tenantHeaderName]: tenantID } : {}),
        'Idempotency-Key': id,
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
    },
  )
  check(response, { accepted: (r) => r.status === 202 })
  if (iterationSleepSeconds > 0) sleep(iterationSleepSeconds)
}
