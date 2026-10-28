import type { ConsistencyFinding, ReportClaim, ReportConsistency } from '@/api/types'
import { Notice, Pill } from './bits'

/** Wording only: every sentence here restates stored fields. Completeness (`status`) and the flagged conflicts are independent: a partial or failed check still lists its conflicts. */
const LIMITS: Record<string, string> = {
  shortened: 'quotes were shortened', absent: 'no quote text is stored', id_only: 'only the passage ID was shown, not its text', none: 'no evidence was shown',
}

const ids = (list: number[]) => list.map((i) => `claim ${i}`).join(', ')
const sentence = (text: string | undefined) => { const t = (text ?? '').trim().replace(/[.;:,\s]+$/, ''); return t ? `${t}.` : '' }

function evidenceNote(f: ConsistencyFinding): string {
  if (f.evidence.length === 0) return ''
  return ' Evidence relied on: ' + f.evidence.map((e) => `claim ${e.claim_id} passage ${e.passage_id}${e.text_shown ? '' : ' (passage ID only; its text was not shown to the model)'}`).join('; ') + '.'
}

/** The conflicts flagged in a report, or null when there is nothing to say (no consistency metadata, a historical report, or fewer than two claims). */
export function conflictsOf(c: ReportConsistency | undefined): ConsistencyFinding[] {
  return c && c.status !== 'not_recorded' ? c.findings.filter((f) => f.kind === 'conflict') : []
}

export function ConsistencyNotice({ consistency, compact = false }: { consistency?: ReportConsistency; compact?: boolean }) {
  if (!consistency || consistency.status === 'not_recorded' || consistency.status === 'not_applicable') return null
  const conflicts = conflictsOf(consistency)
  const limited = Object.entries(consistency.limited_evidence ?? {}).filter(([, v]) => v !== 'not_shown')
  const unchecked = consistency.unchecked_claim_ids ?? []
  const status = consistency.status
  const limitation =
    status === 'reviewed' ? null
      : status === 'partial' ? `The check was only partial: ${sentence(consistency.reason)}${unchecked.length ? ` Not compared: ${ids(unchecked)}.` : ''}`
        : status === 'failed' ? `The consistency check failed: ${sentence(consistency.reason)}`
          : `Consistency between the claims was not checked: ${sentence(consistency.reason)}`

  if (compact && conflicts.length === 0 && status === 'reviewed') return null
  if (conflicts.length === 0 && status === 'reviewed') {
    return (
      <Notice tone="neutral" title="Cross-claim check">
        The model flagged no conflicts among the claims. This does not show that the claims are consistent.
        {limited.length > 0 && <> For some claims the model saw limited evidence ({limited.map(([k, v]) => `claim ${k}: ${LIMITS[v] ?? v}`).join('; ')}).</>}
      </Notice>
    )
  }
  return (
    <Notice tone="gap" title={conflicts.length > 0 ? 'Possible conflict between claims' : status === 'failed' ? 'Consistency check failed' : status === 'not_checked' ? 'Claims not checked against each other' : 'Claims only partly checked against each other'}>
      {conflicts.length > 0 && (
        <>
          <p>Each claim below is supported by its own citations, but the model flagged that these may contradict each other. This does not say which one, if either, is wrong.</p>
          <ul className="list-disc pl-5">
            {conflicts.map((f) => <li key={f.id}>{ids(f.claim_ids)}: {f.explanation}{compact ? '' : evidenceNote(f)}</li>)}
          </ul>
        </>
      )}
      {limitation && <p className={conflicts.length > 0 ? 'mt-1.5' : undefined}>{limitation}</p>}
      {!compact && limited.length > 0 && <p className="mt-1.5">Limited evidence for the model: {limited.map(([k, v]) => `claim ${k}: ${LIMITS[v] ?? v}`).join('; ')}.</p>}
      {compact && <p className="mt-1.5">The full report lists the details.</p>}
    </Notice>
  )
}

/** One label per flagged conflict a report claim is part of; the same claim carries it wherever it appears (answers, related context, cross-references, Overview). */
export function ConflictPills({ item }: { item: Pick<ReportClaim, 'conflicts'> }) {
  if (!item.conflicts?.length) return null
  return <>{item.conflicts.map((c) => <Pill key={c.finding_id} tone="gap" icon="alert">Possible conflict with {ids(c.with_claim_ids)}</Pill>)}</>
}
