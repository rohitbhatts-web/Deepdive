// The Overview derives everything from stored rows. FIXTURES only; nothing here talks to a backend.
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import type { Completion, ResearchStatus, RunEvent, RunSummary } from '@/api/types'
import { OverviewTab } from '@/components/OverviewTab'
import { LifecycleDetail, LifecycleStrip } from '@/components/Lifecycle'
import type { Live } from '@/components/RunPage'
import { coverageBySubquestion, evidenceMap, lifecycle, outcomeSummary, requestsByStage } from '@/lib/overview'
import { parseHash, useRoute } from '@/lib/route'
import { CLAIM_A, CLAIM_B, REPORT, RUN, claim, stubFetch } from './fixtures'

afterEach(() => { cleanup(); window.location.hash = '' })

/** Mounts the tab the way RunPage does: the open claim comes from the page's own hash route, so these tests exercise the real deep-link mechanism. */
function Harness(props: { run: RunSummary; live: Live; version: number }) {
  const r = useRoute()
  return <OverviewTab {...props} claimId={r.page === 'run' ? r.claimId : null} />
}

const ev = (id: number, event_type: string, at = '2026-01-01T00:0' + id + ':00Z'): RunEvent => ({ id, event_type, stage: 'x', created_at: at, payload: {} })
const completion: Completion = { run_id: 7, complete_status: 'completed', stop_reason: 'corrective rounds exhausted (1)', corrective_rounds: 1, max_corrective_rounds: 2, coverage_round: 1, coverage: null, report_id: 3, report_outcome: 'claims_with_gaps', budget_used_of_limit: { model_requests: [9, 20] } }
const research: ResearchStatus = {
  run_id: 7, status: 'collection_finished', stop_reason: null, updated_at: RUN.updated_at, subquestions_by_status: {}, counts: { searches: 3, sources: 3, passages: 40, page_fetch_attempts: 3 },
  model_usage: { model_calls: 9, failed_calls: 0, calls_with_usage: 9, prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 }, limits: { model_requests: 20, search_queries: 8, page_fetches: 12 },
}

describe('coverage and request counts', () => {
  it('counts requirement statuses per question and never produces a score', () => {
    const rows = coverageBySubquestion(
      [{ subquestion_id: 1, status: 'addressed' }, { subquestion_id: 1, status: 'partially_addressed' }, { subquestion_id: 1, status: 'unresolved' }, { subquestion_id: 2, status: 'not_assessed' }],
      [{ id: 1, question: 'Q one' }, { id: 2, question: 'Q two' }, { id: 3, question: 'no requirements' }])
    expect(rows).toEqual([{ subquestionId: 1, label: 'Q one', addressed: 1, partial: 1, open: 1, total: 3 }, { subquestionId: 2, label: 'Q two', addressed: 0, partial: 0, open: 1, total: 1 }])
  })

  it('counts recorded model calls by stage, in pipeline order, with failures, and no other figure', () => {
    const rows = requestsByStage([{ agent: 'verifier', ok: true }, { agent: 'planner', ok: true }, { agent: 'verifier', ok: false }, { agent: 'mystery', ok: true }])
    expect(rows.map((r) => [r.agent, r.calls, r.failed])).toEqual([['planner', 1, 0], ['verifier', 2, 1], ['mystery', 1, 0]])
    expect(Object.keys(rows[0]).sort()).toEqual(['agent', 'calls', 'failed', 'label'])
  })
})

