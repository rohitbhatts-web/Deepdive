import type { RunEvent } from '@/api/types'

const LABEL: Record<string, string> = {
  plan_created: 'Plan created',
  assessment_rejected: 'Source relevance answer rejected',
  association_declined: 'Source not linked to a sub-question',
  association_skipped: 'Source linking skipped',
  claims_verified: 'Claims verified',
  draft_truncated: 'Draft cut to the claim limit',
  integrity_checked: 'Quotes checked against stored passages',
  job_retry_started: 'Retry from stored evidence started',
  no_usable_search_results: 'No usable search results',
  query_sanitized: 'Search query cleaned',
  query_skipped: 'Search query skipped',
  result_skipped: 'Search result skipped',
  selection_rejected: 'Page selection rejected',
  verification_skipped: 'Verification skipped',
  run_created: 'Run created',
  queries_generated: 'Search queries written',
  search_completed: 'Search completed',
  search_failed: 'Search failed',
  urls_selected: 'Pages selected to read',
  source_ingested: 'Source stored',
  source_duplicate: 'Duplicate source skipped',
  fetch_failed: 'Page fetch failed',
  subquestion_done: 'Sub-question finished',
  research_finished: 'Collection finished',
  associations_assessed: 'Sources linked to sub-questions',
  claims_drafted: 'Claims drafted',
  claim_discarded: 'Draft claim discarded',
  draft_input: 'Passages offered to the drafter',
  draft_skipped: 'Drafting skipped',
  claims_rejudged: 'Older verdicts re-judged',
  claims_reverified: 'Claims re-verified',
  verification_started: 'Verification started',
  verification_finished: 'Verification finished',
  obligations_derived: 'Coverage obligations derived',
  coverage_assessed: 'Coverage assessed',
  coverage_routed: 'Correction tasks planned',
  correction_round_started: 'Correction round started',
  correction_round_finished: 'Correction round finished',
  candidates_drafted: 'Candidate claims drafted from new evidence',
  drafting_skipped: 'Drafting from new evidence skipped',
  drafting_failed: 'Drafting from new evidence failed',
  claim_revised: 'Claim revised',
  claim_split: 'Claim split into parts',
  claim_excluded: 'Claim excluded',
  split_skipped: 'Split not made',
  report_created: 'Report created',
  completion_finished: 'Completion finished',
}

export const STAGE_LABEL: Record<string, string> = {
  api: 'Request',
  planner: 'Planning',
  integrity: 'Checking evidence',
  researcher: 'Searching and reading',
  ingest: 'Fetching sources',
  relevance: 'Linking sources',
  drafter: 'Drafting claims',
  verifier: 'Verifying claims',
  rejudge: 'Re-judging verdicts',
  coverage: 'Assessing coverage',
  correct: 'Correcting gaps',
  reviser: 'Revising claims',
  report: 'Writing report',
  graph: 'Run control',
  startup: 'Backend restart',
}

export function eventLabel(e: RunEvent): string {
  return LABEL[e.event_type] ?? e.event_type.replace(/_/g, ' ')
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

/** One factual line from the stored payload; nothing is inferred. */
export function eventDetail(e: RunEvent): string | null {
  const p = e.payload
  switch (e.event_type) {
    case 'search_completed':
    case 'search_failed':
      return str(p.query) ?? str(p.error)
    case 'queries_generated':
      return Array.isArray(p.queries) ? p.queries.join(' | ') : null
    case 'fetch_failed':
      return [str(p.url), str(p.outcome) ?? str(p.reason)].filter(Boolean).join(' - ')
    case 'source_ingested':
    case 'source_duplicate':
      return [str(p.title), str(p.final_url) ?? str(p.url)].filter(Boolean).join(' - ')
    case 'claims_drafted':
    case 'candidates_drafted':
      return Array.isArray(p.claim_ids) ? `claims ${p.claim_ids.join(', ')}` : null
    case 'claim_revised':
    case 'claim_split':
    case 'claim_excluded':
      return str(p.reason)
    case 'coverage_assessed':
    case 'coverage_routed':
    case 'correction_round_started':
    case 'correction_round_finished':
      return typeof p.round === 'number' ? `round ${p.round}` : null
    case 'drafting_skipped':
    case 'draft_skipped':
      return str(p.reason)
    case 'completion_finished':
    case 'research_finished':
    case 'verification_finished':
      return str(p.stop_reason) ?? str(p.status)
    case 'report_created':
      return str(p.outcome)
    case 'job_retry_started':
      return typeof p.attempt === 'number' ? `attempt ${p.attempt}; no searches or fetches` : null
    default:
      return null
  }
}
