// The hosted recorded demo: banner, no way to start work, per-run labels. FIXTURES only; nothing here talks to a backend.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '@/App'
import { RunPage } from '@/components/RunPage'
import type { Completion, DemoInfo, ResearchStatus } from '@/api/types'
import { RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); vi.useRealTimers(); window.location.hash = '' })

const DEMO: DemoInfo = {
  read_only: true, label: 'Recorded demo (read-only)', notice: 'Recorded runs; nothing is researched live.', withholding: 'Uncleared page text is replaced by a marker.',
  limitations: ['Reports can be incomplete or empty.'], runs: [{ run_id: 7, note: 'Recorded run: a partial report.' }, { run_id: 8, title: 'Controlled development check (not a research run)', note: 'A development check on stored evidence.' }],
}
const REASON = "drafter failed: Mesh call failed for model 'm': APITimeoutError: Request timed out. (request timeout 60s)"
const failedRun = { ...RUN, id: 7, status: 'collection_finished', verify_status: 'failed', complete_status: 'failed', report_outcome: null, stop_reason: null }
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
const runPage = { '/runs?limit=30': { items: [], total: 0, limit: 30, offset: 0 }, '/runs/7': failedRun, '/runs/7/research': research, '/runs/7/completion': completion, '/runs/7/events': [] }

describe('read-only demo', () => {
  it('shows the banner and an explanation instead of the research form, and offers no start action', async () => {
    window.location.hash = '#/'
    stubFetch({ '/demo': DEMO, '/runs?limit=30': { items: [], total: 0, limit: 30, offset: 0 } })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'DeepDive: recorded demo' })).toBeInTheDocument()
    expect(screen.getByLabelText('About this demo')).toHaveTextContent('recorded development runs, not live research')
    expect(screen.getByRole('link', { name: /Open run 7/ })).toHaveAttribute('href', '#/runs/7/overview')
    expect(screen.getByText('Reports can be incomplete or empty.')).toBeInTheDocument()
    expect(screen.getByText('Controlled development check (not a research run)')).toBeInTheDocument() // a development check is labelled as one, not as research
    expect(screen.queryByLabelText('Research question')).toBeNull()
    expect(screen.queryByRole('button', { name: /Start research/ })).toBeNull()
    expect(screen.getAllByRole('link', { name: /About this demo/ }).length).toBeGreaterThanOrEqual(2) // the banner and the sidebar entry
  })

  it('a deployment that is not the demo keeps the research form (and a failed /demo does not hide it)', async () => {
    window.location.hash = '#/'
    stubFetch({ '/demo': { read_only: false }, '/limits': { limits: { model_requests: 20 }, question_min_chars: 3, question_max_chars: 2000, max_correction_rounds: 2 }, '/runs?limit=30': { items: [], total: 0, limit: 30, offset: 0 } })
    render(<App />)
    expect(await screen.findByLabelText('Research question')).toBeInTheDocument()
    expect(screen.queryByLabelText('About this demo')).toBeNull()
    cleanup()
    stubFetch({ '/limits': { limits: { model_requests: 20 }, question_min_chars: 3, question_max_chars: 2000, max_correction_rounds: 2 }, '/runs?limit=30': { items: [], total: 0, limit: 30, offset: 0 } }) // /demo missing: 404
    render(<App />)
    expect(await screen.findByLabelText('Research question')).toBeInTheDocument()
  })

  it('a failed recorded run is labelled and offers no retry', async () => {
    window.location.hash = '#/runs/7'
    stubFetch({ '/demo': DEMO, ...runPage, '/runs/7/retry': { run_id: 7, allowed: true, reasons: [] } })
    render(<App />)
    expect(await screen.findByText('Drafting failed after collection finished')).toBeInTheDocument()
    expect(screen.getByText('Recorded run: a partial report.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('the recorded-run note can be expanded on narrow screens and is always in the page', async () => {
    window.location.hash = '#/runs/7'
    stubFetch({ '/demo': DEMO, ...runPage })
    render(<App />)
    const toggle = await screen.findByRole('button', { name: 'Read the full note' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Recorded run: a partial report.')).toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('without the demo flag the same failed run still offers the explicit retry panel', async () => {
    stubFetch({ ...runPage, '/runs/7/retry': { run_id: 7, allowed: true, reasons: [], attempt: 1, stored_evidence: { sources: 9, passages: 155, claims: 0 }, new_job_limits: { model_requests: 20, search_queries: 0, page_fetches: 0, output_tokens: 15000, elapsed_seconds: 300, candidate_claims: 4 }, retrieval: 'disabled', previous_job: null, note: '' } })
    render(<RunPage runId={7} tab="report" claimId={null} interval={50} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument())
  })
})