describe('lifecycle', () => {
  const all = ['plan_created', 'research_finished', 'claims_drafted', 'claims_verified', 'coverage_assessed', 'report_created'].map((t, i) => ev(i + 1, t))

  it('shows recorded stages with their stored time and nothing running once the run has a report', () => {
    const stages = lifecycle(RUN, all, completion)
    expect(stages.map((s) => s.state)).toEqual(Array(6).fill('recorded'))
    expect(stages[0].at).toBe('2026-01-01T00:01:00Z')
  })

  it('never shows a stage as running beside a completed report, even when events are missing', () => {
    const stages = lifecycle(RUN, [ev(1, 'plan_created'), ev(2, 'research_finished')], completion)
    expect(stages.map((s) => s.state)).toEqual(['recorded', 'recorded', 'not_recorded', 'not_recorded', 'not_recorded', 'not_recorded'])
    expect(stages.some((s) => s.state === 'running')).toBe(false)
    const active = lifecycle({ ...RUN, complete_status: 'completing' }, [ev(1, 'plan_created'), ev(2, 'research_finished')], null)
    expect(active.some((s) => s.state === 'running')).toBe(false) // a report exists: a later job's progress belongs on the Run history tab
  })

  it('shows the first missing stage as running only for an active run without a report, and failure or a stop on the right stage', () => {
    const live = { ...RUN, report_outcome: null, status: 'researching', complete_status: 'not_started', verify_status: 'not_started' }
    expect(lifecycle(live, [ev(1, 'plan_created')], null).map((s) => s.state)).toEqual(['recorded', 'running', 'waiting', 'waiting', 'waiting', 'waiting'])
    const failed = { ...RUN, report_outcome: null, verify_status: 'failed', complete_status: 'failed' }
    const states = lifecycle(failed, [ev(1, 'plan_created'), ev(2, 'research_finished')], { stop_reason: 'drafter failed: timeout' }).map((s) => s.state)
    expect(states).toEqual(['recorded', 'recorded', 'failed', 'not_recorded', 'not_recorded', 'not_recorded'])
    const stopped = { ...RUN, report_outcome: null, complete_status: 'stopped' }
    expect(lifecycle(stopped, [ev(1, 'plan_created'), ev(2, 'research_finished'), ev(3, 'claims_drafted')], null).map((s) => s.state)).toEqual(['recorded', 'recorded', 'recorded', 'stopped', 'not_recorded', 'not_recorded'])
  })
})

describe('outcome wording and the evidence map', () => {
  it('states the outcome from stored data and says an empty report does not prove absence', () => {
    expect(outcomeSummary(RUN, REPORT, completion, REPORT.requirements).headline).toBe('2 verified claims answer part of the question.')
    expect(outcomeSummary(RUN, REPORT, completion, REPORT.requirements).detail).toBe('1 of 2 requirements is still not fully answered.')
    const empty = outcomeSummary({ ...RUN, report_outcome: 'insufficient_evidence' }, { ...REPORT, outcome: 'insufficient_evidence', sections: [] }, completion, [])
    expect(empty.detail).toMatch(/does not show that the answer does not exist/)
    expect(outcomeSummary({ ...RUN, complete_status: 'completing', report_outcome: null }, null, null, []).headline).toBe('Research is in progress.')
  })

  const cite = (id: string, source: number, role = 'claim_evidence') => ({ passage_id: id, role, quotes: ['q'], source_id: source, url: `https://example.test/${source}`, title: `Page ${source}` })

  it('builds a bounded subset of the stored graph with honest counts, report claims first', () => {
    const claims = Array.from({ length: 9 }, (_, i) => claim({ id: i + 1, citations: [cite(`S${i + 1}-P0`, i + 1), ...(i === 0 ? [cite('S1-P1', 1, 'neighbour_context')] : [])], eligible_for_report: i < 5 }))
    const sources = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, title: `Page ${i + 1}`, final_url: `https://example.test/${i + 1}`, passage_count: 10 }))
    const map = evidenceMap(claims, sources, new Set([9, 8]))
    expect(map.claims.map((c) => c.id).slice(0, 2)).toEqual([8, 9]) // claims in the report are chosen first
    expect(map.claims).toHaveLength(4)
    expect(map.counts).toMatchObject({ claimsShown: 4, claimsTotal: 9, sourcesTotal: 12 })
    expect(map.counts.sourcesShown).toBeLessThanOrEqual(4)
    expect(map.list).toHaveLength(9) // the list fallback keeps every connected claim, not just the drawn subset
    expect(map.list.find((c) => c.id === 1)!.cites.map((c) => c.passageId)).toEqual(['S1-P0', 'S1-P1'])
    for (const e of map.edges) {  // every edge ends at a node that is shown; none is invented
      const ids = new Set([...map.sources.map((s) => `src-${s.id}`), ...map.passages.map((p) => `psg-${p.id}`), ...map.claims.map((c) => `clm-${c.id}`)])
      expect(ids.has(e.from) && ids.has(e.to)).toBe(true)
    }
    const context = evidenceMap([claims[0]], sources, new Set()).edges.find((e) => e.kind === 'context')
    expect(context).toMatchObject({ from: 'psg-S1-P1', to: 'clm-1' })
  })
})

