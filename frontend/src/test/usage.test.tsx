// Usage reporting: unknown token totals are Unavailable (never 0), a reported zero stays 0, and the latest job's request budget is shown even when
// collection failed, chosen by event order and named by its source. FIXTURES only; nothing here talks to a backend.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Completion, ResearchStatus, RunEvent } from '@/api/types'
import { RunPage } from '@/components/RunPage'
import { latestJobBudget, latestJobRequests, tokenValue, usageNote, usageState, type Usage } from '@/lib/usage'
import { RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); window.location.hash = '' })

const usage = (over: Partial<Usage>): Usage => ({ model_calls: 0, failed_calls: 0, calls_with_usage: 0, prompt_tokens: null, completion_tokens: null, total_tokens: null, ...over })
const ev = (id: number, event_type: string, budget?: Record<string, [number, number]>): RunEvent =>
  ({ id, event_type, stage: 'graph', created_at: `2026-01-01T00:0${id % 10}:00Z`, payload: budget ? { budget_used_of_limit: budget } : {} })
const req = (used: number): Record<string, [number, number]> => ({ model_requests: [used, 20], output_tokens: [1500, 15000] })

describe('token totals', () => {
  it('tells no calls, unknown usage, partial usage, full usage and a reported zero apart', () => {
    const none = usage({})
    const unknown = usage({ model_calls: 1, failed_calls: 1 })
    const partial = usage({ model_calls: 3, failed_calls: 1, calls_with_usage: 2, prompt_tokens: 1377, completion_tokens: 626, total_tokens: 2003 })
    const full = usage({ model_calls: 2, calls_with_usage: 2, prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 })
    const zero = usage({ model_calls: 1, calls_with_usage: 1, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 })
    expect([none, unknown, partial, full, zero].map(usageState)).toEqual(['none', 'unknown', 'partial', 'full', 'full'])
    expect(tokenValue(none.prompt_tokens, none)).toBe('No calls')
    expect(tokenValue(unknown.prompt_tokens, unknown)).toBe('Unavailable')
    expect(tokenValue(partial.prompt_tokens, partial)).toBe('1,377')
    expect(tokenValue(zero.prompt_tokens, zero)).toBe('0') // a genuine reported zero is a number, not Unavailable
    expect(usageNote(none)).toMatch(/No model call/)
    expect(usageNote(unknown)).toMatch(/None of the 1 recorded call reported token usage.*unknown, not zero/)
    expect(usageNote(partial)).toMatch(/reported by 2 of 3 calls.*lower bounds/)
    expect(usageNote(full)).toBeNull()
  })
})

describe('an older API response without calls_with_usage', () => {
  const old = (over: Partial<Usage>): Usage => { const u = usage(over); delete u.calls_with_usage; return u }

  it('never shows an unverifiable 0 as a number, still shows a real total, and says what it cannot tell', () => {
    const allZero = old({ model_calls: 1, failed_calls: 1, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }) // what an older server sums unknown usage to
    const someTokens = old({ model_calls: 3, failed_calls: 1, prompt_tokens: 1377, completion_tokens: 626, total_tokens: 2003 })
    expect(usageState(allZero)).toBe('legacy')
    expect(tokenValue(allZero.prompt_tokens, allZero)).toBe('Unavailable')
    expect(tokenValue(someTokens.prompt_tokens, someTokens)).toBe('1,377')
    expect(usageNote(someTokens)).toMatch(/does not say how many calls reported token usage/)
    expect(usageState(old({}))).toBe('none') // no calls is still known from model_calls alone
    expect(tokenValue(null, allZero)).toBe('Unavailable')
  })

  it('renders without error: the tiles show Unavailable for the old 0 and the note explains it', async () => {
    const failed = { ...RUN, status: 'failed', verify_status: 'not_started', complete_status: 'not_started', report_outcome: null, stop_reason: 'planner failed: timeout' }
    stubFetch({
      '/runs/7': failed, '/runs/7/completion': { run_id: 7, complete_status: 'not_started', stop_reason: null, corrective_rounds: 0, max_corrective_rounds: 2, coverage_round: null, coverage: null, report_id: null, report_outcome: null, budget_used_of_limit: null },
      '/runs/7/research': { run_id: 7, status: 'failed', stop_reason: null, updated_at: RUN.updated_at, subquestions_by_status: {}, counts: { searches: 0, sources: 0, passages: 0, page_fetch_attempts: 0 },
        model_usage: { model_calls: 1, failed_calls: 1, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, limits: { model_requests: 20, search_queries: 8, page_fetches: 12 } },
      '/runs/7/events': [], '/runs/7/model-calls': [],
    })
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByLabelText('Run summary')).toHaveTextContent('Tokens in / out (all calls)Unavailable / Unavailable')
    expect(screen.getByText(/does not say how many calls reported token usage/)).toBeInTheDocument()
  })
})

