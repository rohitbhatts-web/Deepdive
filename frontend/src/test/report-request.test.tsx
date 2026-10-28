// The report is requested only when the run summary says one exists, so the browser logs no 404 for a missing report. FIXTURES only.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReportTab } from '@/components/ReportTab'
import { RunPage } from '@/components/RunPage'
import type { Completion, ResearchStatus } from '@/api/types'
import { CLAIM_A, CLAIM_B, REPORT, RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); vi.useRealTimers(); window.location.hash = '' })

describe('report request', () => {
  it('does not ask for a report when the run has none (no 404 request at all)', async () => {
    const calls = stubFetch({ '/runs/7/claims': [] })
    render(<ReportTab run={{ ...RUN, report_outcome: null, complete_status: 'completing' }} claimId={null} version={0} active />)
    expect(await screen.findByText('The report is not written yet')).toBeInTheDocument()
    expect(calls.filter((c) => c.path === '/runs/7/report')).toHaveLength(0)
  })

  it('fetches the report as soon as the run summary reports one', async () => {
    const calls = stubFetch({ '/runs/7/report': REPORT, '/runs/7/claims': [CLAIM_A, CLAIM_B] })
    const { rerender } = render(<ReportTab run={{ ...RUN, report_outcome: null }} claimId={null} version={0} active />)
    expect(await screen.findByText('The report is not written yet')).toBeInTheDocument()
    rerender(<ReportTab run={{ ...RUN, report_outcome: 'claims_with_gaps' }} claimId={null} version={0} active />)
    expect(await screen.findByText(/Report 3: Claims with gaps/)).toBeInTheDocument()
    expect(calls.filter((c) => c.path === '/runs/7/report')).toHaveLength(1)
  })

  it('still shows an unexpected 404 for a run that claims to have a report as a failure, not silence', async () => {
    stubFetch({ '/runs/7/report': { __status: 500, detail: 'database is locked' }, '/runs/7/claims': [] })
    render(<ReportTab run={{ ...RUN, report_outcome: 'claims_with_gaps' }} claimId={null} version={1} active={false} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('database is locked')
  })
})

describe('failed run header', () => {
  const research: ResearchStatus = {
    run_id: 7, status: 'failed', stop_reason: 'interrupted: server restarted during research', updated_at: RUN.updated_at, subquestions_by_status: {},
    counts: { searches: 1, sources: 0, page_fetch_attempts: 0 }, model_usage: { model_calls: 2, failed_calls: 0, calls_with_usage: 2, prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    limits: { model_requests: 20, search_queries: 8, page_fetches: 12 },
  }
  const completion: Completion = {
    run_id: 7, complete_status: 'failed', stop_reason: 'not started: the server restarted during research', corrective_rounds: 0, max_corrective_rounds: 2,
    coverage_round: null, coverage: null, report_id: null, report_outcome: null, budget_used_of_limit: null,
  }

  it('shows one failure notice, with the collection reason, and no second "finished because" notice', async () => {
    const failed = { ...RUN, status: 'failed', verify_status: 'failed', complete_status: 'failed', stop_reason: research.stop_reason, report_outcome: null }
    stubFetch({ '/runs/7': failed, '/runs/7/research': research, '/runs/7/completion': completion, '/runs/7/events': [] })
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByText('Collection failed')).toBeInTheDocument()
    expect(screen.getByText('interrupted: server restarted during research')).toBeInTheDocument()
    expect(screen.queryByText('Finished because')).toBeNull()
    expect(screen.queryByText(/server restarted during completion/)).toBeNull()
  })
})
