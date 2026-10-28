// Phase 5 audit tests. Fixtures only (see fixtures.ts); none of these says anything about live runs.
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Inspector } from '@/components/Inspector'
import { ReportTab } from '@/components/ReportTab'
import { RunPage } from '@/components/RunPage'
import { Sidebar } from '@/components/Sidebar'
import { get } from '@/api/client'
import type { Completion, ResearchStatus, RunSummary } from '@/api/types'
import { runStatusLabel } from '@/lib/claimState'
import { CLAIM_A, REPORT, RUN, stubFetch } from './fixtures'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  window.location.hash = ''
})

const RESEARCH: ResearchStatus = {
  run_id: 7, status: 'collection_finished', stop_reason: null, updated_at: RUN.updated_at, subquestions_by_status: {},
  counts: { searches: 2, sources: 3, page_fetch_attempts: 4 },
  model_usage: { model_calls: 21, failed_calls: 0, calls_with_usage: 21, prompt_tokens: 1000, completion_tokens: 200, total_tokens: 1200 },
  limits: { model_requests: 15, search_queries: 2, page_fetches: 4 },
}
const COMPLETION: Completion = {
  run_id: 7, complete_status: 'completed', stop_reason: 'corrective rounds exhausted (1)', corrective_rounds: 1, max_corrective_rounds: 2,
  coverage_round: 1, coverage: null, report_id: 3, report_outcome: 'claims_with_gaps',
  budget_used_of_limit: { model_requests: [12, 15], search_queries: [2, 2] },
}
const runRoutes = (over: Record<string, unknown> = {}) => ({
  '/runs/7': RUN, '/runs/7/research': RESEARCH, '/runs/7/completion': COMPLETION, '/runs/7/events': [], '/runs/7/report': REPORT,
  '/runs/7/claims': [], '/runs/7/model-calls': [], ...over,
})

describe('usage scope labels', () => {
  it('header separates total model calls for the run from the latest job requests used / limit', async () => {
    stubFetch(runRoutes())
    render(<RunPage runId={7} tab="report" claimId={null} interval={50} />)
    const dl = await screen.findByLabelText('Run summary')
    expect(dl).toHaveTextContent('Total model calls for this run21')
    expect(dl).toHaveTextContent('Latest job requests used / limit12 / 15')
  })

  it('shows Unavailable, not a number, when no job budget was recorded', async () => {
    stubFetch(runRoutes({ '/runs/7/completion': { ...COMPLETION, budget_used_of_limit: null } }))
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByLabelText('Run summary')).toHaveTextContent('Latest job requests used / limitUnavailable')
    expect(await screen.findByText(/Latest job requests used \/ limit:/)).toHaveTextContent('Unavailable')
  })

  it('usage tab labels both scopes and invents no cost', async () => {
    stubFetch(runRoutes())
    render(<RunPage runId={7} tab="usage" claimId={null} interval={50} />)
    expect(await screen.findByText('Total for this run: all stored model calls')).toBeInTheDocument()
    expect(screen.getByText('Latest job: budget used / limit')).toBeInTheDocument()
    expect(screen.getByText(/Latest job requests used \/ limit:/)).toHaveTextContent('12 / 15')
    expect(screen.getAllByText(/Unavailable –/)).toHaveLength(3) // estimated, calculated, provider-reported cost
    expect(screen.queryByText(/\$\s?\d/)).toBeNull()
  })
})