describe('latest job budget', () => {
  const completion = (b: Record<string, [number, number]> | null): Completion =>
    ({ run_id: 7, complete_status: 'not_started', stop_reason: null, corrective_rounds: 0, max_corrective_rounds: 2, coverage_round: null, coverage: null, report_id: null, report_outcome: null, budget_used_of_limit: b })

  it('uses the failed collection job when nothing later recorded a budget, and names it', () => {
    const got = latestJobBudget([ev(1, 'research_started'), ev(5, 'research_finished', req(3)), ev(6, 'verification_skipped')], completion(null))
    expect(got).toMatchObject({ source: 'collection job', eventType: 'research_finished', eventId: 5 })
    expect(latestJobRequests([ev(5, 'research_finished', req(3))], completion(null))).toBe('3 / 20')
  })

  it('chooses by event order, not by kind: a later collection job beats an earlier completion job', () => {
    const events = [ev(5, 'research_finished', req(11)), ev(9, 'completion_finished', req(14)), ev(15, 'research_finished', req(4))]
    expect(latestJobBudget(events, completion(req(14)))).toMatchObject({ source: 'collection job', eventId: 15 })
    expect(latestJobRequests(events, completion(req(14)))).toBe('4 / 20')
    expect(latestJobBudget([ev(5, 'research_finished', req(11)), ev(9, 'verification_finished', req(6)), ev(12, 'completion_finished', req(2))], completion(req(2)))).toMatchObject({ source: 'completion job', eventId: 12 })
  })

  it('falls back to the completion record without events, and is Unavailable with no record at all', () => {
    expect(latestJobBudget([], completion(req(12)))).toMatchObject({ source: 'completion job', eventId: null })
    expect(latestJobBudget([ev(1, 'research_started')], completion(null))).toBeNull()
    expect(latestJobRequests([], completion(null))).toBe('Unavailable')
  })
})

describe('a run that failed during collection', () => {
  const failed = { ...RUN, status: 'failed', verify_status: 'not_started', complete_status: 'not_started', report_outcome: null, stop_reason: "researcher failed on subquestion 1: Mesh call failed for model 'm': InternalServerError: Error code: 503" }
  const research = (model_usage: Usage): ResearchStatus => ({ run_id: 7, status: 'failed', stop_reason: failed.stop_reason, updated_at: RUN.updated_at, subquestions_by_status: {}, counts: { searches: 0, sources: 0, passages: 0, page_fetch_attempts: 0 }, model_usage, limits: { model_requests: 20, search_queries: 8, page_fetches: 12 } })
  const completion: Completion = { run_id: 7, complete_status: 'not_started', stop_reason: null, corrective_rounds: 0, max_corrective_rounds: 2, coverage_round: null, coverage: null, report_id: null, report_outcome: null, budget_used_of_limit: null }
  const routes = (model_usage: Usage) => ({
    '/runs/7': failed, '/runs/7/research': research(model_usage), '/runs/7/completion': completion,
    '/runs/7/events': [ev(2, 'research_started'), ev(5, 'research_finished', { model_requests: [3, 20], output_tokens: [2126, 15000] })],
    '/runs/7/model-calls': [],
  })

  it('shows the collection job budget with its source, and never presents the reserved output tokens as usage', async () => {
    stubFetch(routes(usage({ model_calls: 3, failed_calls: 1, calls_with_usage: 2, prompt_tokens: 1377, completion_tokens: 626, total_tokens: 2003 })))
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByLabelText('Run summary')).toHaveTextContent('Latest job requests used / limit3 / 20')
    expect(screen.getByText(/Latest job requests used \/ limit:/)).toHaveTextContent('3 / 20 · from the collection job')
    expect(screen.getByLabelText('Run summary')).toHaveTextContent('Tokens in / out (usage from 2 of 3 calls)1,377 / 626') // partial totals say so
    expect(screen.getByText(/reported by 2 of 3 calls/)).toBeInTheDocument()
    expect(screen.getByText('Output-token cap counted (a reservation, not usage)')).toBeInTheDocument()
    expect(screen.getByText(/assumed per-call maximum for every call that reported no usage/)).toBeInTheDocument()
    expect(screen.queryByText('Output tokens')).toBeNull() // the cap is never labelled as plain usage
  })

  it('shows Unavailable, not 0, when the calls reported no usage, and No calls when there were none', async () => {
    stubFetch(routes(usage({ model_calls: 1, failed_calls: 1 })))
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByLabelText('Run summary')).toHaveTextContent('Tokens in / out (all calls)Unavailable / Unavailable')
    expect(screen.getByText('Prompt tokens').parentElement).toHaveTextContent('Prompt tokensUnavailable')
    expect(screen.getByText(/None of the 1 recorded call reported token usage, so the totals are unknown, not zero/)).toBeInTheDocument()
    cleanup()
    stubFetch(routes(usage({})))
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByLabelText('Run summary')).toHaveTextContent('Tokens in / out (all calls)No calls / No calls')
  })
})