describe('routing', () => {
  it('opens a run on the Overview unless a tab is named, and keeps older deep links', () => {
    expect(parseHash('#/runs/3')).toMatchObject({ page: 'run', runId: 3, tab: 'overview' })
    expect(parseHash('#/runs/3/report?claim=2')).toMatchObject({ tab: 'report', claimId: 2 })
    expect(parseHash('#/runs/3/nonsense')).toMatchObject({ tab: 'overview' })
  })
})

describe('Overview tab', () => {
  const live: Live = { run: RUN, research, completion, events: ['plan_created', 'research_finished', 'claims_drafted', 'claims_verified', 'coverage_assessed', 'report_created'].map((t, i) => ev(i + 1, t)) }
  const A = { ...CLAIM_A, citations: [{ passage_id: 'S1-P0', role: 'claim_evidence', quotes: ['The cache holds 512 entries.'], source_id: 1, url: 'https://example.test/guide', title: 'Guide' }] }
  const B = { ...CLAIM_B, citations: [{ passage_id: 'S2-P3', role: 'claim_evidence', quotes: ['the engine is embedded'], source_id: 2, url: 'https://example.test/two', title: 'Second page' }] }
  const requirements = [
    { requirement_key: 'Q1.R1', subquestion_id: 1, requirement_text: 'Cache size', status: 'addressed', gap: '', missing_kind: 'none', supporting_claim_ids: [1], obligations: [] },
    { requirement_key: 'Q1.R2', subquestion_id: 1, requirement_text: 'Write concurrency', status: 'unresolved', gap: 'no evidence on writers', missing_kind: 'detail', supporting_claim_ids: [], obligations: [] },
    { requirement_key: 'Q2.R1', subquestion_id: 2, requirement_text: 'Engine type', status: 'partially_addressed', gap: 'only one source', missing_kind: 'detail', supporting_claim_ids: [2], obligations: [] },
  ]
  const routes = () => ({
    '/runs/7/report': REPORT, '/runs/7/claims': [A, B], '/runs/7/claims/1': A, '/runs/7/claims/2': B,
    '/runs/7/sources': [{ id: 1, final_url: 'https://example.test/guide', title: 'Guide', retrieved_at: RUN.created_at, content_type: 'text/html', passage_count: 12 },
      { id: 2, final_url: 'https://example.test/two', title: 'Second page', retrieved_at: RUN.created_at, content_type: 'text/html', passage_count: 5 },
      { id: 3, final_url: 'https://example.test/three', title: 'Never cited', retrieved_at: RUN.created_at, content_type: 'text/html', passage_count: 2 }],
    '/runs/7/coverage': { round: 1, summary: {}, requirements },
    '/runs/7/subquestions': [{ id: 1, question: 'FIXTURE first question' }, { id: 2, question: 'FIXTURE second question' }],
    '/runs/7/model-calls': [{ id: 1, agent: 'planner', model: 'm', ok: true, error: null, prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 }, { id: 2, agent: 'verifier', model: 'm', ok: true, error: null, prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 }, { id: 3, agent: 'verifier', model: 'm', ok: false, error: 'x', prompt_tokens: null, completion_tokens: null, total_tokens: null }],
  })

  it('answers first: outcome, exact answer text, what is unanswered, and honest counts', async () => {
    stubFetch(routes())
    render(<Harness run={RUN} live={live} version={0} />)
    expect(await screen.findByText('2 verified claims answer part of the question.')).toBeInTheDocument()
    const answer = screen.getByRole('heading', { name: 'Research answer' }).closest('[class*="rounded-2xl"]') as HTMLElement
    expect(within(answer).getByText(/The cache holds\s+512 entries/).textContent).toBe(CLAIM_A.text) // exactly as stored, markup shown as text
    const open = screen.getByRole('region', { name: /Still unanswered/ })
    expect(within(open).getByText('Write concurrency')).toBeInTheDocument()
    expect(within(open).getByText(/no evidence on writers/)).toBeInTheDocument()
    expect(within(open).getByRole('link', { name: /View all gaps/ })).toHaveAttribute('href', '#/runs/7/coverage')
    expect(within(answer).getByRole('link', { name: /View full report/ })).toHaveAttribute('href', '#/runs/7/report')
    expect(within(answer).getAllByRole('button', { name: /Inspect evidence for claim/ })).toHaveLength(2)
    expect(screen.getByRole('list', { name: 'Run at a glance' })).toHaveTextContent('1 of 3') // 1 of 3 requirements addressed
    expect(screen.getByText('9 / 20')).toBeInTheDocument() // the latest job's recorded request count and its stored limit
  })

  it('draws bars from requirement statuses labelled Addressed / Partial / Open, and never calls them accuracy', async () => {
    stubFetch(routes())
    render(<Harness run={RUN} live={live} version={0} />)
    expect(await screen.findByRole('img', { name: /FIXTURE first question: 1 addressed, 0 partial, 1 open, of 2 requirements/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /FIXTURE second question: 0 addressed, 1 partial, 0 open, of 1 requirements/ })).toBeInTheDocument()
    const legend = within(screen.getByRole('heading', { name: 'Coverage by question' }).closest('[class*="rounded-2xl"]') as HTMLElement).getByLabelText('Legend')
    expect(legend).toHaveTextContent('Addressed (1)')
    expect(legend).toHaveTextContent('Partial (1)')
    expect(legend).toHaveTextContent('Open (1)')
    expect(screen.queryByText(/accuracy|confidence|score/i)).toBeNull()
  })

  it('shows recorded request counts by stage without limits, timings or costs', async () => {
    stubFetch(routes())
    render(<Harness run={RUN} live={live} version={0} />)
    expect(await screen.findByText(/3 recorded requests in total/)).toBeInTheDocument()
    const section = screen.getByRole('heading', { name: 'Model requests by stage' }).closest('[class*="rounded-2xl"]') as HTMLElement
    expect(within(section).getByText('Verify claims').parentElement).toHaveTextContent('2 · 1 failed')
    expect(section.textContent).not.toMatch(/\$|ms\b|seconds|confidence|limit/i)
  })

  it('connects the stored sources, passages and claims, and opens the existing inspector from any node; the list is a complete fallback', async () => {
    stubFetch(routes())
    const user = userEvent.setup()
    render(<Harness run={RUN} live={live} version={0} />)
    expect(await screen.findByText('2 of 3 shown')).toBeInTheDocument() // the subset is labelled in each column: sources, cited passages, claims
    expect(screen.getByText('2 of 2 cited shown')).toBeInTheDocument()
    expect(screen.getByText('2 of 2 shown')).toBeInTheDocument()
    expect(screen.queryByText('Never cited', { selector: 'button *' })).toBeNull() // an uncited source is counted, not drawn
    expect(screen.queryByRole('heading', { name: /inspector/ })).toBeNull() // nothing selected yet
    const passageNode = () => within(screen.getByRole('list', { name: 'Passages' })).getByRole('button', { name: /S1-P0/ })
    await user.click(passageNode())
    expect(await screen.findByRole('heading', { name: 'Claim 1 inspector' })).toBeInTheDocument()
    expect(passageNode()).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: /Close inspector/ }))
    await user.click(within(screen.getByRole('list', { name: 'Claims' })).getByRole('button', { name: /Claim 2/ }))
    expect(await screen.findByRole('heading', { name: 'Claim 2 inspector' })).toBeInTheDocument()
    const list = screen.getByRole('list', { name: 'Evidence connections' })
    expect(within(list).getAllByRole('button').length).toBeGreaterThanOrEqual(4) // claims and their passages, reachable without the drawing
    await user.click(within(list).getByRole('button', { name: 'S2-P3' }))
    expect(await screen.findByRole('heading', { name: 'Claim 2 inspector' })).toBeInTheDocument()
    await user.keyboard('{Escape}') // the inspector closes from the keyboard
    await waitFor(() => expect(screen.queryByRole('heading', { name: /inspector/ })).toBeNull())
  })

  describe('the open claim lives in the page link', () => {
    const hash = () => window.location.hash
    const inspectorFor = (n: number) => screen.findByRole('heading', { name: `Claim ${n} inspector` })

    it('opens from an answer button, a map node and the list fallback by writing the claim into the link', async () => {
      window.location.hash = '#/runs/7/overview'
      stubFetch(routes())
      const user = userEvent.setup()
      render(<Harness run={RUN} live={live} version={0} />)
      await user.click(await screen.findByRole('button', { name: 'Inspect evidence for claim 1' }))
      expect(await inspectorFor(1)).toBeInTheDocument()
      expect(hash()).toBe('#/runs/7/overview?claim=1')
      await user.click(within(screen.getByRole('list', { name: 'Claims' })).getByRole('button', { name: /Claim 2/ }))
      expect(await inspectorFor(2)).toBeInTheDocument()
      expect(hash()).toBe('#/runs/7/overview?claim=2')
      const list = screen.getByRole('list', { name: 'Evidence connections' })
      await user.click(within(list).getByRole('button', { name: /^Claim 1:/ }))
      expect(await inspectorFor(1)).toBeInTheDocument()
      expect(hash()).toBe('#/runs/7/overview?claim=1')
      await user.click(within(list).getByRole('button', { name: 'S2-P3' })) // a passage opens its claim, with the passage highlighted
      expect(await inspectorFor(2)).toBeInTheDocument()
      expect(hash()).toBe('#/runs/7/overview?claim=2')
      expect(within(screen.getByRole('list', { name: 'Passages' })).getByRole('button', { name: /S2-P3/ })).toHaveAttribute('aria-pressed', 'true')
    })

    it('loads an inspector straight from a direct link, and keeps it after a refresh', async () => {
      window.location.hash = '#/runs/7/overview?claim=2'
      stubFetch(routes())
      render(<Harness run={RUN} live={live} version={0} />)
      expect(await inspectorFor(2)).toBeInTheDocument()
      expect(within(screen.getByRole('list', { name: 'Claims' })).getByRole('button', { name: /Claim 2/ })).toHaveAttribute('aria-pressed', 'true')
      cleanup() // a refresh: the page is rebuilt from the same link alone
      stubFetch(routes())
      render(<Harness run={RUN} live={live} version={0} />)
      expect(await inspectorFor(2)).toBeInTheDocument()
      expect(hash()).toBe('#/runs/7/overview?claim=2')
    })

    it('closes from the button and from Escape, and closing clears the claim from the link', async () => {
      window.location.hash = '#/runs/7/overview?claim=1'
      stubFetch(routes())
      const user = userEvent.setup()
      render(<Harness run={RUN} live={live} version={0} />)
      await user.click(await screen.findByRole('button', { name: /Close inspector/ }))
      await waitFor(() => expect(screen.queryByRole('heading', { name: /inspector/ })).toBeNull())
      expect(hash()).toBe('#/runs/7/overview')
      await user.click(screen.getByRole('button', { name: 'Inspect evidence for claim 2' }))
      expect(await inspectorFor(2)).toBeInTheDocument()
      await user.keyboard('{Escape}')
      await waitFor(() => expect(screen.queryByRole('heading', { name: /inspector/ })).toBeNull())
      expect(hash()).toBe('#/runs/7/overview')
      cleanup() // and a refresh after closing stays closed
      stubFetch(routes())
      render(<Harness run={RUN} live={live} version={0} />)
      await screen.findByRole('heading', { name: 'Research answer' })
      expect(screen.queryByRole('heading', { name: /inspector/ })).toBeNull()
    })

    it('shows no evidence for a claim id that is not in this run: the request is scoped to the run and its refusal is shown', async () => {
      window.location.hash = '#/runs/7/overview?claim=999' // an invalid id, or one that belongs to another run, is refused by the run-scoped endpoint (404)
      const calls = stubFetch({ ...routes(), '/runs/7/claims/999': { __status: 404, detail: 'claim 999 not found in run 7' } })
      render(<Harness run={RUN} live={live} version={0} />)
      expect(await screen.findByRole('heading', { name: 'Claim 999 inspector' })).toBeInTheDocument()
      expect(await screen.findByText('claim 999 not found in run 7')).toBeInTheDocument()
      const inspector = screen.getByRole('complementary', { name: 'Claim and evidence inspector' })
      expect(within(inspector).queryByText(CLAIM_A.text)).toBeNull() // no other claim's text or evidence is shown in its place
      expect(within(inspector).queryByText('Cited evidence')).toBeNull()
      expect(calls.filter((c) => /\/claims\/\d+$/.test(c.path)).map((c) => c.path)).toEqual(['/runs/7/claims/999']) // only this run's path was asked for
      expect(within(screen.getByRole('list', { name: 'Claims' })).queryByRole('button', { pressed: true })).toBeNull() // and no map node is marked as selected
    })

    it('follows Back and Forward, and a source panel is not part of the link', async () => {
      window.location.hash = '#/runs/7/overview'
      stubFetch(routes())
      const user = userEvent.setup()
      render(<Harness run={RUN} live={live} version={0} />)
      await user.click(await screen.findByRole('button', { name: 'Inspect evidence for claim 1' }))
      expect(await inspectorFor(1)).toBeInTheDocument()
      window.location.hash = '#/runs/7/overview?claim=2' // as Back or Forward would
      expect(await inspectorFor(2)).toBeInTheDocument()
      await user.click(within(screen.getByRole('list', { name: 'Sources' })).getByRole('button', { name: /Guide/ }))
      expect(await screen.findByRole('heading', { name: 'Guide' })).toBeInTheDocument()
      expect(hash()).toBe('#/runs/7/overview') // a source has no claim, so the claim leaves the link
    })
  })

  it('previews at most three answers and three gaps, with working links to the rest', async () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ claim_id: i + 1, claim_version: 1, text: `FIXTURE answer ${i + 1}`, semantic_result_id: 2, citations: REPORT.sections[0].claims[0].citations }))
    const gaps = Array.from({ length: 5 }, (_, i) => ({ requirement_key: `Q1.R${i + 1}`, requirement_text: `FIXTURE gap ${i + 1}`, status: 'unresolved', gap: 'none stored' }))
    stubFetch({ ...routes(), '/runs/7/report': { ...REPORT, sections: [{ ...REPORT.sections[0], claims: many }], requirements: gaps, unresolved_requirements: gaps } })
    render(<Harness run={RUN} live={live} version={0} />)
    const answer = (await screen.findByRole('heading', { name: 'Research answer' })).closest('[class*="rounded-2xl"]') as HTMLElement
    expect(within(answer).getAllByRole('button', { name: /Inspect evidence for claim/ })).toHaveLength(3)
    expect(within(answer).getByRole('link', { name: 'View full report (2 more claims)' })).toBeInTheDocument()
    const open = screen.getByRole('region', { name: /Still unanswered \(5\)/ })
    expect(within(open).getAllByRole('listitem')).toHaveLength(3)
    expect(within(open).getByRole('link', { name: 'View all gaps (2 more)' })).toBeInTheDocument()
  })

  it('labels a historical answer and its coverage when listed claims need re-judging', async () => {
    const old = (c: typeof A) => ({ ...c, eligible_for_report: false })
    stubFetch({ ...routes(), '/runs/7/claims': [old(A), old(B)] })
    render(<Harness run={RUN} live={live} version={0} />)
    expect(await screen.findByText('Historical answer: 2 of 2 claims need re-judging.')).toBeInTheDocument()
    const cov = screen.getByRole('heading', { name: /Coverage by question/ })
    expect(cov).toHaveTextContent('Historical')
  })

  it('says the report is loading rather than claiming there is none while it is being read', async () => {
    stubFetch(routes())
    render(<Harness run={RUN} live={live} version={0} />)
    expect(screen.getByText('Loading the stored report…')).toBeInTheDocument()
    expect(screen.queryByText('This run has no report.')).toBeNull()
    expect(await screen.findByText('2 verified claims answer part of the question.')).toBeInTheDocument()
  })

  it('keeps the stop reason behind a technical-details disclosure', async () => {
    stubFetch(routes())
    render(<Harness run={RUN} live={live} version={0} />)
    const summary = await screen.findByText('Technical stop details')
    expect(summary.closest('details')).not.toHaveAttribute('open')
    expect(summary.closest('details')).toHaveTextContent('corrective rounds exhausted (1)')
  })

  it('says so when nothing is connected, and shows a failed run without any answer', async () => {
    stubFetch({ ...routes(), '/runs/7/claims': [], '/runs/7/report': { __status: 404, detail: 'none' } })
    render(<Harness run={{ ...RUN, report_outcome: null, verify_status: 'failed', complete_status: 'failed' }} live={{ ...live, events: live.events.slice(0, 2), run: { ...RUN, report_outcome: null, verify_status: 'failed', complete_status: 'failed' }, completion: { ...completion, stop_reason: 'drafter failed: timeout' } }} version={0} />)
    expect(await screen.findByText('Drafting failed after collection finished.')).toBeInTheDocument()
    expect(screen.getByText(/nothing to connect/)).toBeInTheDocument()
    expect(screen.getByText('No answer was produced')).toBeInTheDocument()
  })
})

describe('lifecycle strip', () => {
  it('shows stage names and states without timestamps; the detail with times lives in Run history', () => {
    const stages = ['plan_created', 'research_finished', 'claims_drafted', 'claims_verified', 'coverage_assessed', 'report_created'].map((t, i) => ev(i + 1, t))
    render(<LifecycleStrip run={RUN} events={stages} completion={completion} />)
    const strip = screen.getByRole('list', { name: 'Research lifecycle' })
    expect(strip).toHaveTextContent('Plan: Recorded')
    expect(strip.textContent).not.toMatch(/\d{4}|\d:\d\d/)
    cleanup()
    render(<LifecycleStrip run={{ ...RUN, report_outcome: null, verify_status: 'failed', complete_status: 'failed' }} events={stages.slice(0, 2)} completion={{ ...completion, stop_reason: 'drafter failed: timeout' }} />)
    expect(screen.getByRole('list', { name: 'Research lifecycle' })).toHaveTextContent('Failed') // a failed stage is named, never shown as recorded
    cleanup()
    render(<LifecycleDetail run={RUN} events={stages} completion={completion} />)
    expect(screen.getByRole('list', { name: 'Lifecycle stages with times' })).toHaveTextContent(/Plan\s*Recorded · /)
  })
})
