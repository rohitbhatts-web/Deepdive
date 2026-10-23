// Regression for the cleanup incident: an ad-hoc cleanup that matched by program name (taskkill /IM node.exe) and by port ended an UNRELATED Node
// process. Real processes, no mocks of the kill: the unrelated Node process must survive every case. Run: node --test scripts/ui_acceptance/owned.test.mjs
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { after, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { launchOwned, liveIdentity, stopOwned, verifyOwned } from './owned.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const idle = (marker) => ['-e', `setInterval(() => {}, 1000) /* ${marker} */`]
const alive = (pid) => { try { process.kill(pid, 0); return true } catch { return false } }
const until = async (fn, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await new Promise((r) => setTimeout(r, 100)) } return fn() }
const spawned = []
after(() => { for (const c of spawned) { try { process.kill(c.pid) } catch { /* already gone */ } } })

function unrelatedNode() {
  const c = spawn(process.execPath, idle('unrelated-process-that-must-survive'), { stdio: 'ignore', windowsHide: true })
  spawned.push(c)
  return c
}

test('stopping an owned process leaves an unrelated Node process (same program name) running', async () => {
  const unrelated = unrelatedNode()
  const record = launchOwned({ label: 'owned test node', command: process.execPath, args: idle('owned-by-this-test'), recordPath: path.join(HERE, '.owned-test-record.json') })
  fs.rmSync(path.join(HERE, '.owned-test-record.json'), { force: true })
  spawned.push({ pid: record.pid })
  assert.ok(alive(unrelated.pid) && alive(record.pid))
  const r = stopOwned(record)
  assert.equal(r.stopped, true, r.reason)
  assert.ok(await until(() => !alive(record.pid)), 'the owned process should be gone')
  assert.ok(alive(unrelated.pid), 'the unrelated node process must still be running')
})

test('a record whose pid now belongs to a different process is refused and nothing is stopped', () => {
  const unrelated = unrelatedNode()
  const real = liveIdentity(unrelated.pid)
  assert.ok(real, 'identity of the unrelated process is readable')
  const stale = { label: 'stale record', pid: unrelated.pid, created: '2000-01-01T00:00:00.0000000Z', commandLine: real.commandLine, recordedAt: 'x' }
  let killed = false
  const r = stopOwned(stale, { killTree: () => { killed = true } })
  assert.equal(r.stopped, false)
  assert.match(r.reason, /different process|cannot be established/)
  assert.equal(killed, false)
  assert.ok(alive(unrelated.pid))
  const wrongCommand = { ...stale, created: real.created, commandLine: 'node something-else.js' }
  assert.equal(verifyOwned(wrongCommand).owned, false)
  assert.ok(alive(unrelated.pid))
})

test('a record for a process that already exited stops nothing', () => {
  const unrelated = unrelatedNode()
  const gone = { label: 'exited', pid: 2147483000, created: 'x', commandLine: 'x', recordedAt: 'x' }
  const r = stopOwned(gone, { killTree: () => assert.fail('must not kill') })
  assert.equal(r.stopped, false)
  assert.match(r.reason, /not running/)
  assert.ok(alive(unrelated.pid))
})

test('no script or document in the repo tells anyone to stop processes by name, pattern or port', () => {
  const root = path.resolve(HERE, '..', '..')
  const files = []
  const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); if (e.isDirectory()) { if (!['node_modules', '.venv', '.git', 'dist'].includes(e.name)) walk(p) } else files.push(p) } }
  for (const d of ['scripts', 'docs']) walk(path.join(root, d))
  files.push(path.join(root, 'README.md'))
  const bad = /taskkill[^\n]*\/IM\b|Stop-Process\s+-Name|\bpkill\b|\bkillall\b|wmic[^\n]*delete|Get-Process[^\n]*Stop-Process/i
  const offenders = files.filter((f) => /\.(mjs|py|md|ps1|sh)$/.test(f) && !f.endsWith('owned.test.mjs') && bad.test(fs.readFileSync(f, 'utf8')))
  assert.deepEqual(offenders, [])
})
