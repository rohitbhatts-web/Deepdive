// Start (or stop) ONE live local walkthrough: the real backend and the real Vite UI on a NEW database, as processes this tool owns. It never starts research: you
// click Start in the UI. Unchanged product defaults: no limit or model is set here (the backend reads its own configuration), and nothing is printed from .env.
//
//   node scripts/live_demo.mjs --database data/live-git-merge-rebase-<stamp>.db [--dry-run]
//   node scripts/live_demo.mjs --stop <the records directory it printed>
//
// Ports are picked free (never a port that something already listens on) and nothing that is not in a record written here is ever stopped. Each record holds the
// pid with its creation time and command line; a process is stopped only if it still matches, via scripts/ui_acceptance/owned.mjs.
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { launchOwned, stopOwned } from './ui_acceptance/owned.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function option(argv, name) {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null
}

/** The first port from `start` that nothing is listening on (checked by binding it), so an old server is never reused or disturbed. */
export function freePort(start, tries = 50) {
  return new Promise((resolve, reject) => {
    const attempt = (port, left) => {
      if (left === 0) return reject(new Error(`no free port found from ${start}`))
      const srv = net.createServer()
      srv.once('error', () => attempt(port + 1, left - 1))
      srv.listen(port, '127.0.0.1', () => srv.close(() => resolve(port)))
    }
    attempt(start, tries)
  })
}

/** The files this walkthrough will create, or the reasons it refuses: an existing database or log is never reused or overwritten. */
export function plan(root, database, recordsDir) {
  const db = path.resolve(root, database)
  const stem = db.replace(/\.db$/i, '')
  const files = { database: db, backendLog: `${stem}.backend.log`, frontendLog: `${stem}.frontend.log` }
  const existing = Object.values(files).filter((f) => fs.existsSync(f) || fs.existsSync(`${f}-wal`) || fs.existsSync(`${f}-shm`))
  const problems = existing.map((f) => `${f} already exists: choose a new database name`)
  if (!/\.db$/i.test(db)) problems.push('the database path must end in .db (so it stays git-ignored)')
  return { files, records: recordsDir, problems }
}

async function start(argv) {
  const database = option(argv, '--database')
  if (!database) throw new Error('--database <new path ending in .db> is required')
  const recordsDir = path.join(os.tmpdir(), 'deepdive-live-demo', path.basename(database).replace(/\.db$/i, ''))
  const p = plan(ROOT, database, recordsDir)
  if (p.problems.length) {
    console.log('Refusing to start:\n  ' + p.problems.join('\n  '))
    return 2
  }
  const backendPort = await freePort(Number(option(argv, '--backend-port') ?? 8051))
  const frontendPort = await freePort(Number(option(argv, '--frontend-port') ?? 5211))
  const py = process.platform === 'win32' ? path.join(ROOT, '.venv', 'Scripts', 'python.exe') : path.join(ROOT, '.venv', 'bin', 'python')
  const backendArgs = ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(backendPort)]
  const frontendArgs = ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(frontendPort), '--strictPort']
  console.log(`database (new): ${p.files.database}\nbackend  : 127.0.0.1:${backendPort}\nfrontend : http://127.0.0.1:${frontendPort}/\nlogs     : ${p.files.backendLog}, ${p.files.frontendLog}\nrecords  : ${recordsDir}`)
  if (argv.includes('--dry-run')) {
    console.log('dry run: nothing was started, no file was created.')
    return 0
  }
  fs.mkdirSync(recordsDir, { recursive: true })
  const log = (f) => fs.openSync(f, 'a')
  const be = launchOwned({ label: 'live-demo-backend', command: py, args: backendArgs, recordPath: path.join(recordsDir, 'backend.json'),
    options: { cwd: ROOT, env: { ...process.env, DATABASE_PATH: p.files.database }, stdio: ['ignore', log(p.files.backendLog), log(p.files.backendLog)] } })
  const fe = launchOwned({ label: 'live-demo-frontend', command: 'node', args: frontendArgs, recordPath: path.join(recordsDir, 'frontend.json'),
    options: { cwd: path.join(ROOT, 'frontend'), env: { ...process.env, DEEPDIVE_API: `http://127.0.0.1:${backendPort}` }, stdio: ['ignore', log(p.files.frontendLog), log(p.files.frontendLog)] } })
  console.log(`started (owned): backend pid ${be.pid}, frontend pid ${fe.pid}\nOpen http://127.0.0.1:${frontendPort}/ , choose New research, and click Start yourself. No research has been started.`)
  return 0
}

function stop(recordsDir) {
  for (const name of ['frontend.json', 'backend.json']) {
    const file = path.join(recordsDir, name)
    if (!fs.existsSync(file)) { console.log(`${name}: no record`); continue }
    console.log(name, JSON.stringify(stopOwned(JSON.parse(fs.readFileSync(file, 'utf8')))))
  }
  return 0
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const argv = process.argv.slice(2)
  const stopDir = option(argv, '--stop')
  const run = stopDir ? Promise.resolve(stop(stopDir)) : start(argv)
  run.then((code) => process.exit(code), (e) => { console.error(String(e.message ?? e)); process.exit(1) })
}
