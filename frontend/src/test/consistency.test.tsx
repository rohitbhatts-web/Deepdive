// Cross-claim consistency warnings. FIXTURES only: they show how stored fields are rendered, not that any model detects real conflicts.
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Completion, ConsistencyFinding, Report, ReportConsistency, ResearchStatus } from '@/api/types'
import { OverviewTab } from '@/components/OverviewTab'
import { ReportTab } from '@/components/ReportTab'
import { requestsByStage } from '@/lib/overview'
import type { Live } from '@/components/RunPage'
import { CLAIM_A, CLAIM_B, REPORT, RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); window.location.hash = '' })

const cite = REPORT.sections[0].claims[0].citations
const FINDING: ConsistencyFinding = {
  id: 5, kind: 'conflict', explanation: 'FIXTURE: one says new commits, the other says a single patch.', claim_ids: [1, 2],
  evidence: [{ claim_id: 1, passage_id: 'S1-P0', text_shown: true }, { claim_id: 2, passage_id: 'S2-P3', text_shown: false }],
}
const conflictsFor = (id: number) => [{ finding_id: 5, with_claim_ids: [id === 1 ? 2 : 1] }]

/** Claim 1 is a direct answer in Section 1; claim 2 is only RELATED CONTEXT in Section 2 (a context-only, cross-section conflict). */
function reportWith(consistency?: ReportConsistency, labelled = true): Report {
  return {
    ...REPORT, relevance_judged: true, consistency,
    sections: [
      { position: 1, label: 'Section 1', subquestion_id: 1, subquestion_text: 'FIXTURE first subquestion', answered: true, open_requirements: [],
        claims: [{ claim_id: 1, claim_version: 1, text: CLAIM_A.text, semantic_result_id: 2, role: 'direct_answer', citations: cite, ...(labelled && consistency?.findings.length ? { conflicts: conflictsFor(1) } : {}) }],
        context: [] },
      { position: 2, label: 'Section 2', subquestion_id: 2, subquestion_text: 'FIXTURE second subquestion', answered: false, claims: [], open_requirements: [],
        context: [{ claim_id: 2, claim_version: 1, text: CLAIM_B.text, semantic_result_id: 2, role: 'relevant_context', role_reason: 'a related fact', citations: cite,
          ...(labelled && consistency?.findings.length ? { conflicts: conflictsFor(2) } : {}) }] },
    ],
  }
}

const reviewedWithConflict: ReportConsistency = { status: 'reviewed', has_conflicts: true, findings: [FINDING], reason: 'the model listed every shown claim as reviewed' }

function showReport(rep: Report) {
  stubFetch({ '/runs/7/report': rep, '/runs/7/claims': [CLAIM_A, CLAIM_B] })
  return render(<ReportTab run={RUN} claimId={null} version={0} active={false} />)
}

