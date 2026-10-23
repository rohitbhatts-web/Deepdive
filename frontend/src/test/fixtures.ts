// FIXTURES: hand-written API responses used only by tests. They are not run data and carry no accuracy claim.
import type { Claim, Report, RunSummary } from '@/api/types'

export const RUN: RunSummary = {
  id: 7, question: 'FIXTURE question?', status: 'collection_finished', verify_status: 'completed', complete_status: 'completed',
  stop_reason: null, report_outcome: 'claims_with_gaps', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:05:00Z',
}

export const PASSAGE_A = 'Alpha passage with <b>markup</b>.  Two spaces. The cache holds 512 entries.'
export const PASSAGE_B = 'Beta passage says the engine is embedded. Second sentence.'

export function claim(over: Partial<Claim> & { id: number }): Claim {
  return {
    subquestion_id: 1, parent_claim_id: null, version: 1, text: `fixture claim ${over.id}`, origin: 'drafted', provenance: {},
    created_at: '2026-01-01T00:01:00Z', status: 'supported', integrity_verdict: 'passed', semantic_verdict: 'supported',
    superseded_by: null, excluded: null, needs_rejudge: false, eligible_for_report: true, citations: [], evidence: [],
    integrity: { id: 1, stage: 'integrity', verdict: 'passed', reason: 'all quotes found', model: null, details: {}, passages: [], created_at: '2026-01-01T00:02:00Z' },
    semantic: { id: 2, stage: 'semantic', verdict: 'supported', reason: 'fixture explanation', model: 'fixture-model', details: {}, passages: [], created_at: '2026-01-01T00:03:00Z' },
    ...over,
  }
}

export const CLAIM_A = claim({
  id: 1, text: 'The cache holds   512 entries <i>exactly</i>.',
  evidence: [{ id: 11, position: 0, passage_ref: 'S1-P0', passage_id: 'S1-P0', resolution: 'resolved', quote: 'The cache holds 512 entries.', passage: { id: 'S1-P0', text: PASSAGE_A, source_id: 1, final_url: 'https://example.test/a', title: 'Source A' } }],
})
export const CLAIM_B = claim({
  id: 2, text: 'The engine is embedded.',
  evidence: [{ id: 12, position: 0, passage_ref: 'S2-P3', passage_id: 'S2-P3', resolution: 'resolved', quote: 'the engine is embedded', passage: { id: 'S2-P3', text: PASSAGE_B, source_id: 2, final_url: 'https://example.test/b', title: 'Source B' } }],
})

const cite = (c: Claim) =>
  c.evidence.map((e) => ({ passage_id: e.passage_id!, role: 'claim_evidence', quotes: [e.quote], source_id: e.passage!.source_id, url: e.passage!.final_url, title: e.passage!.title }))

export const REPORT: Report = {
  id: 3, run_id: 7, outcome: 'claims_with_gaps', fully_addressed: false, coverage_round: 1, corrective_rounds: 1, stop_reason: 'corrective rounds exhausted (1)',
  created_at: '2026-01-01T00:04:00Z',
  sections: [{ position: 1, label: 'Section 1', subquestion_id: 1, subquestion_text: 'FIXTURE subquestion', claims: [
    { claim_id: 1, claim_version: 1, text: CLAIM_A.text, semantic_result_id: 2, citations: cite(CLAIM_A) },
    { claim_id: 2, claim_version: 1, text: CLAIM_B.text, semantic_result_id: 2, citations: cite(CLAIM_B) },
  ] }],
  requirements: [
    { requirement_key: 'Q1.R1', requirement_text: 'Cache size', status: 'addressed', gap: '' },
    { requirement_key: 'Q1.R2', requirement_text: 'Write concurrency', status: 'unresolved', gap: 'no evidence on writers' },
  ],
  unresolved_requirements: [{ requirement_key: 'Q1.R2', requirement_text: 'Write concurrency', status: 'unresolved', gap: 'no evidence on writers' }],
  omissions: [], markdown: '# FIXTURE report',
}

export type Routes = Record<string, unknown>

/** Install a fetch stub: exact path -> JSON body (or a function returning one), or {__status, detail} for an error. Records calls. */
export function stubFetch(routes: Routes) {
  const calls: { path: string; method: string }[] = []
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api/, '')
    calls.push({ path, method: init?.method ?? 'GET' })
    const hit = routes[path]
    if (hit === undefined) return new Response(JSON.stringify({ detail: `no fixture for ${path}` }), { status: 404 })
    const body = typeof hit === 'function' ? (hit as (i?: RequestInit) => unknown)(init) : hit
    if (body && typeof body === 'object' && '__status' in body) {
      const b = body as { __status: number; detail: unknown }
      return new Response(JSON.stringify({ detail: b.detail }), { status: b.__status })
    }
    return new Response(JSON.stringify(body), { status: 200 })
  }) as typeof fetch
  return calls
}
