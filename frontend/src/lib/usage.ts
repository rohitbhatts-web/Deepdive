import type { Completion, ResearchStatus, RunEvent } from '@/api/types'

export type Usage = ResearchStatus['model_usage']
export type Budget = Record<string, [number, number]>

const fmt = (n: number) => n.toLocaleString('en-US')

/**
 * What the stored calls say about token usage. `none`: no model call was recorded. `unknown`: calls were recorded but none reported usage (a failed or timed-out
 * call returns none). `partial`: some did. `full`: every call did. A reported zero is a real number and stays 0. `legacy`: an older API that does not send
 * `calls_with_usage`, so a 0 total cannot be told from an unknown one and a partial total cannot be told from a complete one.
 */
export type UsageState = 'none' | 'unknown' | 'partial' | 'full' | 'legacy'

export function usageState(u: Usage): UsageState {
  if (u.model_calls === 0) return 'none'
  if (typeof u.calls_with_usage !== 'number') return 'legacy'
  if (u.calls_with_usage === 0) return 'unknown'
  return u.calls_with_usage < u.model_calls ? 'partial' : 'full'
}

/** One token total as text: "No calls", "Unavailable" (calls exist but none reported it) or the number, which may be 0. */
export function tokenValue(total: number | null, u: Usage): string {
  const state = usageState(u)
  if (state === 'none') return 'No calls'
  if (state === 'unknown' || total === null) return 'Unavailable'
  if (state === 'legacy' && total === 0) return 'Unavailable' // an older server sums unknown usage to 0, so a zero cannot be trusted there
  return fmt(total)
}

/** The sentence that says how complete a token total is, or null when every call reported usage. */
export function usageNote(u: Usage): string | null {
  const state = usageState(u)
  if (state === 'none') return 'No model call has been recorded for this run.'
  if (state === 'unknown') return `None of the ${fmt(u.model_calls)} recorded call${u.model_calls === 1 ? '' : 's'} reported token usage, so the totals are unknown, not zero.`
  if (state === 'legacy') return 'This server does not say how many calls reported token usage, so a total may leave calls out, and a total of 0 is shown as Unavailable.'
  if (state === 'partial') return `Token usage was reported by ${fmt(u.calls_with_usage!)} of ${fmt(u.model_calls)} calls. The totals leave out the other ${fmt(u.model_calls - u.calls_with_usage!)}, so they are lower bounds.`
  return null
}

const isBudget = (b: unknown): b is Budget =>
  typeof b === 'object' && b !== null && Array.isArray((b as Budget).model_requests) && (b as Budget).model_requests.length === 2 && (b as Budget).model_requests.every((n) => typeof n === 'number')

const JOB: Record<string, string> = { research_finished: 'collection job', verification_finished: 'verification job', completion_finished: 'completion job' }

export interface JobBudget { budget: Budget; source: string; eventType: string; eventId: number | null; at: string | null }

/**
 * The request budget recorded by the most recent job that stored one, chosen by event order (the later event wins), not by event kind: a run whose collection or
 * verification failed has no completion job, and its own final budget is the latest one. `source` names the job so the figure is never taken for another's.
 * Falls back to the completion record when no event list is available.
 */
export function latestJobBudget(events: RunEvent[], completion: Completion | null): JobBudget | null {
  let best: RunEvent | null = null
  for (const e of events) {
    if (isBudget(e.payload.budget_used_of_limit) && (best === null || e.id > best.id)) best = e
  }
  if (best) return { budget: best.payload.budget_used_of_limit as Budget, source: JOB[best.event_type] ?? best.event_type, eventType: best.event_type, eventId: best.id, at: best.created_at }
  const c = completion?.budget_used_of_limit
  return isBudget(c) ? { budget: c, source: 'completion job', eventType: 'completion_finished', eventId: null, at: null } : null
}

/** "used / limit" model requests of the latest job, or Unavailable when no job has stored its final budget. */
export function latestJobRequests(events: RunEvent[], completion: Completion | null): string {
  const r = latestJobBudget(events, completion)?.budget.model_requests
  return r ? `${fmt(r[0])} / ${fmt(r[1])}` : 'Unavailable'
}
