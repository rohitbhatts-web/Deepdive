// Stop ONLY processes that this tooling launched and can still positively identify. Never by program name, never by port, never by command-line
// pattern. A record stores the pid together with the process creation time and command line seen right after launch; before anything is stopped the
// live process under that pid must match the record exactly (a recycled pid is a different process). If ownership cannot be established the process
// is left alone and the reason is reported.
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

/** The live identity of a pid: { created, commandLine }, or null when no such process exists. Windows: CIM; elsewhere: ps. */
export function liveIdentity(pid) {
  if (process.platform === 'win32') {
    const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command',
      `$p = Get-CimInstance Win32_Process -Filter "ProcessId=${Number(pid)}"; if ($p) { @{ created = [string]$p.CreationDate.ToUniversalTime().ToString('o'); commandLine = [string]$p.CommandLine } | ConvertTo-Json -Compress }`],
    { encoding: 'utf8', windowsHide: true })
    const out = (r.stdout ?? '').trim()
    return out ? JSON.parse(out) : null
  }
  const r = spawnSync('ps', ['-o', 'lstart=,args=', '-p', String(pid)], { encoding: 'utf8' })
  const out = (r.stdout ?? '').trim()
  return out ? { created: out.slice(0, 24), commandLine: out.slice(25) } : null
}

/** Launch a process detached and write the ownership record. Returns the record. */
export function launchOwned({ label, command, args = [], options = {}, recordPath, identity = liveIdentity }) {
  const child = spawn(command, args, { stdio: 'ignore', detached: true, windowsHide: true, ...options })
  child.unref()
  const seen = identity(child.pid)
  if (!seen) throw new Error(`could not read the identity of ${label} (pid ${child.pid}); it was started but ownership cannot be recorded, stop it yourself`)
  const record = { label, pid: child.pid, created: seen.created, commandLine: seen.commandLine, recordedAt: new Date().toISOString() }
  if (recordPath) fs.writeFileSync(recordPath, JSON.stringify(record, null, 1))
  return record
}

/** Is the live process under record.pid the very process that was launched? */
export function verifyOwned(record, identity = liveIdentity) {
  const now = identity(record.pid)
  if (!now) return { owned: false, gone: true, reason: `pid ${record.pid} (${record.label}) is not running; nothing to stop` }
  if (now.created !== record.created || now.commandLine !== record.commandLine) {
    return { owned: false, gone: false, reason: `pid ${record.pid} now belongs to a different process (started ${now.created}, command ${String(now.commandLine).slice(0, 80)}); ownership of ${record.label} cannot be established, left alone` }
  }
  return { owned: true, gone: false, reason: 'identity matches the launch record' }
}

const taskkill = (pid) => spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' })

/** Stop the recorded process (and the tree it started) if, and only if, ownership is verified. */
export function stopOwned(record, { identity = liveIdentity, killTree = process.platform === 'win32' ? taskkill : (pid) => process.kill(pid) } = {}) {
  const v = verifyOwned(record, identity)
  if (!v.owned) return { stopped: false, reason: v.reason }
  killTree(record.pid)
  return { stopped: true, reason: `stopped ${record.label} (pid ${record.pid}) after verifying its identity` }
}

// CLI:  node owned.mjs stop RECORD.json [RECORD.json ...]
if (process.argv[1] && path.basename(process.argv[1]) === 'owned.mjs') {
  const [cmd, ...files] = process.argv.slice(2)
  if (cmd === 'stop') {
    for (const f of files) {
      const r = stopOwned(JSON.parse(fs.readFileSync(f, 'utf8')))
      console.log(`${r.stopped ? 'STOPPED' : 'LEFT ALONE'}: ${r.reason}`)
    }
  } else if (cmd) {
    console.error('usage: node owned.mjs stop RECORD.json [...]')
    process.exit(2)
  }
}
