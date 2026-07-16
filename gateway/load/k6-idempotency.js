import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Rate } from 'k6/metrics'

// A retry must reuse the idempotency key. This scenario asserts that the
// Gateway acknowledges both attempts with exactly one task identity; it does
// not start workers and therefore never consumes provider/model capacity.
const duplicateMismatch = new Counter('idempotency_task_id_mismatch')
const duplicateAccepted = new Rate('idempotency_duplicate_accepted')
const vus = Number(__ENV.VUS || 20)
const duration = __ENV.DURATION || '30s'
const tenantCount = Number(__ENV.TENANT_COUNT || 20)
const base = __ENV.BASE_URL || 'http://localhost:8080'
const apiKey = __ENV.API_KEY || ''
const prefix = __ENV.TASK_PREFIX || 'idempotency'
// Leave this at zero for capacity discovery. A sustained acceptance run can
// provide a delay so VUs do not become an unintended unlimited-rate source.
const iterationSleepSeconds = Number(__ENV.ITERATION_SLEEP_SECONDS || 0)
// By default no tenant header is sent: production Gateway ignores caller
// headers and derives the tenant from the authenticated identity. A trusted
// proxy integration test may opt in to its injected header explicitly.
const tenantHeaderName = __ENV.TENANT_HEADER_NAME || ''

export const options = {
  vus,
  duration,
  thresholds: {
    http_req_failed: ['rate<0.01'],
    idempotency_duplicate_accepted: ['rate==1'],
    idempotency_task_id_mismatch: ['count==0'],
  },
}

export default function () {
  const key = `${prefix}-${__VU}-${__ITER}`
  const tenantID = __ENV.TENANT_ID || `tenant-${__VU % tenantCount}`
  const params = {
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': key,
      ...(tenantHeaderName ? { [tenantHeaderName]: tenantID } : {}),
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    },
  }
  const body = JSON.stringify({ title: `retry-${key}`, description: 'Idempotency and retry acceptance test' })
  const first = http.post(`${base}/api/v1/tasks`, body, params)
  const second = http.post(`${base}/api/v1/tasks`, body, params)
  const accepted = first.status === 202 && second.status === 202
  duplicateAccepted.add(accepted)
  const firstTask = accepted ? first.json('task_id') : ''
  const secondTask = accepted ? second.json('task_id') : ''
  if (accepted && firstTask !== secondTask) duplicateMismatch.add(1)
  check({ first, second }, { 'both retries accepted': () => accepted, 'same task returned': () => accepted && firstTask === secondTask })
  if (iterationSleepSeconds > 0) sleep(iterationSleepSeconds)
}
