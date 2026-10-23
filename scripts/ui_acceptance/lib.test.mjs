// node --test scripts/ui_acceptance/lib.test.mjs     (no browser, no backend, no credits)
import assert from 'node:assert/strict'
import net from 'node:net'
import test from 'node:test'
import { cleanupDecision, isActiveRun, portInUse, startOwnedBackend, unexpectedHttpErrors, waitForTerminal } from './lib.mjs'

const active = { status: 'researching', verify_status: 'verifying', complete_status: 'completing' }
const done = { status: 'collection_finished', verify_status: 'completed', complete_status: 'completed' }
const fakeClock = () => { let t = 0; return { now: () => t, sleep: async (ms) => { t += ms } } }

test('stored job status decides whether a run is active', () => {
  assert.equal(isActiveRun(active), true)
  assert.equal(isActiveRun({ ...done, status: 'researching' }), true)
  assert.equal(isActiveRun({ ...done, verify_status: 'verifying' }), true)
  assert.equal(isActiveRun({ ...done, complete_status: 'completing' }), true)
  for (const terminal of [done, { ...done, status: 'failed', complete_status: 'failed' }, { ...done, complete_status: 'stopped' }, { status: 'created', verify_status: 'not_started', complete_status: 'not_started' }]) assert.equal(isActiveRun(terminal), false)
  assert.equal(isActiveRun(null), false)
})

test('REGRESSION: delayed page rendering does not end the wait early', async () => {
  const clock = fakeClock()
  let renderedAt = null
  const states = [active, active, active, active, done]
  let call = 0
  const r = await waitForTerminal({
    ...clock, timeoutMs: 60000, pollMs: 1000,
    pageReady: async () => { await clock.sleep(8000); renderedAt = clock.now() }, // the page needs 8 s to render
    getRun: async () => states[Math.min(call++, states.length - 1)],
  })
  assert.equal(renderedAt, 8000)
  assert.equal(r.state, 'terminal')
  assert.equal(r.run, done)
  assert.equal(r.ticks, 4) // it polled the active run four times; it did not conclude "finished" because the progress text had not appeared yet
})

test('REGRESSION: an active run is never reported terminal, whatever the screen shows', async () => {
  const clock = fakeClock()
  const r = await waitForTerminal({ ...clock, timeoutMs: 5000, getRun: async () => active, pageReady: async () => {} })
  assert.equal(r.state, 'timeout')
  assert.equal(isActiveRun(r.run), true)
})

test('completion is the stored terminal status, including failed and stopped', async () => {
  for (const terminal of [{ ...done, status: 'failed', complete_status: 'failed' }, { ...done, complete_status: 'stopped' }]) {
    const clock = fakeClock()
    let n = 0
    const r = await waitForTerminal({ ...clock, timeoutMs: 60000, getRun: async () => (n++ < 2 ? active : terminal) })
    assert.deepEqual([r.state, r.run], ['terminal', terminal])
  }
})

test('transient API failures are retried; persistent failure is "unreachable", never "terminal"', async () => {
  const clock = fakeClock()
  let n = 0
  const ok = await waitForTerminal({ ...clock, timeoutMs: 60000, getRun: async () => { n += 1; if (n <= 3) throw new Error('boom'); return n < 6 ? active : done } })
  assert.equal(ok.state, 'terminal')
  const dead = await waitForTerminal({ ...fakeClock(), timeoutMs: 60000, maxFailures: 5, getRun: async () => { throw new Error('down') } })
  assert.equal(dead.state, 'unreachable')
  assert.equal(dead.run, null)
})

test('cleanup never stops a backend that may still be running a job, or one this script did not start', () => {
  assert.equal(cleanupDecision({ ownedByScript: true, jobState: 'terminal' }).action, 'stop-owned')
  assert.equal(cleanupDecision({ ownedByScript: true, jobState: 'none' }).action, 'stop-owned')
  for (const jobState of ['timeout', 'unreachable', 'active', 'unknown', undefined]) assert.equal(cleanupDecision({ ownedByScript: true, jobState }).action, 'leave', String(jobState))
  for (const jobState of ['terminal', 'none', 'timeout']) assert.equal(cleanupDecision({ ownedByScript: false, jobState }).action, 'leave')
})

test('no HTTP error is suppressed unless a rule names it exactly', () => {
  const rs = [{ method: 'GET', status: 404, url: '/api/runs/3/report' }, { method: 'GET', status: 500, url: '/api/runs/3/events' }, { method: 'GET', status: 200, url: '/api/runs/3' }]
  assert.equal(unexpectedHttpErrors(rs).length, 2)
  const allowed = unexpectedHttpErrors(rs, [{ method: 'GET', status: 404, pattern: /\/api\/runs\/\d+\/report$/ }])
  assert.deepEqual(allowed.map((r) => r.status), [500])
  assert.equal(unexpectedHttpErrors(rs, [{ method: 'POST', status: 404, pattern: /report/ }]).length, 2) // wrong method: not allowed
})

test('portInUse sees a listening server and a free port', async () => {
  const server = net.createServer().listen(0, '127.0.0.1')
  await new Promise((r) => server.once('listening', r))
  const { port } = server.address()
  assert.equal(await portInUse(port), true)
  await new Promise((r) => server.close(r))
  assert.equal(await portInUse(port), false)
})

test('startOwnedBackend refuses an occupied port without spawning or killing anything', async () => {
  const calls = []
  await assert.rejects(startOwnedBackend({ spawnFn: () => { calls.push('spawn'); return { pid: 1 } }, killTree: (p) => calls.push(`kill ${p}`), port: 8000, command: 'x', args: [], options: {}, inUse: async () => true, waitHealthy: async () => {} }), /already in use/)
  assert.deepEqual(calls, [])
})

test('an owned backend is stopped only by its own pid, once', async () => {
  const kills = []
  const h = await startOwnedBackend({ spawnFn: () => ({ pid: 4242 }), killTree: (p) => kills.push(p), port: 8000, command: 'x', args: [], options: {}, inUse: async () => false, waitHealthy: async () => {} })
  assert.equal(h.pid, 4242)
  h.stop()
  h.stop()
  assert.deepEqual(kills, [4242])
})

test('a backend that never becomes healthy is stopped (it has no job) and the error is raised', async () => {
  const kills = []
  await assert.rejects(startOwnedBackend({ spawnFn: () => ({ pid: 7 }), killTree: (p) => kills.push(p), port: 8000, command: 'x', args: [], options: {}, inUse: async () => false, waitHealthy: async () => { throw new Error('never healthy') } }), /never healthy/)
  assert.deepEqual(kills, [7])
})

test('leaving the backend running releases the child so the script itself can exit', async () => {
  let unrefs = 0
  const h = await startOwnedBackend({ spawnFn: () => ({ pid: 9, unref: () => { unrefs += 1 } }), killTree: () => {}, port: 8000, command: 'x', args: [], options: {}, inUse: async () => false, waitHealthy: async () => {} })
  h.release()
  assert.equal(unrefs, 1)
})
