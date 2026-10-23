import type { Claim } from '@/api/types'

export type Tone = 'ok' | 'gap' | 'bad' | 'neutral'
export type StateIcon = 'check' | 'alert' | 'x' | 'help' | 'flask' | 'ban' | 'clock'

export interface ClaimState {
  key: string
  label: string
  tone: Tone
  icon: StateIcon
  explain: string
}

type StateInput = Pick<Claim, 'origin' | 'excluded' | 'integrity_verdict' | 'semantic_verdict' | 'needs_rejudge' | 'superseded_by'>

/**
 * One label per claim, in strict order of what must be said first. "Supported" is only ever returned for a stored
 * `supported` semantic verdict on a claim whose integrity check passed; a missing or errored verdict never reads as support.
 */
export function claimState(c: StateInput): ClaimState {
  if (c.origin === 'controlled_challenge')
    return { key: 'test_input', label: 'Test input', tone: 'neutral', icon: 'flask', explain: 'A deliberately wrong claim used to test the verifier. It is never part of a report.' }
  if (c.excluded)
    return { key: 'excluded', label: 'Excluded', tone: 'bad', icon: 'ban', explain: `Retired by the correction loop: ${c.excluded.reason}` }
  if (c.integrity_verdict === 'failed')
    return { key: 'integrity_failed', label: 'Integrity failed', tone: 'bad', icon: 'x', explain: 'A quote was not found in the stored passage, so the claim was never judged for support.' }
  if (c.integrity_verdict === null)
    return { key: 'unchecked', label: 'Not checked', tone: 'neutral', icon: 'help', explain: 'The evidence integrity check has not run for this claim.' }
  switch (c.semantic_verdict) {
    case null:
      return { key: 'not_judged', label: 'Not judged', tone: 'neutral', icon: 'help', explain: 'Integrity passed, but no semantic verdict exists yet. This is not a rejection and not support.' }
    case 'error':
      return { key: 'verification_error', label: 'Verification error', tone: 'gap', icon: 'alert', explain: 'The verifier failed to return a usable verdict. The claim is neither supported nor shown to be unsupported.' }
    case 'supported':
      return c.needs_rejudge
        ? { key: 'needs_rejudge', label: 'Needs re-judge', tone: 'gap', icon: 'clock', explain: 'Supported under an older verifier rubric; not trusted for reports until re-judged.' }
        : { key: 'supported', label: 'Supported', tone: 'ok', icon: 'check', explain: 'Integrity passed and the verifier judged the cited passages to support the claim.' }
    case 'partially_supported':
      return { key: 'partially_supported', label: 'Partially supported', tone: 'gap', icon: 'alert', explain: 'The cited passages support only part of the claim.' }
    case 'unsupported':
      return { key: 'unsupported', label: 'Unsupported', tone: 'bad', icon: 'x', explain: 'The verifier judged that the cited passages do not support the claim.' }
    case 'contradicted':
      return { key: 'contradicted', label: 'Contradicted', tone: 'bad', icon: 'x', explain: 'The cited passages contradict the claim.' }
    default:
      return { key: 'unknown', label: String(c.semantic_verdict), tone: 'neutral', icon: 'help', explain: 'Unrecognised verdict value.' }
  }
}

export const REQUIREMENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  addressed: { label: 'Addressed', tone: 'ok' },
  partially_addressed: { label: 'Partially addressed', tone: 'gap' },
  unresolved: { label: 'Unresolved', tone: 'gap' },
}

export const REPORT_OUTCOME: Record<string, { label: string; tone: Tone; text: string }> = {
  insufficient_evidence: { label: 'Insufficient evidence', tone: 'gap', text: 'No verified claim could be reported.' },
  claims_with_gaps: { label: 'Claims with gaps', tone: 'gap', text: 'Verified claims are listed below; some requirements are not fully addressed.' },
  claims_all_requirements_addressed: { label: 'All requirements addressed', tone: 'ok', text: 'Every requirement was assessed as addressed. That is a model assessment, not a guarantee.' },
}

export interface FailureNotice {
  title: string
  detail: string
}

/**
 * The overall failure notice, derived from WHICH job or stage failed, even when `collection_finished` is what is stored for the run.
 * Returns null when nothing failed (a stopped run is described separately).
 */
export function failureNotice(
  run: { status: string; verify_status: string; complete_status: string; stop_reason: string | null },
  completion: { stop_reason: string | null } | null,
): FailureNotice | null {
  if (run.status === 'failed') return { title: 'Collection failed', detail: run.stop_reason ?? 'No reason recorded.' }
  if (run.verify_status !== 'failed' && run.complete_status !== 'failed') return null
  const reason = completion?.stop_reason ?? 'No reason recorded.'
  const stage = /^drafter failed/.test(reason)
    ? 'Drafting'
    : /^coverage assessment failed/.test(reason)
      ? 'Coverage assessment'
      : /^report assembly failed/.test(reason)
        ? 'Report assembly'
        : /^(verifier|verification)/.test(reason) || run.verify_status === 'failed'
          ? 'Verification'
          : 'Completion'
  return { title: `${stage} failed after collection finished`, detail: reason }
}

export function isRunActive(run: { status: string; verify_status: string; complete_status: string }): boolean {
  return run.status === 'researching' || run.verify_status === 'verifying' || run.complete_status === 'completing'
}

/** The run's overall state in words, from persisted statuses only. */
export function runStatusLabel(run: { status: string; verify_status: string; complete_status: string; report_outcome: string | null }): { label: string; tone: Tone } {
  if (isRunActive(run)) return { label: 'Running', tone: 'neutral' }
  if (run.status === 'failed' || run.complete_status === 'failed') return { label: 'Failed', tone: 'bad' }
  if (run.status === 'created') return { label: 'Not started', tone: 'neutral' }
  if (run.complete_status === 'completed' && run.report_outcome === null) return { label: 'Completed, no report', tone: 'gap' }
  if (run.complete_status === 'completed' && run.report_outcome === 'insufficient_evidence') return { label: 'Insufficient evidence', tone: 'gap' }
  if (run.complete_status === 'completed')
    return run.report_outcome === 'claims_all_requirements_addressed' ? { label: 'Completed', tone: 'ok' } : { label: 'Completed with gaps', tone: 'gap' }
  if (run.complete_status === 'stopped' || run.status === 'stopped') return { label: 'Stopped', tone: 'gap' }
  return { label: 'Collected', tone: 'neutral' }
}
