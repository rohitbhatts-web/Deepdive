// Pure logic of the UI-to-backend acceptance script (no browser, no network side effects of its own beyond injected functions), so it can be tested.
// The three rules this module exists to enforce, each learned from a real failure (the first live acceptance run was cut off by its own cleanup):
//  1. A run is over only when its STORED job status says so, never because something is missing from the screen.
//  2. A paid backend job is never stopped while it may still be running (timeout, assertion failure, unreachable API all leave it alone).
//  3. The script stops only the process tree it started itself, and refuses to start when something already owns the port.

import net from 'node:net'

export const ACTIVE_RUN_STATES = { status: ['researching'], verify_status: ['verifying'], complete_status: ['completing'] }

/** True while any of the run's jobs is still running, from the stored run fields (GET /runs/{id}). */
export function isActiveRun(run) {
  return Object.entries(ACTIVE_RUN_STATES).some(([field, values]) => values.includes(run?.[field]))
}

/**
 * Wait until the run's stored job status is terminal.
 * `pageReady` (rendering) is awaited first and never decides completion. Returns {state, run, ticks}:
 *   terminal    - the stored status is terminal
 *   timeout     - still active when the time ran out (the job may still be running: do not stop the backend)
 *   unreachable - the API failed `maxFailures` times in a row (state unknown: do not stop the backend)
 */
export async function waitForTerminal({ getRun, pageReady = async () => {}, sleep, now = Date.now, timeoutMs, pollMs = 1000, maxFailures = 5, onTick = () => {} }) {
  await pageReady()
  const started = now()
  let failures = 0
  let ticks = 0
  let run = null
  while (true) {
    try {
      run = await getRun()
      failures = 0
    } catch {
      failures += 1
      if (failures >= maxFailures) return { state: 'unreachable', run, ticks }
    }
    if (run && !isActiveRun(run)) return { state: 'terminal', run, ticks }
    if (now() - started >= timeoutMs) return { state: 'timeout', run, ticks }
    ticks += 1
    if (run) await onTick(run, ticks)
    await sleep(pollMs)
  }
}

/**
 * What cleanup may do with the backend. Only 'stop-owned' ever stops anything, and only when the script started the process and the job state is
 * KNOWN to be terminal. Active, timed-out, unreachable or unknown job states, and processes the script did not start, are left alone.
 */
export function cleanupDecision({ ownedByScript, jobState }) {
  if (!ownedByScript) return { action: 'leave', reason: 'the backend was not started by this script' }
  if (jobState === 'terminal') return { action: 'stop-owned', reason: 'the job is terminal and the process is owned by this script' }
  if (jobState === 'none') return { action: 'stop-owned', reason: 'no job was started and the process is owned by this script' }
  return { action: 'leave', reason: `the job state is "${jobState}": a paid job may still be running` }
}

/** Every HTTP response with status >= 400 is unexpected unless a rule explicitly allows it. No error is suppressed by default. */
export function unexpectedHttpErrors(responses, allow = []) {
  return responses.filter((r) => r.status >= 400 && !allow.some((a) => a.method === r.method && a.status === r.status && a.pattern.test(r.url)))
}

/** True when something is already accepting connections on the port. */
export function portInUse(port, host = '127.0.0.1', timeoutMs = 1000) {
  return new Promise((resolve) => {
    const s = net.connect({ port, host })
    const done = (v) => { s.destroy(); resolve(v) }
    s.setTimeout(timeoutMs, () => done(false))
    s.once('connect', () => done(true))
    s.once('error', () => done(false))
  })
}

/**
 * Start the backend as a process this script owns. Refuses when the port is already served (it never takes over or kills someone else's server).
 * `spawnFn(command, args, options)` returns a child with `pid`; `killTree(pid)` stops that pid and its children only.
 */
export async function startOwnedBackend({ spawnFn, killTree, port, command, args, options, inUse = portInUse, waitHealthy }) {
  if (await inUse(port)) throw new Error(`port ${port} is already in use; refusing to start (and refusing to stop whatever owns it)`)
  const child = spawnFn(command, args, options)
  const handle = {
    pid: child.pid, stopped: false,
    stop() { if (!handle.stopped) { handle.stopped = true; killTree(child.pid) } },
    // Leaving a job running must also let THIS script exit: an un-released child keeps the parent's event loop alive forever.
    release() { child.unref?.() },
  }
  try {
    await waitHealthy()
  } catch (e) {
    handle.stop() // we started it and it never became healthy: it has no job, so stopping it is safe
    throw e
  }
  return handle
}
