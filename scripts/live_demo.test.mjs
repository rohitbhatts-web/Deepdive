// The live-walkthrough launcher's decisions, without starting anything: it refuses an existing database or log, only plans a new .db path, picks a port nothing
// listens on, and its dry run creates no file. Run: node --test scripts/live_demo.test.mjs
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { freePort, option, plan } from './live_demo.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'live-demo-test-'))

test('a new .db path is planned with its own logs; an existing database or log is refused', () => {
  const dir = tmp()
  assert.deepEqual(plan(dir, 'data/new.db', 'records').problems, [])
  fs.mkdirSync(path.join(dir, 'data'))
  fs.writeFileSync(path.join(dir, 'data', 'old.db'), 'x')
  assert.match(plan(dir, 'data/old.db', 'records').problems[0], /old\.db already exists/)
  fs.writeFileSync(path.join(dir, 'data', 'logged.backend.log'), 'x')
  assert.match(plan(dir, 'data/logged.db', 'records').problems[0], /logged\.backend\.log already exists/)
  assert.match(plan(dir, 'data/notadb.txt', 'records').problems.join(' '), /must end in \.db/)
})

test('freePort skips a port that something is listening on', async () => {
  const srv = net.createServer()
  await new Promise((r) => srv.listen(0, '127.0.0.1', r))
  const busy = srv.address().port
  const got = await freePort(busy)
  srv.close()
  assert.notEqual(got, busy)
})

test('option reads a flag value, and null when absent', () => {
  assert.equal(option(['--database', 'a.db'], '--database'), 'a.db')
  assert.equal(option(['--database'], '--database'), null)
  assert.equal(option([], '--database'), null)
})

test('the dry run reports the plan, starts nothing and creates no file', () => {
  const dir = tmp()
  const before = fs.readdirSync(path.join(HERE, '..', 'data')).sort()
  const r = spawnSync('node', [path.join(HERE, 'live_demo.mjs'), '--database', 'data/dry-run-never-created.db', '--dry-run'], { encoding: 'utf8' })
  assert.equal(r.status, 0, r.stderr)
  assert.match(r.stdout, /dry run: nothing was started/)
  assert.match(r.stdout, /http:\/\/127\.0\.0\.1:\d+\//)
  assert.deepEqual(fs.readdirSync(path.join(HERE, '..', 'data')).sort(), before)
  assert.ok(fs.existsSync(dir))
})

test('without --database, or with an existing one, it refuses', () => {
  assert.notEqual(spawnSync('node', [path.join(HERE, 'live_demo.mjs')], { encoding: 'utf8' }).status, 0)
  const existing = path.join(tmp(), 'existing.db')  // made here, so the test does not depend on any local run database
  fs.writeFileSync(existing, '')
  const r = spawnSync('node', [path.join(HERE, 'live_demo.mjs'), '--database', existing, '--dry-run'], { encoding: 'utf8' })
  assert.equal(r.status, 2)
  assert.match(r.stdout, /already exists: choose a new database name/)
})
