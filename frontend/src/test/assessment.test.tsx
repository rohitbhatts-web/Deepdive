// "Not assessed" is not "unanswered". FIXTURES only: a report built without any coverage assessment (the job ran out of budget first), and a mixed one.
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Completion, Report, ReportRequirement, ResearchStatus } from '@/api/types'
import { OverviewTab } from '@/components/OverviewTab'
import { ReportTab } from '@/components/ReportTab'
import type { Live } from '@/components/RunPage'
import { requirementBuckets } from '@/lib/claimState'
import { outcomeSummary } from '@/lib/overview'
import { CLAIM_A, CLAIM_B, REPORT, RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); window.location.hash = '' })

const req = (key: string, status: string, gap = ''): ReportRequirement => ({ requirement_key: key, subquestion_id: 1, requirement_text: `FIXTURE requirement ${key}`, status, gap })
const UNASSESSED: Report = {
  ...REPORT, coverage_round: null, relevance_judged: false, coverage_assessed: false,
  requirements: [req('Q1.R1', 'not_assessed'), req('Q1.R2', 'not_assessed'), req('Q2.R1', 'not_assessed')],
  unresolved_requirements: [], unassessed_requirements: [req('Q1.R1', 'not_assessed'), req('Q1.R2', 'not_assessed'), req('Q2.R1', 'not_assessed')],
}
const MIXED: Report = {
  ...REPORT, requirements: [req('Q1.R1', 'addressed'), req('Q1.R2', 'unresolved', 'no evidence on writers'), req('Q2.R1', 'not_assessed')],
  unresolved_requirements: [req('Q1.R2', 'unresolved', 'no evidence on writers')], unassessed_requirements: [req('Q2.R1', 'not_assessed')],
}

describe('buckets', () => {
  it('keeps requirements assessed as unanswered apart from requirements never assessed', () => {
    const { unanswered, unassessed } = requirementBuckets(MIXED.requirements)
    expect(unanswered.map((r) => r.requirement_key)).toEqual(['Q1.R2'])
    expect(unassessed.map((r) => r.requirement_key)).toEqual(['Q2.R1'])
  })

  it('says the requirements were not assessed instead of counting them as unanswered', () => {
    const o = outcomeSummary(RUN, UNASSESSED, null, UNASSESSED.requirements)
    expect(o.headline).toMatch(/requirements were not assessed/)
    expect(o.detail).toMatch(/No coverage assessment was made/)
    expect(o.detail).not.toMatch(/still not fully answered/)
    const m = outcomeSummary(RUN, MIXED, null, MIXED.requirements)
    expect(m.detail).toMatch(/^1 of 3 requirements is still not fully answered\. 1 was not assessed/)
  })
})

describe('report tab', () => {
  const show = (rep: Report) => {
    stubFetch({ '/runs/7/report': rep, '/runs/7/claims': [CLAIM_A, CLAIM_B] })
    render(<ReportTab run={RUN} claimId={null} version={0} active={false} />)
  }

  it('labels a report without any coverage assessment as not assessed, never as unresolved', async () => {
    show(UNASSESSED)
    expect(await screen.findByText(/Report 3: Requirements not assessed/)).toBeInTheDocument()
    expect(screen.getByText(/No coverage assessment was made, so the requirements were not assessed/)).toBeInTheDocument()
    const reqs = screen.getByRole('region', { name: /Requirements/ })
    expect(within(reqs).getAllByText('Not assessed')).toHaveLength(3)
    expect(within(reqs).queryByText('Unresolved')).toBeNull()
    expect(screen.queryByText(/No requirement was left unresolved/)).toBeNull()  // nor the opposite claim
    expect(screen.getByText(/these requirements were not assessed/)).toBeInTheDocument()
  })

  it('keeps the existing wording for an assessed report and marks only the unassessed one', async () => {
    show(MIXED)
    expect(await screen.findByText(/Report 3: Claims with gaps/)).toBeInTheDocument()
    const reqs = screen.getByRole('region', { name: /Requirements/ })
    expect(within(reqs).getByText('Unresolved')).toBeInTheDocument()
    expect(within(reqs).getByText('Not assessed')).toBeInTheDocument()
    expect(within(reqs).getByText('Addressed')).toBeInTheDocument()
  })
})

describe('overview', () => {
  const completion = { run_id: 7, complete_status: 'stopped', stop_reason: 'max_model_requests reached (20)', corrective_rounds: 0, max_corrective_rounds: 2, coverage_round: null, coverage: null, report_id: 3, report_outcome: 'claims_with_gaps' } as unknown as Completion
  const research = { run_id: 7, status: 'collection_finished', stop_reason: null, updated_at: RUN.updated_at, subquestions_by_status: {}, counts: { searches: 1, sources: 1, passages: 1, page_fetch_attempts: 1 },
    model_usage: { model_calls: 1, failed_calls: 0, calls_with_usage: 1, prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 }, limits: { model_requests: 20, search_queries: 8, page_fetches: 12 } } as ResearchStatus
  const live = { run: RUN, research, completion, events: [] } as unknown as Live

  it('does not list never-assessed requirements under "Still unanswered"', async () => {
    stubFetch({ '/runs/7/report': UNASSESSED, '/runs/7/claims': [CLAIM_A, CLAIM_B], '/runs/7/sources': [], '/runs/7/subquestions': [{ id: 1, question: 'FIXTURE' }],
      '/runs/7/coverage': { round: null, summary: {}, requirements: [] }, '/runs/7/model-calls': [] })
    render(<OverviewTab run={RUN} live={live} version={0} claimId={null} />)
    const open = await screen.findByRole('region', { name: /Still unanswered/ })
    expect(within(open).queryAllByRole('listitem')).toHaveLength(0)
    expect(within(open).queryByText(/Open/)).toBeNull()
    expect(within(open).getByText(/3 requirements were not assessed: no coverage assessment covers them, so whether they are answered is unknown\./)).toBeInTheDocument()
    expect(screen.queryByText(/Every requirement was assessed as addressed/)).toBeNull()
  })
})
