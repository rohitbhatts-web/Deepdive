import type { Claim, Completion, ModelCall, Report, RunEvent, RunSummary } from '@/api/types'
import { failureNotice, isRunActive, type Tone } from './claimState'

// Everything the Overview shows is derived here from stored rows. Nothing is estimated, scored or given a limit that was not recorded.

// ---------------------------------------------------------------- coverage bars

export interface CoverageRow { subquestionId: number; label: string; addressed: number; partial: number; open: number; total: number }

/** Requirement statuses counted per sub-question. "Partial" is partially_addressed; "Open" is everything else that is not addressed (unresolved or not assessed). */
export function coverageBySubquestion(requirements: { subquestion_id: number; status: string }[], questions: { id: number; question: string }[]): CoverageRow[] {
  const rows = new Map<number, CoverageRow>()
  for (const q of questions) rows.set(q.id, { subquestionId: q.id, label: q.question, addressed: 0, partial: 0, open: 0, total: 0 })
  for (const r of requirements) {
    const row = rows.get(r.subquestion_id) ?? { subquestionId: r.subquestion_id, label: `Sub-question ${r.subquestion_id}`, addressed: 0, partial: 0, open: 0, total: 0 }
    row.total += 1
    if (r.status === 'addressed') row.addressed += 1
    else if (r.status === 'partially_addressed') row.partial += 1
    else row.open += 1
    rows.set(r.subquestion_id, row)
  }
  return [...rows.values()].filter((r) => r.total > 0)
}

export function coverageTotals(rows: CoverageRow[]) {
  return rows.reduce((t, r) => ({ addressed: t.addressed + r.addressed, partial: t.partial + r.partial, open: t.open + r.open, total: t.total + r.total }), { addressed: 0, partial: 0, open: 0, total: 0 })
}

// ---------------------------------------------------------------- model requests by stage

export interface StageRow { agent: string; label: string; calls: number; failed: number }

const AGENT_LABEL: Record<string, string> = {
  planner: 'Plan', researcher: 'Search and read', relevance: 'Link sources', drafter: 'Draft claims', verifier: 'Verify claims',
  coverage: 'Assess coverage', reviser: 'Revise claims', synthesizer: 'Write report',
}
const AGENT_ORDER = Object.keys(AGENT_LABEL)

/** Recorded model calls per stage (agent), in pipeline order. Counts only: no per-stage limit, time, cost or token figure is implied. */
export function requestsByStage(calls: Pick<ModelCall, 'agent' | 'ok'>[]): StageRow[] {
  const rows = new Map<string, StageRow>()
  for (const c of calls) {
    const r = rows.get(c.agent) ?? { agent: c.agent, label: AGENT_LABEL[c.agent] ?? c.agent, calls: 0, failed: 0 }
    r.calls += 1
    if (!c.ok) r.failed += 1
    rows.set(c.agent, r)
  }
  const rank = (a: string) => (AGENT_ORDER.includes(a) ? AGENT_ORDER.indexOf(a) : AGENT_ORDER.length)
  return [...rows.values()].sort((a, b) => rank(a.agent) - rank(b.agent) || a.agent.localeCompare(b.agent))
}

// ---------------------------------------------------------------- lifecycle

export type LifeState = 'recorded' | 'running' | 'stopped' | 'failed' | 'not_recorded' | 'waiting'
export interface LifeStage { key: string; label: string; state: LifeState; at: string | null }

const STAGES: { key: string; label: string; events: string[]; failTitle: string[] }[] = [
  { key: 'plan', label: 'Plan', events: ['plan_created'], failTitle: [] },
  { key: 'collect', label: 'Collect', events: ['research_finished'], failTitle: ['Collection'] },
  { key: 'draft', label: 'Draft', events: ['claims_drafted'], failTitle: ['Drafting'] },
  { key: 'verify', label: 'Verify', events: ['claims_verified', 'verification_finished'], failTitle: ['Verification'] },
  { key: 'assess', label: 'Assess', events: ['coverage_assessed'], failTitle: ['Coverage assessment'] },
  { key: 'report', label: 'Report', events: ['report_created'], failTitle: ['Report assembly', 'Completion'] },
]

/**
 * The stages of a run from its RECORDED events and terminal states. A stage with a stored event is "recorded" (with the event's time). Only a run that is still
 * active can show a stage as running, and never once a report exists (a later job's progress is on the Run history tab). After the run has ended, a stage with
 * no event is "not recorded"; the stage a failure or a stop belongs to says failed or stopped.
 */
