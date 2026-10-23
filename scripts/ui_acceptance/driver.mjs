// UI-to-backend acceptance driver. Needs: the Vite dev server on :5173 (proxying /api to :8000), Microsoft Edge, and playwright-core
// (PLAYWRIGHT_CORE=<dir of an installed playwright-core>, or installed normally). Not a project dependency.
//
//   LIVE, real backend and providers (SPENDS CREDITS; only with approval):
//     ACCEPT_BACKEND=real ACCEPT_QUESTION="..." node scripts/ui_acceptance/driver.mjs OUT_DIR
//   SIMULATED backend (no credits; the question text picks the simulated script, see scripts/ui_sim_backend.py):
//     ACCEPT_BACKEND=sim ACCEPT_QUESTION="SIMULATED ..." node scripts/ui_acceptance/driver.mjs OUT_DIR
//   INSPECT ONLY a stored run (no start, no credits):
//     ACCEPT_BACKEND=real INSPECT_DB=<copy of a database> RUN_ID=1 node scripts/ui_acceptance/driver.mjs OUT_DIR
//
// Safety rules (see lib.mjs): completion is the stored job status; the backend is stopped only if this script started it AND the job is known to
// be terminal (or never started); a timeout, an assertion failure or an unreachable API leaves a possibly-running paid job alone; the script
// refuses to start when port 8000 is already served. Nothing is retried. Test hooks (ACCEPT_TEST_*) exist only to test these rules.
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { cleanupDecision, isActiveRun, startOwnedBackend, unexpectedHttpErrors, waitForTerminal } from './lib.mjs'

const { chromium } = await import(process.env.PLAYWRIGHT_CORE ? pathToFileURL(path.join(process.env.PLAYWRIGHT_CORE, 'index.mjs')).href : 'playwright-core')
const REPO = path.resolve(import.meta.dirname, '..', '..')
const OUT = process.argv[2]
fs.mkdirSync(OUT, { recursive: true })
const BACKEND = process.env.ACCEPT_BACKEND ?? 'real'
const QUESTION = process.env.ACCEPT_QUESTION
const INSPECT_DB = process.env.INSPECT_DB
const FIXED_RUN = process.env.RUN_ID ? Number(process.env.RUN_ID) : null
const PORT = 8000
const MAX_WAIT_MS = Number(process.env.MAX_WAIT_MS ?? 420000)
const DB = process.env.ACCEPT_DB ?? INSPECT_DB ?? (BACKEND === 'sim' ? path.join(OUT, 'sim.db') : path.join(REPO, 'data', 'ui-acceptance.db'))
if (!FIXED_RUN && BACKEND === 'real' && fs.existsSync(DB)) { console.error(`refusing to start a live run on an existing database (${DB}); give ACCEPT_DB a NEW path so earlier results are preserved`); process.exit(2) }
const edge = process.env.EDGE ?? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const checks = []
const check = (name, ok, detail = '') => { const result = ok === null ? 'NOTE' : ok ? 'PASS' : 'FAIL'; checks.push({ name, result, detail: String(detail).slice(0, 400) }); console.log(`${result}  ${name}${detail ? ' - ' + String(detail).slice(0, 220) : ''}`) }
const api = async (p) => { const r = await fetch(`http://127.0.0.1:${PORT}${p}`); return r.json() }

let backend = null
let runId = FIXED_RUN
let jobState = FIXED_RUN ? 'terminal' : 'none' // 'none' until a run is started; then unknown until observed
let browser = null
let fatal = null
let finalRun = null