describe('report tab', () => {
  it('flags the conflict in a notice and on every affected claim, including a context-only claim in another section', async () => {
    showReport(reportWith(reviewedWithConflict))
    expect(await screen.findByText('Possible conflict between claims')).toBeInTheDocument()
    expect(screen.getByText(/FIXTURE: one says new commits/)).toBeInTheDocument()
    expect(screen.getByText(/does not say which one, if either, is wrong/)).toBeInTheDocument()
    expect(screen.getByText('Possible conflict with claim 2')).toBeInTheDocument() // beside the answer in Section 1
    const ctx = screen.getByRole('group', { name: 'Related context for Section 2' })
    expect(within(ctx).getByText('Possible conflict with claim 1')).toBeInTheDocument() // beside the related-context claim in Section 2
    expect(within(ctx).getByText(CLAIM_B.text)).toBeInTheDocument() // the claim text itself is unchanged
    expect(screen.getByText(/claim 2 passage S2-P3 \(passage ID only; its text was not shown to the model\)/)).toBeInTheDocument()
  })

  it('shows a partial check together with its conflict: the limitation never hides the warning', async () => {
    showReport(reportWith({ ...reviewedWithConflict, status: 'partial', reason: 'the reply did not list claims [5] as reviewed', unchecked_claim_ids: [5] }))
    expect(await screen.findByText('Possible conflict between claims')).toBeInTheDocument()
    expect(screen.getByText(/FIXTURE: one says new commits/)).toBeInTheDocument()
    expect(screen.getByText(/The check was only partial: .*Not compared: claim 5\./)).toBeInTheDocument()
    expect(screen.getByText(/as reviewed\. Not compared: claim 5\./)).toBeInTheDocument() // the reason ends its own sentence
    expect(screen.getAllByText(/Possible conflict with claim/).length).toBe(2)
  })

  it('says plainly that claims were not checked, and attaches no label', async () => {
    showReport(reportWith({ status: 'not_checked', reason: 'no stored assessment matches the current claims and configuration', findings: [] }))
    expect(await screen.findByText('Claims not checked against each other')).toBeInTheDocument()
    expect(screen.getByText(/Consistency between the claims was not checked: no stored assessment matches/)).toBeInTheDocument()
    expect(screen.queryByText(/Possible conflict/)).toBeNull()
    expect(screen.queryByText(/flagged no conflicts/)).toBeNull() // "not checked" is never worded as "no conflicts"
  })

  it('words a clean review as "no conflicts flagged", not as "consistent"', async () => {
    showReport(reportWith({ status: 'reviewed', findings: [], limited_evidence: { '1': 'shortened', '2': 'id_only' } }))
    expect(await screen.findByText('The model flagged no conflicts among the claims.', { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/does not show that the claims are consistent/)).toBeInTheDocument()
    expect(screen.getByText(/claim 1: quotes were shortened; claim 2: only the passage ID was shown, not its text/)).toBeInTheDocument()
    expect(screen.queryByText(/Possible conflict/)).toBeNull()
  })

  it('shows a failed check as failed', async () => {
    showReport(reportWith({ status: 'failed', reason: 'no usable assessment: down', findings: [] }))
    expect(await screen.findByText('Consistency check failed')).toBeInTheDocument()
    expect(screen.getByText(/The consistency check failed: no usable assessment: down/)).toBeInTheDocument()
  })

  it('leaves a historical report exactly as it was: no consistency data, "not recorded" and "nothing to compare" render identically', async () => {
    const bare = showReport(reportWith(undefined))
    await screen.findByText(/Report 3: Claims with gaps/)
    const baseline = bare.container.innerHTML
    cleanup()
    for (const c of [{ status: 'not_recorded', findings: [] }, { status: 'not_applicable', findings: [] }] as ReportConsistency[]) {
      const r = showReport(reportWith(c))
      await screen.findByText(/Report 3: Claims with gaps/)
      expect(r.container.innerHTML).toBe(baseline)
      cleanup()
    }
    expect(baseline).not.toMatch(/Possible conflict|not checked|Cross-claim/)
  })
})

describe('overview answer cards', () => {
  const completion: Completion = { run_id: 7, complete_status: 'completed', stop_reason: null, corrective_rounds: 1, max_corrective_rounds: 2, coverage_round: 1, coverage: null, report_id: 3, report_outcome: 'claims_with_gaps' } as unknown as Completion
  const research = { run_id: 7, status: 'collection_finished', stop_reason: null, updated_at: RUN.updated_at, subquestions_by_status: {}, counts: { searches: 1, sources: 1, passages: 1, page_fetch_attempts: 1 },
    model_usage: { model_calls: 1, failed_calls: 0, calls_with_usage: 1, prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 }, limits: { model_requests: 20, search_queries: 8, page_fetches: 12 } } as ResearchStatus
  const live = { run: RUN, research, completion, events: [] } as unknown as Live
  const A = { ...CLAIM_A, citations: cite }
  const B = { ...CLAIM_B, citations: cite }

  const overview = (rep: Report) => {
    // both claims are answers here, so both appear as answer cards
    const answers: Report = { ...rep, sections: [{ ...rep.sections[0], claims: [rep.sections[0].claims[0], { ...rep.sections[1].context![0], role: 'direct_answer' }] }] }
    stubFetch({
      '/runs/7/report': answers, '/runs/7/claims': [A, B], '/runs/7/sources': [], '/runs/7/subquestions': [{ id: 1, question: 'FIXTURE first question' }],
      '/runs/7/coverage': { round: 1, summary: {}, requirements: [] }, '/runs/7/model-calls': [],
    })
    return render(<OverviewTab run={RUN} live={live} version={0} claimId={null} />)
  }

  it('labels each affected answer card and shows a compact notice; the notice does not depend on which cards are previewed', async () => {
    overview(reportWith(reviewedWithConflict))
    expect(await screen.findByText('Possible conflict between claims')).toBeInTheDocument()
    expect(screen.getByText('The full report lists the details.')).toBeInTheDocument()
    expect(await screen.findByText('Possible conflict with claim 2')).toBeInTheDocument()
    expect(screen.getByText('Possible conflict with claim 1')).toBeInTheDocument()
    expect(screen.queryByText(/passage ID only/)).toBeNull() // the compact form leaves the evidence detail to the report
  })

  it('shows nothing extra for a clean review or a historical report', async () => {
    const clean = overview(reportWith({ status: 'reviewed', findings: [] }))
    await screen.findByText(/Research answer/)
    const withClean = clean.container.innerHTML
    cleanup()
    const old = overview(reportWith(undefined))
    await screen.findByText(/Research answer/)
    expect(old.container.innerHTML).toBe(withClean)
    expect(withClean).not.toMatch(/Possible conflict|not checked|Cross-claim/)
  })

  it('still warns when the check was not run', async () => {
    overview(reportWith({ status: 'not_checked', reason: 'not run: max_model_requests reached (20)', findings: [] }))
    expect(await screen.findByText('Claims not checked against each other')).toBeInTheDocument()
    expect(screen.getByText(/not run: max_model_requests reached \(20\)/)).toBeInTheDocument()
  })
})

describe('request counts', () => {
  it('names the consistency stage like its neighbours and keeps it in pipeline order, before the report is written', () => {
    const rows = requestsByStage([{ agent: 'synthesizer', ok: true }, { agent: 'consistency', ok: true }, { agent: 'coverage', ok: true }])
    expect(rows.map((r) => [r.agent, r.label])).toEqual([['coverage', 'Assess coverage'], ['consistency', 'Compare claims'], ['synthesizer', 'Write report']])
  })
})