export function lifecycle(run: RunSummary, events: RunEvent[], completion: Pick<Completion, 'stop_reason'> | null): LifeStage[] {
  const last = (types: string[]) => [...events].reverse().find((e) => types.includes(e.event_type)) ?? null
  const found = STAGES.map((s) => last(s.events))
  const hasReport = run.report_outcome !== null || found[5] !== null
  const active = isRunActive(run)
  const failure = active ? null : failureNotice(run, completion)
  const stopped = !active && !failure && (run.complete_status === 'stopped' || run.status === 'stopped')
  const firstMissing = found.findIndex((e) => e === null)
  return STAGES.map((s, i) => {
    const ev = found[i]
    if (ev) return { key: s.key, label: s.label, state: 'recorded' as const, at: ev.created_at }
    if (failure && s.failTitle.some((t) => failure.title.startsWith(t))) return { key: s.key, label: s.label, state: 'failed' as const, at: null }
    if (active && !hasReport) return { key: s.key, label: s.label, state: i === firstMissing ? 'running' as const : 'waiting' as const, at: null }
    if (active) return { key: s.key, label: s.label, state: 'waiting' as const, at: null }
    if (stopped && i === firstMissing) return { key: s.key, label: s.label, state: 'stopped' as const, at: null }
    return { key: s.key, label: s.label, state: 'not_recorded' as const, at: null }
  })
}

// ---------------------------------------------------------------- plain-language outcome

export interface Outcome { headline: string; detail: string; tone: Tone }

export function outcomeSummary(run: RunSummary, report: Report | null, completion: Completion | null, requirements: { status?: string }[], stale = 0): Outcome {
  if (isRunActive(run)) return { headline: 'Research is in progress.', detail: 'The answer appears here when the run finishes. Nothing below is final yet.', tone: 'neutral' }
  const failure = failureNotice(run, completion)
  if (failure) return { headline: `${failure.title}.`, detail: failure.detail, tone: 'bad' }
  if (!report) return { headline: run.status === 'created' ? 'Research has not started.' : 'This run has no report.', detail: run.stop_reason ?? 'No report was stored for it.', tone: 'neutral' }
  const open = requirements.filter((r) => r.status !== 'addressed').length
  const answered = new Set(report.sections.flatMap((s) => s.claims.map((c) => c.claim_id))).size
  if (report.outcome === 'insufficient_evidence') {
    return { headline: 'No claim could be verified and reported.', detail: 'This does not show that the answer does not exist, only that this run did not establish it from the pages it collected.', tone: 'gap' }
  }
  if (report.outcome === 'claims_all_requirements_addressed') {
    return { headline: `${answered} verified claim${answered === 1 ? '' : 's'} answer${answered === 1 ? 's' : ''} the question.`, detail: 'Every requirement was assessed as addressed. That is a model assessment, not a guarantee.', tone: 'ok' }
  }
  if (stale > 0) {  // the report is a historical snapshot: say plainly when claims in it no longer count under the current rules
    return { headline: `The report lists ${answered} claim${answered === 1 ? '' : 's'} as answers; ${stale} ${stale === 1 ? 'is' : 'are'} not currently report-eligible.`, detail: `${open} of ${requirements.length} requirement${requirements.length === 1 ? '' : 's'} ${open === 1 ? 'is' : 'are'} not fully answered. Claims marked Needs re-judge were verified under an older rubric.`, tone: 'gap' }
  }
  return { headline: `${answered} verified claim${answered === 1 ? '' : 's'} answer${answered === 1 ? 's' : ''} part of the question.`, detail: `${open} of ${requirements.length} requirement${requirements.length === 1 ? '' : 's'} ${open === 1 ? 'is' : 'are'} still not fully answered.`, tone: 'gap' }
}

// ---------------------------------------------------------------- evidence map

export const MAP_LIMITS = { claims: 4, passages: 6, sources: 4 } as const

export interface MapClaim { id: number; text: string; inReport: boolean; eligible: boolean }
export interface MapPassage { id: string; sourceId: number; claimIds: number[]; contextOnlyFor: number[] }
export interface MapSource { id: number; title: string; url: string; passages: number; cited: number }
export interface MapEdge { from: string; to: string; kind: 'contains' | 'cites' | 'context' }
/** Every claim that cites something, with all of its stored citations: the complete list behind the bounded graph. */
export interface MapListClaim extends MapClaim { cites: { passageId: string; sourceId: number; sourceTitle: string; context: boolean }[] }
export interface EvidenceMapData {
  list: MapListClaim[]
  claims: MapClaim[]; passages: MapPassage[]; sources: MapSource[]; edges: MapEdge[]
  counts: { claimsShown: number; claimsTotal: number; passagesShown: number; passagesCitedTotal: number; sourcesShown: number; sourcesTotal: number; sourcesCited: number }
}

