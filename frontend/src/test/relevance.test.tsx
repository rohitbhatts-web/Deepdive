// A judged report shows direct answers as answers, relevant context apart (never as an answer), and names what stays unanswered. FIXTURES only.
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Report } from '@/api/types'
import { ReportTab } from '@/components/ReportTab'
import { CLAIM_A, CLAIM_B, REPORT, RUN, stubFetch } from './fixtures'

afterEach(() => { cleanup(); window.location.hash = '' })

const cite = REPORT.sections[0].claims[0].citations
const JUDGED: Report = {
  ...REPORT, relevance_judged: true,
  sections: [
    { position: 1, label: 'Section 1', subquestion_id: 1, subquestion_text: 'FIXTURE date subquestion', answered: true,
      claims: [{ claim_id: 1, claim_version: 1, text: CLAIM_A.text, semantic_result_id: 2, role: 'direct_answer', requirement_texts: ['Cache size'], citations: cite }],
      context: [],
      open_requirements: [{ requirement_key: 'Q1.R2', requirement_text: 'Original publication date', status: 'unresolved', gap: 'only a later revision is dated' }] },
    { position: 2, label: 'Section 2', subquestion_id: 2, subquestion_text: 'FIXTURE milestone subquestion', answered: false, claims: [],
      context: [{ claim_id: 2, claim_version: 1, text: CLAIM_B.text, semantic_result_id: 2, role: 'relevant_context', role_reason: 'a different event', citations: cite }],
      open_requirements: [{ requirement_key: 'Q2.R1', requirement_text: 'Publication date', status: 'unresolved', gap: 'no claim states it' }] },
  ],
}

describe('judged report', () => {
  it('says an unanswered section is unanswered, keeps context apart, and names the open requirements', async () => {
    stubFetch({ '/runs/7/report': JUDGED, '/runs/7/claims': [CLAIM_A, CLAIM_B] })
    render(<ReportTab run={RUN} claimId={null} version={0} active={false} />)
    expect(await screen.findByText(/Report 3: Claims with gaps/)).toBeInTheDocument()
    expect(screen.getByText('No direct answer was found for this subquestion')).toBeInTheDocument()
    const ctx = screen.getByRole('group', { name: 'Related context for Section 2' })
    expect(within(ctx).getByText(CLAIM_B.text)).toBeInTheDocument()
    expect(within(ctx).getByText('Context, not an answer')).toBeInTheDocument()
    expect(within(ctx).getByText('a different event')).toBeInTheDocument()
    expect(screen.getAllByText('Not directly answered in this section').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Original publication date \(unresolved: only a later revision is dated\)/)).toBeInTheDocument()
    // the context claim is not listed among the answers of its section
    expect(screen.queryByRole('button', { name: new RegExp(CLAIM_B.text.slice(0, 12)) })).toBeNull()
  })

  it('shows nothing extra for a report made without relevance judgments', async () => {
    stubFetch({ '/runs/7/report': REPORT, '/runs/7/claims': [CLAIM_A, CLAIM_B] })
    render(<ReportTab run={RUN} claimId={null} version={0} active={false} />)
    expect(await screen.findByText(/Report 3: Claims with gaps/)).toBeInTheDocument()
    expect(screen.queryByText('No direct answer was found for this subquestion')).toBeNull()
    expect(screen.queryByText(/Related context/)).toBeNull()
  })
})

describe('a direct answer shown in another subquestion\'s section', () => {
  const REFERENCED: Report = {
    ...JUDGED,
    sections: [
      JUDGED.sections[0],
      { position: 2, label: 'Section 2', subquestion_id: 2, subquestion_text: 'FIXTURE milestone subquestion', answered: true, context: [], open_requirements: [],
        claims: [{ claim_id: 1, claim_version: 1, text: CLAIM_A.text, semantic_result_id: 2, role: 'direct_answer', requirement_texts: ['Publication date'], citations: cite,
          cross_reference: { owner_label: 'Section 1', owner_subquestion_id: 1 } }] },
    ],
  }

  it('shows the same verified claim with its owner named, and no element id appears twice', async () => {
    stubFetch({ '/runs/7/report': REFERENCED, '/runs/7/claims': [CLAIM_A, CLAIM_B] })
    const { container } = render(<ReportTab run={RUN} claimId={null} version={0} active={false} />)
    expect(await screen.findByText(/Report 3: Claims with gaps/)).toBeInTheDocument()
    expect(screen.queryByText('No direct answer was found for this subquestion')).toBeNull()
    expect(screen.getByText('Same verified claim as in Section 1, where it was drafted')).toBeInTheDocument()
    const buttons = screen.getAllByRole('button', { name: /The cache holds/ })
    expect(buttons).toHaveLength(2) // the claim appears in both sections
    expect(buttons.map((b) => b.textContent)).toEqual([CLAIM_A.text, CLAIM_A.text]) // with its exact text
    const ids = Array.from(container.querySelectorAll('[id]')).map((e) => e.id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(screen.getByRole('list', { name: 'Citations for claim 1 in Section 2' })).toBeInTheDocument()
  })
})