describe('states and semantics', () => {
  it('names a finished run without a report as such, and a never-started run as not started', () => {
    const base = { status: 'collection_finished', verify_status: 'completed', complete_status: 'completed', report_outcome: null }
    expect(runStatusLabel(base).label).toBe('Completed, no report')
    expect(runStatusLabel({ ...base, status: 'created', verify_status: 'not_started', complete_status: 'not_started' }).label).toBe('Not started')
    expect(runStatusLabel({ ...base, status: 'researching' }).label).toBe('Running')
  })

  it('does not claim a never-started run "stopped"', async () => {
    stubFetch({ '/runs/7/claims': [] })
    render(<ReportTab run={{ ...RUN, status: 'created', verify_status: 'not_started', complete_status: 'not_started' }} claimId={null} version={1} active={false} />)
    expect(await screen.findByText('Research has not started')).toBeInTheDocument()
    expect(screen.queryByText(/stopped before/)).toBeNull()
  })

  it('section links are navigation with aria-current, not an incomplete tab widget', async () => {
    stubFetch(runRoutes())
    render(<RunPage runId={7} tab="coverage" claimId={null} interval={50} />)
    const nav = await screen.findByRole('navigation', { name: 'Run sections' })
    expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent('Coverage')
    expect(screen.queryByRole('tab')).toBeNull()
  })

  it('announces the run state in a status region', async () => {
    stubFetch(runRoutes())
    render(<RunPage runId={7} tab="report" claimId={null} interval={50} />)
    expect(await screen.findByText('Run 7: Completed with gaps.')).toHaveAttribute('role', 'status')
  })

  it('moves focus to the inspector when a claim opens', async () => {
    stubFetch({ '/runs/7/claims/1': CLAIM_A })
    render(<Inspector runId={7} claimId={1} highlight={null} onClose={() => {}} />)
    const heading = await screen.findByRole('heading', { name: 'Claim 1 inspector' })
    await waitFor(() => expect(heading).toHaveFocus())
  })

  it('a missing run gets a heading and a readable error; a non-JSON 500 reads as backend trouble', async () => {
    globalThis.fetch = vi.fn(async () => new Response('Internal Server Error', { status: 500 })) as typeof fetch
    await expect(get('/runs/1')).rejects.toThrow('The backend is not responding correctly (HTTP 500).')
    stubFetch({})
    render(<RunPage runId={99} tab="report" claimId={null} interval={50} />)
    expect(await screen.findByRole('heading', { name: 'Run 99 could not be opened' })).toBeInTheDocument()
  })

  it('shows source URLs with a non-http scheme as plain text, never as a link', async () => {
    const hostile = { ...CLAIM_A, evidence: [{ ...CLAIM_A.evidence[0], passage: { ...CLAIM_A.evidence[0].passage!, final_url: 'javascript:alert(1)', title: '<img src=x onerror=alert(1)>' } }] }
    stubFetch({ '/runs/7/claims/1': hostile })
    const { container } = render(<Inspector runId={7} claimId={1} highlight={null} onClose={() => {}} />)
    await screen.findByText('javascript:alert(1)')
    expect(container.querySelector('a[href^="javascript"]')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument()
  })
})

const advance = async (ms: number) => { for (let t = 0; t < ms; t += 1000) await act(async () => { await vi.advanceTimersByTimeAsync(1000) }) }

describe('recent-runs refresh', () => {
  const page = (items: Partial<RunSummary>[]) => ({ items: items.map((i) => ({ ...RUN, ...i })), total: items.length, limit: 30, offset: 0 })

  it('refreshes while a listed run is active and stops once none is', async () => {
    vi.useFakeTimers()
    let n = 0
    const calls = stubFetch({ '/runs?limit=30': () => (++n < 3 ? page([{ id: 1, status: 'researching', complete_status: 'completing' }]) : page([{ id: 1 }])) })
    render(<Sidebar activeRunId={null} version={0} />)
    await advance(1000)
    expect(screen.getByText('Running')).toBeInTheDocument()
    await advance(12000)
    expect(screen.queryByText('Running')).toBeNull()
    const settled = calls.length
    await advance(30000)
    expect(calls.length).toBe(settled) // nothing active: no background polling
  })

  it('keeps refreshing while a run is active, and cleans up on unmount', async () => {
    vi.useFakeTimers()
    const calls = stubFetch({ '/runs?limit=30': page([{ id: 1, status: 'researching', complete_status: 'completing' }]) })
    const { unmount } = render(<Sidebar activeRunId={null} version={0} />)
    await advance(10000)
    expect(calls.length).toBeGreaterThanOrEqual(3)
    unmount()
    const after = calls.length
    await advance(20000)
    expect(calls.length).toBe(after)
  })
})

describe('nothing active', () => {
  it('does not poll the recent-runs list at all', async () => {
    vi.useFakeTimers()
    const calls = stubFetch({ '/runs?limit=30': { items: [RUN], total: 1, limit: 30, offset: 0 } })
    render(<Sidebar activeRunId={null} version={0} />)
    await advance(20000)
    expect(calls.length).toBe(1)
  })
})