const ordinal = (passageId: string) => Number(/-P(\d+)$/.exec(passageId)?.[1] ?? 0)

/**
 * A manageable subset of the REAL stored graph: sources -> passages -> claims. Claims in the report come first, then other eligible claims, then the rest; their cited
 * passages follow (cited-as-evidence before neighbour context), then the sources of those passages. Every edge is a stored citation, or a passage's own source.
 * Counts say how much was left out; the list view and the Evidence tab show everything.
 */
export function evidenceMap(claims: Claim[], sources: { id: number; title: string | null; final_url: string; passage_count: number }[], reportClaimIds: Set<number>, limits = MAP_LIMITS): EvidenceMapData {
  const ranked = [...claims].filter((c) => c.citations.length > 0).sort((a, b) => Number(reportClaimIds.has(b.id)) - Number(reportClaimIds.has(a.id)) || Number(b.eligible_for_report) - Number(a.eligible_for_report) || a.id - b.id)
  const chosen = ranked.slice(0, limits.claims)
  const passageMap = new Map<string, MapPassage>()
  const edges: MapEdge[] = []
  const allCited = new Set(claims.flatMap((c) => c.citations.map((ct) => ct.passage_id)))
  for (const c of chosen) {
    for (const ct of [...c.citations].sort((a, b) => Number(a.role === 'neighbour_context') - Number(b.role === 'neighbour_context'))) {
      if (!passageMap.has(ct.passage_id) && passageMap.size >= limits.passages) continue
      const p = passageMap.get(ct.passage_id) ?? { id: ct.passage_id, sourceId: ct.source_id, claimIds: [], contextOnlyFor: [] }
      if (ct.role === 'neighbour_context') p.contextOnlyFor.push(c.id)
      else p.claimIds.push(c.id)
      passageMap.set(ct.passage_id, p)
      edges.push({ from: `psg-${ct.passage_id}`, to: `clm-${c.id}`, kind: ct.role === 'neighbour_context' ? 'context' : 'cites' })
    }
  }
  const passages = [...passageMap.values()].sort((a, b) => a.sourceId - b.sourceId || ordinal(a.id) - ordinal(b.id))
  const sourceIds = [...new Set(passages.map((p) => p.sourceId))].slice(0, limits.sources)
  const shownPassages = passages.filter((p) => sourceIds.includes(p.sourceId))
  const keep = new Set(shownPassages.map((p) => `psg-${p.id}`))
  const info = new Map(sources.map((s) => [s.id, s]))
  const titleOf = (id: number, fallback: string) => info.get(id)?.title ?? fallback
  const citedBy = new Map<number, string>()
  for (const c of claims) for (const ct of c.citations) citedBy.set(ct.source_id, ct.title ?? ct.url)
  const mapSources: MapSource[] = sourceIds.map((id) => ({
    id, title: titleOf(id, citedBy.get(id) ?? `Source ${id}`), url: info.get(id)?.final_url ?? '', passages: info.get(id)?.passage_count ?? 0,
    cited: shownPassages.filter((p) => p.sourceId === id).length,
  }))
  const allEdges = [
    ...mapSources.flatMap((s) => shownPassages.filter((p) => p.sourceId === s.id).map((p) => ({ from: `src-${s.id}`, to: `psg-${p.id}`, kind: 'contains' as const }))),
    ...edges.filter((e) => keep.has(e.from)),
  ]
  const shownClaims = chosen.filter((c) => allEdges.some((e) => e.to === `clm-${c.id}`))
  const citedSources = new Set(claims.flatMap((c) => c.citations.map((ct) => ct.source_id)))
  const list = ranked.map((c) => ({ id: c.id, text: c.text, inReport: reportClaimIds.has(c.id), eligible: c.eligible_for_report,
    cites: c.citations.map((ct) => ({ passageId: ct.passage_id, sourceId: ct.source_id, sourceTitle: titleOf(ct.source_id, ct.title ?? ct.url), context: ct.role === 'neighbour_context' })) }))
  return {
    list,
    claims: shownClaims.map((c) => ({ id: c.id, text: c.text, inReport: reportClaimIds.has(c.id), eligible: c.eligible_for_report })),
    passages: shownPassages, sources: mapSources, edges: allEdges,
    counts: { claimsShown: shownClaims.length, claimsTotal: claims.length, passagesShown: shownPassages.length, passagesCitedTotal: allCited.size, sourcesShown: mapSources.length, sourcesTotal: sources.length, sourcesCited: citedSources.size },
  }
}

export const requirementShort: Record<string, string> = { addressed: 'Addressed', partially_addressed: 'Partial', unresolved: 'Open' }
