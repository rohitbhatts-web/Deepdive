// HTTP smoke test of a running hosted demo (local or deployed):  node scripts/smoke_hosted_demo.mjs http://127.0.0.1:8777
// Read-only except for deliberately attempted writes, which the demo must refuse (403). Starts no research, needs no key, calls no provider.
const base = (process.argv[2] ?? 'http://127.0.0.1:8000').replace(/\/$/, '')
const results = []
const check = (name, ok, detail = '') => { results.push({ name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`) }
const get = (p, init) => fetch(base + p, init)

const health = await get('/health')
check('GET /health is 200 and does not need the database', health.status === 200 && (await health.json()).status === 'ok')
const ready = await get('/ready')
const readyBody = await ready.json()
check('GET /ready is 200, reports the recorded runs and no refused update', ready.status === 200 && readyBody.runs >= 1 && !readyBody.update_error, JSON.stringify(readyBody))
const index = await get('/')
const html = await index.text()
check('GET / serves the production build', index.status === 200 && html.includes('<div id="root">') && /\/assets\/index-[\w-]+\.js/.test(html))
check('security headers are set', ['content-security-policy', 'x-content-type-options', 'x-frame-options'].every((h) => index.headers.get(h)))
for (const deep of ['/runs/1/report', '/runs/1/evidence', '/runs/2/usage']) {
  const r = await get(deep)
  check(`deep link ${deep} serves the app shell`, r.status === 200 && (await r.text()).includes('<div id="root">'))
}
const demo = await (await get('/api/demo')).json()
check('GET /api/demo says read-only and labels the demo', demo.read_only === true && /Recorded/.test(demo.label ?? ''))
const apiHealth = await (await get('/api/health')).json()
check('the API reports read-only and no provider configured', apiHealth.read_only === true && apiHealth.mesh_configured === false)
const runs = await (await get('/api/runs')).json()
check('GET /api/runs lists recorded runs', runs.total >= 1, `total ${runs.total}`)
const report = await get('/api/runs/1/report')
check('GET /api/runs/1/report returns the stored report', report.status === 200 && Boolean((await report.json()).markdown))
check('interactive API docs are not served', (await get('/api/docs')).status === 404 && (await get('/api/openapi.json')).status === 404)
const missing = await get('/api/no-such-endpoint')
check('an unknown API path is a JSON 404, not the app shell', missing.status === 404 && !(await missing.text()).includes('<div id="root">'))
const writes = [['POST', '/api/runs', { question: 'smoke test: this must be refused' }], ['POST', '/api/runs/1/research'], ['POST', '/api/runs/1/research?complete=true'],
  ['POST', '/api/runs/1/verify'], ['POST', '/api/runs/1/complete'], ['POST', '/api/runs/1/retry', { confirm_paid_retry: true }],
  ['POST', '/api/runs/1/sources', { url: 'https://example.com/' }], ['PUT', '/api/runs/1'], ['PATCH', '/api/runs/1'], ['DELETE', '/api/runs/1']]
for (const [method, path, body] of writes) {
  const r = await get(path, { method, headers: { 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
  check(`${method} ${path} is refused`, r.status === 403, `HTTP ${r.status}`)
}
const after = await (await get('/api/runs')).json()
check('refused writes created nothing', after.total === runs.total, `${runs.total} -> ${after.total}`)
const failed = results.filter((r) => !r.ok)
console.log(failed.length ? `\n${failed.length} check(s) FAILED` : `\nall ${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
