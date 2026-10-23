// Failure wording, stage labels, preserved-evidence counts and the explicit retry panel. FIXTURES only; nothing here talks to a backend.
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RunPage } from '@/components/RunPage'
import type { Completion, ResearchStatus, RetryPlan } from '@/api/types'
import { failureNotice } from '@/lib/claimState'
import { STAGE_LABEL, eventLabel } from '@/lib/events'
import { RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); vi.useRealTimers(); window.location.hash = '' })

const REASON = "drafter failed: Mesh call failed for model 'qwen/qwen3-235b-a22b-instruct-2507': APITimeoutError: Request timed out. (request timeout 60s)"
const failedRun = { ...RUN, status: 'collection_finished', verify_status: 'failed', complete_status: 'failed', report_outcome: null, stop_reason: null }
const research: ResearchStatus = {
  run_id: 7, status: 'collection_finished', stop_reason: null, updated_at: RUN.updated_at, subquestions_by_status: {},
  counts: { searches: 8, sources: 9, passages: 155, page_fetch_attempts: 12 },
  model_usage: { model_calls: 11, failed_calls: 1, prompt_tokens: 9670, completion_tokens: 1718, total_tokens: 11388 },
  limits: { model_requests: 20, search_queries: 8, page_fetches: 12 },
}
const completion: Completion = {
  run_id: 7, complete_status: 'failed', stop_reason: REASON, corrective_rounds: 0, max_corrective_rounds: 2, coverage_round: null, coverage: null,
  report_id: null, report_outcome: null, budget_used_of_limit: { model_requests: [11, 20] },
}
const plan: RetryPlan = {
  run_id: 7, allowed: true, reasons: [], attempt: 1, stored_evidence: { sources: 9, passages: 155, claims: 0 },
  new_job_limits: { model_requests: 20, search_queries: 0, page_fetches: 0, output_tokens: 15000, elapsed_seconds: 300, candidate_claims: 4 },
  retrieval: 'disabled', previous_job: { stop_reason: REASON, status: 'failed', finished_at: '2026-01-01T00:05:00Z', budget_used_of_limit: { model_requests: [11, 20] } }, note: '',
}
const routes = (over: Record<string, unknown> = {}) => ({ '/runs/7': failedRun, '/runs/7/research': research, '/runs/7/completion': completion, '/runs/7/events': [], '/runs/7/retry': plan, ...over })

describe('failure wording', () => {
  it('names the failed stage even though collection_finished is what is stored', () => {
    expect(failureNotice(failedRun, completion)).toEqual({ title: 'Drafting failed after collection finished', detail: REASON })
    const base = { ...failedRun }
    expect(failureNotice(base, { stop_reason: 'coverage assessment failed: x' })?.title).toBe('Coverage assessment failed after collection finished')
    expect(failureNotice(base, { stop_reason: 'report assembly failed: x' })?.title).toBe('Report assembly failed after collection finished')
    expect(failureNotice({ ...base, complete_status: 'completed' }, { stop_reason: 'verifier failed: x' })?.title).toBe('Verification failed after collection finished')
    expect(failureNotice({ ...base, verify_status: 'completed' }, { stop_reason: 'something else' })?.title).toBe('Completion failed after collection finished')
    expect(failureNotice({ ...base, status: 'failed', stop_reason: 'interrupted: server restarted during research' }, completion)).toEqual({ title: 'Collection failed', detail: 'interrupted: server restarted during research' })
    expect(failureNotice({ ...base, verify_status: 'completed', complete_status: 'completed' }, completion)).toBeNull()
    expect(failureNotice({ ...base, verify_status: 'stopped', complete_status: 'stopped' }, completion)).toBeNull() // a stop is described separately
  })

  it('shows a failure notice from the failed stage, the reason, and the preserved evidence counts', async () => {
    stubFetch(routes())
    render(<RunPage runId={7} tab="report" claimId={null} interval={50} />)
    expect(await screen.findByText('Drafting failed after collection finished')).toBeInTheDocument()
    expect(screen.getByText(/APITimeoutError: Request timed out/)).toBeInTheDocument()
    expect(screen.getByText(/Collected evidence is preserved: 9 source\(s\), 155 passages/)).toBeInTheDocument()
    expect(screen.queryByText('Finished because')).toBeNull() // a failure is never described as "finished"
    expect(screen.getByLabelText('Run summary')).toHaveTextContent('Sources9') // the collected-source count stays visible
    expect(screen.getByLabelText('Run summary')).toHaveTextContent('11 (1 failed)')
  })

  it('a completed run keeps the neutral notice and a collection failure shows no retry panel', async () => {
    stubFetch(routes({ '/runs/7': { ...failedRun, verify_status: 'completed', complete_status: 'completed', report_outcome: 'claims_with_gaps' }, '/runs/7/completion': { ...completion, complete_status: 'completed', stop_reason: 'corrective rounds exhausted (1)' } }))
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByText('Finished because')).toBeInTheDocument()
    cleanup()
    stubFetch(routes({ '/runs/7': { ...failedRun, status: 'failed', stop_reason: 'interrupted: server restarted during research' } }))
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByText('Collection failed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry from stored evidence/ })).toBeNull()
  })
})