try {
  const sim = BACKEND === 'sim'
  backend = await startOwnedBackend({
    spawnFn: (cmd, args, options) => spawn(cmd, args, options),
    killTree: (pid) => spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' }),
    port: PORT,
    // The project's own venv python, started directly: `uv run` exits after launching its child on Windows, which would leave no process to own
    // and stop. The venv launcher stays alive as the parent of the real server, so its pid can stop the whole tree.
    command: process.platform === 'win32' ? path.join(REPO, '.venv', 'Scripts', 'python.exe') : path.join(REPO, '.venv', 'bin', 'python'),
    args: sim ? ['scripts/ui_sim_backend.py'] : ['-m', 'uvicorn', 'backend.main:app', '--port', String(PORT)],
    // detached: on Windows libuv otherwise puts the child in a job object that kills it when this script exits, which would kill a running job.
    options: { cwd: REPO, shell: false, detached: true, windowsHide: true, stdio: 'ignore', env: { ...process.env, PYTHONUTF8: '1', ...(sim ? { SIM_DB: DB, SIM_STEP_SECONDS: process.env.SIM_STEP_SECONDS ?? '2' } : { DATABASE_PATH: DB }) } },
    waitHealthy: async () => { for (let i = 0; i < 80; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/health`)).ok) return } catch { /* not up yet */ } await sleep(500) } throw new Error('backend did not become healthy') },
  })
  console.log(`started backend (${BACKEND}) pid ${backend.pid} on :${PORT}, database ${DB}`)
  const health = await api('/health')
  check('backend up', health.status === 'ok' && (sim || health.mesh_configured === true), JSON.stringify({ status: health.status, mesh_configured: health.mesh_configured }))

  browser = await chromium.launch({ executablePath: edge, headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const reqs = []
  const httpErrors = []
  const consoleErrors = []
  page.on('request', (r) => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) reqs.push({ t: Date.now(), m: r.method(), path: u.pathname + u.search }) })
  page.on('response', (r) => { if (r.status() >= 400) { const u = new URL(r.url()); httpErrors.push({ method: r.request().method(), status: r.status(), url: u.pathname + u.search }) } })
  page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
  page.on('pageerror', (e) => consoleErrors.push(String(e)))
  const shot = (n, full = true) => page.screenshot({ path: path.join(OUT, `${n}.png`), fullPage: full })
  const text = async (sel) => (await page.locator(sel).first().innerText()).replace(/\s+/g, ' ').trim()

  const delay = Number(process.env.ACCEPT_TEST_DELAY_RENDER_MS ?? 0)
  if (delay) { // TEST HOOK: make the run page render late, as a slow machine would
    let held = 0
    await page.route(/\/api\/runs\/\d+$/, async (route) => { if (held++ < 3) await sleep(delay); await route.continue().catch(() => {}) })
  }

  const timeline = []
  if (!FIXED_RUN) {
    await page.goto('http://localhost:5173/#/')
    await page.getByText('Configured limits for each run').waitFor()
    const onPage = await text('main')
    const limits = (await api('/limits')).limits
    check('New research page shows the configured limits', Object.values(limits).every((v) => onPage.includes(v.toLocaleString('en-US'))), JSON.stringify(limits))
    await shot('01-new-research', false)
    await page.getByLabel('Research question').fill(QUESTION)
    await page.getByRole('button', { name: /Start research/ }).click()
    await page.waitForURL(/#\/runs\/\d+\/report/, { timeout: 30000 })
    runId = Number(/runs\/(\d+)/.exec(page.url())[1])
    jobState = 'unknown' // a paid job may be running from here on
    check('start navigated to the new run', true, `run ${runId}`)
    check('exactly one run and one research start were requested', reqs.filter((r) => r.m === 'POST' && r.path === '/api/runs').length === 1 && reqs.filter((r) => r.m === 'POST' && /\/research/.test(r.path)).length === 1, reqs.filter((r) => r.m === 'POST').map((r) => r.path).join(', '))
    if (process.env.ACCEPT_TEST_FORCE_FAILURE === 'after-start') throw new Error('forced assertion failure (test hook) while the job is running')

    let shotMid = false
    let last = ''
    const t1 = Date.now()
    const result = await waitForTerminal({
      timeoutMs: MAX_WAIT_MS, sleep, getRun: () => api(`/runs/${runId}`),
      pageReady: async () => { await page.locator('main h1').first().waitFor({ timeout: 60000 }) }, // rendering only; never decides completion
      onTick: async () => {
        const prog = page.getByRole('region', { name: 'Run progress' })
        if (!(await prog.count())) return
        const latest = (await prog.locator('[role=status]').first().innerText().catch(() => '')).replace(/\s+/g, ' ')
        const stages = (await page.getByRole('list', { name: 'Stages reached so far' }).locator('li').count().catch(() => 0))
        const key = `${latest}|${stages}`
        if (key !== last) { timeline.push({ s: Math.round((Date.now() - t1) / 1000), latest: latest.slice(0, 120), stages }); last = key }
        if (!shotMid && timeline.length >= 4) { await shot('02-progress', false); shotMid = true }
      },
    })
    jobState = result.state // terminal | timeout | unreachable
    check('run reached a terminal state (stored job status)', result.state === 'terminal', `${result.state} after ${Math.round((Date.now() - t1) / 1000)} s`)
    check('progress showed real stage updates while running', timeline.length >= 3, `${timeline.length} distinct updates: ` + timeline.slice(0, 6).map((x) => `${x.s}s ${x.latest.slice(0, 40)}`).join(' | '))
    fs.writeFileSync(path.join(OUT, 'progress-timeline.json'), JSON.stringify(timeline, null, 1))
    if (result.state === 'terminal') {
      await page.locator('main h1').first().waitFor()
      await sleep(3000) // let the UI poll once more and notice the terminal status
      const tTerm = Date.now()
      await sleep(6000)
      const polls = reqs.filter((r) => r.t >= tTerm && new RegExp(`/api/runs/${runId}/(events|research|completion)$`).test(r.path)).length
      check('polling stopped after the terminal state', polls === 0, `${polls} polling requests in the 6 s after`)
    }
  }

  if (jobState === 'terminal') {
    await page.goto(`http://localhost:5173/#/runs/${runId}/report`)
    await page.locator('main h1').first().waitFor()
    await sleep(1500)
    await shot('03-terminal-report')
    const run = await api(`/runs/${runId}`), research = await api(`/runs/${runId}/research`), completion = await api(`/runs/${runId}/completion`), events = await api(`/runs/${runId}/events`)
    console.log('API status:', JSON.stringify({ status: run.status, verify: run.verify_status, complete: run.complete_status, report_outcome: run.report_outcome, stop: completion.stop_reason }))
    const header = await text('main header')
    check('header shows the stored status label', /Completed|Stopped|Failed/.test(header), header.slice(0, 160))
    if (completion.stop_reason) check('stopping reason is shown', header.includes(completion.stop_reason.slice(0, 40)), completion.stop_reason)
    check('no unfinished job status remains', !isActiveRun(run), `${run.status}/${run.verify_status}/${run.complete_status}`)

    const hasReport = run.report_outcome !== null
    const reportClaims = []
    if (!hasReport) {
      const note = `no report (stopped/failed/simulated run); the screen shows its empty state: "${(await text('#section-panel')).slice(0, 80)}"`
      check('a report exists', sim || run.complete_status !== 'completed' ? null : false, note)
      check('the report was not requested when none exists', !reqs.some((r) => r.m === 'GET' && r.path === `/api/runs/${runId}/report`), 'GET /report calls: ' + reqs.filter((r) => r.path === `/api/runs/${runId}/report`).length)
    } else {
      const report = await api(`/runs/${runId}/report`)
      const apiClaims = report.sections.flatMap((s) => s.claims)
      reportClaims.push(...apiClaims)
      const uiTexts = await page.locator('button[id^="claim-btn-"]').evaluateAll((els) => els.map((e) => e.textContent))
      check('report claim texts in the UI equal the stored report text, exactly', JSON.stringify(uiTexts) === JSON.stringify(apiClaims.map((c) => c.text)), `${uiTexts.length} in the UI, ${apiClaims.length} stored`)
      if (apiClaims.length) {
        const first = apiClaims[0]
        await page.locator(`#claim-btn-${first.claim_id}`).locator('xpath=ancestor::li[1]').locator('ul button').first().click()
        await page.getByRole('heading', { name: new RegExp(`Claim ${first.claim_id} inspector`) }).waitFor({ timeout: 15000 })
        await page.getByText('Stored passage text (exact)').first().waitFor({ timeout: 15000 })
        check('clicking a citation opened that claim in the URL', page.url().includes(`claim=${first.claim_id}`), page.url())
        const detail = await api(`/runs/${runId}/claims/${first.claim_id}`)
        const stored = detail.evidence[0].passage.text
        const shown = await page.locator('#inspector blockquote').first().innerText()
        check('inspector shows the exact stored passage text', shown.replace(/\s+/g, ' ').trim() === stored.replace(/\s+/g, ' ').trim(), `${stored.length} chars`)
        check('the cited quote is highlighted inside the passage', (await page.locator('#inspector mark').first().innerText().catch(() => '')) === detail.evidence[0].quote)
        const link = page.locator('#inspector a[target=_blank]').first()
        check('source link opens safely in a new tab', (await link.getAttribute('rel')) === 'noopener noreferrer' && (await link.getAttribute('href')) === detail.evidence[0].passage.final_url, detail.evidence[0].passage.final_url)
        check('integrity and semantic verdict are displayed', (await text('#inspector')).includes('Integrity check') && (await text('#inspector')).includes('Semantic verdict'))
        await shot('04-report-inspector')
      }
    }

    await page.goto(`http://localhost:5173/#/runs/${runId}/coverage`)
    await sleep(1800)
    const cov = await api(`/runs/${runId}/coverage`)
    await shot('05-coverage')
    if (cov.requirements.length) {
      const gaps = await page.getByText('Remaining gap:').count()
      check('coverage lists every stored requirement with its gaps', (await page.getByText(cov.requirements[0].requirement_text).count()) > 0 && gaps === cov.requirements.filter((r) => r.status !== 'addressed' && r.gap).length, `${cov.requirements.length} requirements, ${gaps} gap boxes`)
    } else check('coverage assessment', null, 'no coverage rows stored for this run (the screen shows its empty state)')
    await page.goto(`http://localhost:5173/#/runs/${runId}/history`)
    await sleep(1800)
    const corr = await api(`/runs/${runId}/corrections`)
    await shot('06-history')
    check('run history shows every corrective round', (await page.getByRole('heading', { name: /Corrective round/ }).count()) === corr.rounds.length, `${corr.rounds.length} stored; ${corr.revisions.length} claim chain(s); ${events.length} events`)

    await page.goto(`http://localhost:5173/#/runs/${runId}/usage`)
    await sleep(1800)
    await shot('07-usage')
    const usage = await text('#section-panel')
    const hdr = (await text('dl[aria-label="Run summary"]')).replace(/\s+/g, '')
    const budget = completion.budget_used_of_limit?.model_requests
    check('header: total model calls for this run equals the stored count', hdr.includes(`Totalmodelcallsforthisrun${research.model_usage.model_calls.toLocaleString('en-US')}`), `API ${research.model_usage.model_calls}`)
    check('header: latest job requests used / limit matches the recorded budget', budget ? hdr.includes(`${budget[0]}/${budget[1]}`) : hdr.includes('Unavailable'), budget ? `${budget[0]} / ${budget[1]}` : 'Unavailable')
    check('usage tab labels both scopes and shows cost as Unavailable', usage.includes('Total for this run: all stored model calls') && usage.includes('Latest job: budget used / limit') && (usage.match(/Unavailable –/g) ?? []).length === 3 && !/\$\s?\d/.test(usage))

    const claimQ = reportClaims[0] ? `?claim=${reportClaims[0].claim_id}` : ''
    await page.goto(`http://localhost:5173/#/runs/${runId}/report${claimQ}`)
    await sleep(1500)
    const before = await text('main header')
    await page.reload()
    await page.locator('main h1').first().waitFor()
    await sleep(2000)
    check('refresh keeps the run, status and summary', before === (await text('main header')))
    if (claimQ) check('refresh keeps the open claim inspector', (await page.getByRole('heading', { name: /inspector/ }).count()) === 1, page.url())
    check('the run is in the recent-runs list', (await text('nav[aria-label="Research navigation"]')).includes(`Run ${runId}`))

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`http://localhost:5173/#/runs/${runId}/report`)
    await sleep(2000)
    await shot('08-narrow-report')
    const overflow = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
    check('390 px layout has no horizontal overflow', overflow[0] === overflow[1], overflow.join(' / '))
  }
  const bad = unexpectedHttpErrors(httpErrors)
  check('no HTTP error responses reached the browser (none suppressed)', bad.length === 0, bad.map((b) => `${b.method} ${b.status} ${b.url}`).join('; '))
  check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))
} catch (e) {
  fatal = e
  check('script completed without an exception', false, e.stack?.split('\n').slice(0, 3).join(' | ') ?? String(e))
} finally {
  if (browser) await browser.close().catch(() => {})
  // Authoritative state for the cleanup decision, probed from the API (never from the screen)
  let finalState = jobState
  if (backend && runId && jobState !== 'none') {
    try { finalRun = await api(`/runs/${runId}`); finalState = isActiveRun(finalRun) ? 'active' : 'terminal' } catch { finalState = 'unreachable' }
  }
  const decision = cleanupDecision({ ownedByScript: Boolean(backend), jobState: finalState })
  if (backend && decision.action === 'stop-owned') { backend.stop(); console.log(`cleanup: stopped the backend this script started (pid ${backend.pid}): ${decision.reason}`) }
  else if (backend) { backend.release(); console.log(`CLEANUP: backend pid ${backend.pid} LEFT RUNNING on :${PORT}: ${decision.reason}. Stop it yourself with: taskkill /PID ${backend.pid} /T /F`) }
  const summary = { runId, backend: BACKEND, backendPid: backend?.pid ?? null, jobStateAtCleanup: finalState, cleanup: decision, finalRun: finalRun && { status: finalRun.status, verify_status: finalRun.verify_status, complete_status: finalRun.complete_status, report_outcome: finalRun.report_outcome }, checks, fatal: fatal ? String(fatal) : null }
  fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1))
  console.log(`\n${checks.filter((c) => c.result === 'PASS').length} PASS, ${checks.filter((c) => c.result === 'FAIL').length} FAIL, ${checks.filter((c) => c.result === 'NOTE').length} NOTE`)
  process.exitCode = checks.some((c) => c.result === 'FAIL') ? 1 : 0
}
