// An empty ("insufficient evidence") report is labelled as such in the header and explained from the run's recorded events, without implying that
// the search was exhaustive or that the answer does not exist. FIXTURES only.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { RunEvent } from '@/api/types'
import { ReportTab } from '@/components/ReportTab'
import { collectionContext, emptyReportContext } from '@/lib/collection'
import { runStatusLabel } from '@/lib/claimState'
import { REPORT, RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); window.location.hash = '' })

const ev = (id: number, event_type: string, payload: Record<string, unknown> = {}): RunEvent => ({ id, event_type, stage: 'x', created_at: '2026-01-01T00:00:00Z', payload })
const EVENTS: RunEvent[] = [
  ev(1, 'search_completed', { hits: 5 }), ev(2, 'search_completed', { hits: 5 }), ev(3, 'search_failed'),
  ev(4, 'result_skipped', { reason: 'pdf_not_supported' }), ev(5, 'result_skipped', { reason: 'pdf_not_supported' }), ev(6, 'result_skipped', { reason: 'other' }),
  ev(7, 'urls_selected', { selected: [] }), ev(8, 'urls_selected', { selected: [] }), ev(9, 'urls_selected', { selected: [{ url: 'u' }] }),
]

describe('collection counts', () => {
  it('counts subquestions, not research steps: a correction round that researches the same subquestion again is not a second subquestion', () => {
    // the recorded Q05 run: 3 subquestions, then 2 correction-round searches for the first; nothing was ever selected
    const events = [5, 6, 7, 5, 5].map((id, i) => ev(i + 1, 'urls_selected', { subquestion_id: id, selected: [] }))
    expect(collectionContext(events)).toMatchObject({ subquestionsWithSelection: 3, noUrlSelected: 3 })
    expect(emptyReportContext(collectionContext(events))).toContain('For 3 of 3 subquestion(s)')
    expect(emptyReportContext(collectionContext(events))).not.toContain('5 of 5')
  })

  it('a subquestion counts as selected if ANY of its steps selected a URL', () => {
    const events = [ev(1, 'urls_selected', { subquestion_id: 5, selected: [] }), ev(2, 'urls_selected', { subquestion_id: 5, selected: [{ url: 'u' }] }),
      ev(3, 'urls_selected', { subquestion_id: 6, selected: [] })]
    expect(collectionContext(events)).toMatchObject({ subquestionsWithSelection: 2, noUrlSelected: 1 })
  })
})

describe('empty report wording', () => {
  it('names an insufficient-evidence run as such in the header, not "Completed with gaps"', () => {
    const base = { status: 'collection_finished', verify_status: 'completed', complete_status: 'completed' }
    expect(runStatusLabel({ ...base, report_outcome: 'insufficient_evidence' }).label).toBe('Insufficient evidence')
    expect(runStatusLabel({ ...base, report_outcome: 'claims_with_gaps' }).label).toBe('Completed with gaps')
    expect(runStatusLabel({ ...base, report_outcome: 'claims_all_requirements_addressed' }).label).toBe('Completed')
  })

  it('counts only what the events record', () => {
    expect(collectionContext(EVENTS)).toEqual({ searches: 3, pages: 0, subquestionsWithSelection: 3, noUrlSelected: 2, pdfSkipped: 2, fetchFailed: 0 })
    expect(collectionContext([])).toEqual({ searches: 0, pages: 0, subquestionsWithSelection: 0, noUrlSelected: 0, pdfSkipped: 0, fetchFailed: 0 })
  })

  it('explains that nothing was fetched and refuses to claim exhaustiveness or that the figure does not exist', () => {
    const text = emptyReportContext(collectionContext(EVENTS))
    expect(text).toContain('3 search queries and stored 0 pages')
    expect(text).toContain('For 2 of 3 subquestion(s) no URL was selected to fetch at any research step')
    expect(text).toContain('2 search result(s) were skipped because PDF documents are not supported')
    expect(text).toContain('does not show that the search was exhaustive, or that the information does not exist')
  })

  it('shows the context beside an insufficient-evidence report and leaves the stored report text alone', async () => {
    stubFetch({
      '/runs/7/report': { ...REPORT, outcome: 'insufficient_evidence', sections: [], requirements: [], unresolved_requirements: [], markdown: 'STORED SNAPSHOT TEXT' },
      '/runs/7/claims': [],
    })
    render(<ReportTab run={{ ...RUN, report_outcome: 'insufficient_evidence' }} claimId={null} version={0} active={false} events={EVENTS} />)
    expect(await screen.findByText(/Report 3: Insufficient evidence/)).toBeInTheDocument()
    expect(screen.getByText('What the run collected')).toBeInTheDocument()
    expect(screen.getByText(/does not show that the search was exhaustive/)).toBeInTheDocument()
    expect(screen.getByText('No verified claim could be reported.', { exact: false })).toBeInTheDocument() // the original outcome sentence is still there
  })

  it('adds no context to a report that has claims', async () => {
    stubFetch({ '/runs/7/report': REPORT, '/runs/7/claims': [] })
    render(<ReportTab run={RUN} claimId={null} version={0} active={false} events={EVENTS} />)
    expect(await screen.findByText(/Report 3: Claims with gaps/)).toBeInTheDocument()
    expect(screen.queryByText('What the run collected')).toBeNull()
  })
})