describe('stage labels', () => {
  it('every stage the backend emits has a friendly label, and planner is not shown raw', () => {
    const emitted = ['api', 'correct', 'coverage', 'drafter', 'graph', 'ingest', 'integrity', 'planner', 'rejudge', 'relevance', 'report', 'researcher', 'reviser', 'startup', 'verifier']
    for (const s of emitted) expect(STAGE_LABEL[s], s).toBeTruthy()
    expect(STAGE_LABEL.planner).toBe('Planning')
    expect(eventLabel({ id: 1, event_type: 'job_retry_started', stage: 'api', created_at: '', payload: {} })).toBe('Retry from stored evidence started')
    expect(eventLabel({ id: 1, event_type: 'plan_created', stage: 'planner', created_at: '', payload: {} })).toBe('Plan created')
  })
})

describe('retry panel', () => {
  it('explains the retry and stays disabled until the person confirms; one click sends one confirmed request', async () => {
    const calls = stubFetch(routes({ '/runs/7/retry': (init?: RequestInit) => (init?.method === 'POST' ? { run_id: 7, status: 'completing', attempt: 1 } : plan) }))
    render(<RunPage runId={7} tab="report" claimId={null} interval={50} />)
    const button = await screen.findByRole('button', { name: /Retry from stored evidence/ })
    expect(screen.getByText(/It makes no search and no page fetch/)).toBeInTheDocument()
    expect(screen.getByLabelText('Run summary')).toBeInTheDocument()
    expect(screen.getByText(/11 of 20 model requests used/)).toBeInTheDocument() // the failed job's budget, shown as it was recorded
    expect(button).toBeDisabled()
    await userEvent.click(button)
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0) // a disabled button sends nothing
    await userEvent.click(screen.getByRole('checkbox', { name: /I understand this makes paid model requests \(up to 20\)/ }))
    expect(button).toBeEnabled()
    await userEvent.click(button)
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(calls.find((c) => c.method === 'POST')?.path).toBe('/runs/7/retry')
  })

  it('shows why a retry is unavailable instead of a button', async () => {
    stubFetch(routes({ '/runs/7/retry': { ...plan, allowed: false, reasons: ['3 claim(s) already exist: a retry only drafts from stored evidence when no claim was drafted yet'] } }))
    render(<RunPage runId={7} tab="report" claimId={null} interval={50} />)
    expect(await screen.findByText('Retry from stored evidence is not available')).toBeInTheDocument()
    expect(screen.getByText(/3 claim\(s\) already exist/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry from stored evidence/ })).toBeNull()
  })

  it('shows a refused retry as an error and lets the person try again deliberately', async () => {
    stubFetch(routes({ '/runs/7/retry': (init?: RequestInit) => (init?.method === 'POST' ? { __status: 409, detail: 'the run changed state' } : plan) }))
    render(<RunPage runId={7} tab="report" claimId={null} interval={50} />)
    await userEvent.click(await screen.findByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: /Retry from stored evidence/ }))
    expect(await screen.findByText('the run changed state')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry from stored evidence/ })).toBeEnabled()
  })
})
