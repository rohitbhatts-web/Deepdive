// End-to-end tests of the acceptance DRIVER against the SIMULATED backend (scripts/ui_sim_backend.py): no model, search or fetch call, no credits.
// Needs: the Vite dev server on :5173, Microsoft Edge, and PLAYWRIGHT_CORE=<dir of playwright-core>. Skipped (not passed) when any is missing.
//   node --test --test-timeout=240000 scripts/ui_acceptance/e2e.test.mjs
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { portInUse } from './lib.mjs'

const DRIVER = path.join(import.meta.dirname, 'driver.mjs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const ready = process.env.PLAYWRIGHT_CORE && fs.existsSync(path.join(process.env.PLAYWRIGHT_CORE, 'index.mjs')) && (await portInUse(5173, 'localhost')) && !(await portInUse(8000))
const opts = { skip: ready ? false : 'needs PLAYWRIGHT_CORE, Vite on :5173 and a free :8000' }
const kill = (pid) => spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' })
const getJson = async (p) => (await fetch(`http://127.0.0.1:8000${p}`)).json()

function runDriver(env, name) {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), `accept-${name}-`))
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [DRIVER, out], { env: { ...process.env, ACCEPT_BACKEND: 'sim', SIM_STEP_SECONDS: '2', ...env }, stdio: ['ignore', 'pipe', 'pipe'] })
    let log = ''
    child.stdout.on('data', (d) => (log += d))
    child.stderr.on('data', (d) => (log += d))
    child.on('close', (code) => resolve({ code, log, out, summary: fs.existsSync(path.join(out, 'summary.json')) ? JSON.parse(fs.readFileSync(path.join(out, 'summary.json'), 'utf8')) : null }))
  })
}
const result = (r, name) => r.summary.checks.find((c) => c.name.startsWith(name))

test('delayed page rendering does not end the wait early; completion is the stored terminal status; a missing report while active is normal', opts, async () => {
  const r = await runDriver({ ACCEPT_QUESTION: 'SIMULATED delayed render', ACCEPT_TEST_DELAY_RENDER_MS: '6000' }, 'delay')
  assert.ok(r.summary, r.log)
  assert.equal(result(r, 'run reached a terminal state').result, 'PASS', r.log)
  assert.match(result(r, 'run reached a terminal state').detail, /^terminal after (\d+) s/)
  assert.ok(Number(/after (\d+) s/.exec(result(r, 'run reached a terminal state').detail)[1]) >= 8, 'the wait lasted for the real job (about 20 s), not 0 s: ' + r.log)
  assert.equal(result(r, 'progress showed real stage updates').result, 'PASS', r.log)
  assert.equal(r.summary.finalRun.complete_status, 'completed')
  assert.equal(r.summary.finalRun.status, 'collection_finished')
  assert.equal(result(r, 'no HTTP error responses').result, 'PASS', 'the missing report during the active run caused no HTTP error: ' + result(r, 'no HTTP error responses').detail)
  assert.equal(result(r, 'no console errors').result, 'PASS')
  assert.equal(result(r, 'polling stopped').result, 'PASS')
  assert.equal(r.summary.cleanup.action, 'stop-owned')
  assert.equal(await portInUse(8000), false, 'the backend this script started is stopped once the job is terminal')
})

for (const [name, env, fatal] of [
  ['a timeout while the job is active', { ACCEPT_QUESTION: 'SIMULATED timeout', MAX_WAIT_MS: '5000' }, null],
  ['an assertion failure while the job is active', { ACCEPT_QUESTION: 'SIMULATED forced failure', ACCEPT_TEST_FORCE_FAILURE: 'after-start' }, /forced assertion failure/],
]) {
  test(`REGRESSION: ${name} does not kill the active backend job`, opts, async () => {
    const r = await runDriver(env, 'leave')
    assert.ok(r.summary, r.log)
    assert.equal(r.code, 1, 'the failure is reported through the exit code')
    if (fatal) assert.match(r.summary.fatal, fatal)
    assert.equal(r.summary.cleanup.action, 'leave')
    assert.match(r.summary.cleanup.reason, /may still be running/)
    assert.match(r.log, /LEFT RUNNING/)
    const pid = r.summary.backendPid
    try {
      assert.equal(await portInUse(8000), true, 'the backend is still serving after the driver exited')
      // the job must keep running to its own end, not be interrupted: it completes by itself
      let run = await getJson(`/runs/${r.summary.runId}`)
      for (let i = 0; i < 60 && (run.complete_status === 'completing' || run.status === 'researching'); i++) { await sleep(1000); run = await getJson(`/runs/${r.summary.runId}`) }
      assert.equal(run.complete_status, 'completed', `the job finished on its own (status ${run.status}/${run.complete_status}: ${run.stop_reason})`)
      assert.notEqual(run.status, 'failed')
      assert.ok(!(await getJson(`/runs/${r.summary.runId}/events`)).some((e) => e.stage === 'startup'), 'no startup-recovery event: the backend was never restarted or killed')
    } finally {
      kill(pid)
      await sleep(1500)
    }
    assert.equal(await portInUse(8000), false, 'the test removed the leftover backend itself')
  })
}

test('the driver refuses to start when something already serves the port, and stops nothing it does not own', opts, async () => {
  const decoy = net.createServer((s) => s.end()).listen(8000, '127.0.0.1')
  await new Promise((r) => decoy.once('listening', r))
  try {
    const r = await runDriver({ ACCEPT_QUESTION: 'SIMULATED never runs' }, 'busy')
    assert.equal(r.code, 1)
    assert.match(r.log, /already in use; refusing to start/)
    assert.doesNotMatch(r.log, /started backend/)
    assert.equal(r.summary.backendPid, null)
    assert.equal(r.summary.cleanup.action, 'leave')
    assert.equal(await portInUse(8000), true, 'the decoy server (not ours) is untouched')
  } finally {
    await new Promise((r) => decoy.close(r))
  }
})

test('cleanup stops only the process tree this script started, not look-alike processes', opts, async () => {
  // a decoy whose command line mentions the sim backend script, serving on another port
  const decoy = spawn(process.execPath, ['-e', "require('net').createServer((s)=>s.end()).listen(8123,'127.0.0.1'); /* scripts/ui_sim_backend.py */ setInterval(()=>{},1000)"], { stdio: 'ignore' })
  await sleep(1000)
  assert.equal(await portInUse(8123), true)
  try {
    const r = await runDriver({ ACCEPT_QUESTION: 'SIMULATED look-alike' }, 'decoy')
    assert.equal(r.summary.cleanup.action, 'stop-owned', r.log)
    assert.equal(await portInUse(8000), false, 'our backend is gone')
    assert.equal(await portInUse(8123), true, 'the look-alike process is still alive')
  } finally {
    kill(decoy.pid)
  }
})
