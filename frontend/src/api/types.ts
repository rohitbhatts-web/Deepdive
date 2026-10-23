// Response shapes of the existing FastAPI backend (see backend/api/*.py). Only the fields the UI reads are listed.

export interface RunSummary {
  id: number
  question: string
  status: string // created | researching | collection_finished | stopped | failed
  verify_status: string
  complete_status: string
  stop_reason: string | null
  report_outcome: string | null
  created_at: string
  updated_at: string
}

export interface RunPage {
  items: RunSummary[]
  total: number
  limit: number
  offset: number
}

export interface Limits {
  limits: Record<string, number>
  question_min_chars: number
  question_max_chars: number
  max_correction_rounds: number
}

export interface ResearchStatus {
  run_id: number
  status: string
  stop_reason: string | null
  updated_at: string
  subquestions_by_status: Record<string, number>
  counts: Record<string, number>
  model_usage: { model_calls: number; failed_calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }
  limits: Record<string, number>
}

export interface Completion {
  run_id: number
  complete_status: string
  stop_reason: string | null
  corrective_rounds: number
  max_corrective_rounds: number
  coverage_round: number | null
  coverage: Record<string, unknown> | null
  report_id: number | null
  report_outcome: string | null
  budget_used_of_limit: Record<string, [number, number]> | null
}

export interface RetryPlan {
  run_id: number
  allowed: boolean
  reasons: string[]
  attempt: number
  stored_evidence: { sources: number; passages: number; claims: number }
  new_job_limits: Record<string, number>
  retrieval: string
  previous_job: { stop_reason: string | null; status: string | null; finished_at: string; budget_used_of_limit: Record<string, [number, number]> | null } | null
  note: string
}

export interface RunEvent {
  id: number
  event_type: string
  stage: string
  created_at: string
  payload: Record<string, unknown>
}

export interface Citation {
  passage_id: string
  role: string
  quotes: string[]
  source_id: number
  url: string
  title: string | null
}

export interface ReportClaim {
  claim_id: number
  claim_version: number
  text: string
  semantic_result_id: number
  verifier_rubric?: string | null
  role?: 'direct_answer' | 'relevant_context' | null
  requirement_keys?: string[]
  role_reason?: string | null
  requirement_texts?: string[]
  citations: Citation[]
}

export interface ReportSection {
  position: number
  label: string
  subquestion_id: number
  subquestion_text: string
  claims: ReportClaim[] // direct answers (every claim of a report made without relevance judgments)
  context?: ReportClaim[] // verified claims that relate to the subquestion but do not answer it
  answered?: boolean | null // false: no direct answer was found; null/absent: the report was made without relevance judgments
  open_requirements?: { requirement_key: string; requirement_text: string; status: string; gap: string }[] // this section's requirements no claim directly answers
}

export interface ReportRequirement {
  requirement_key?: string
  requirement_text?: string
  status?: string
  gap?: string
  [k: string]: unknown
}

export interface Report {
  id: number
  run_id: number
  outcome: string
  fully_addressed: boolean
  coverage_round: number | null
  corrective_rounds: number
  stop_reason: string | null
  created_at: string
  relevance_judged?: boolean
  verifier_rubrics?: string[]
  sections: ReportSection[]
  requirements: ReportRequirement[]
  unresolved_requirements: ReportRequirement[]
  omissions: Record<string, unknown>[]
  markdown: string
}

export interface ResultOut {
  id: number
  stage: string
  verdict: string
  reason: string
  model: string | null
  details: Record<string, unknown>
  passages: { passage_id: string; role: string }[]
  created_at: string
}

export interface Evidence {
  id: number
  position: number
  passage_ref: string
  passage_id: string | null
  resolution: string
  quote: string
  passage: { id: string; text: string; source_id: number; final_url: string; title: string | null } | null
}

export interface Claim {
  id: number
  subquestion_id: number
  parent_claim_id: number | null
  version: number
  text: string
  origin: string
  provenance: Record<string, unknown>
  created_at: string
  status: string
  integrity_verdict: string | null
  semantic_verdict: string | null
  superseded_by: number | null
  excluded: { reason: string; planned_round: number } | null
  needs_rejudge: boolean
  eligible_for_report: boolean
  citations: Citation[]
  evidence: Evidence[]
  integrity: ResultOut | null
  semantic: ResultOut | null
  history?: ResultOut[]
}

export interface Obligation {
  obligation_key: string
  subject: string
  dimension: string
  status: string
  gap: string
  missing_kind: string
  supporting_claim_ids: number[]
}

export interface CoverageRequirement {
  requirement_key: string
  subquestion_id: number
  requirement_text: string
  status: string
  gap: string
  missing_kind: string
  supporting_claim_ids: number[]
  obligations: Obligation[]
}

export interface Coverage {
  round: number | null
  summary: Record<string, unknown>
  requirements: CoverageRequirement[]
}

export interface CorrectionTask {
  id: number
  kind: string
  status: string
  requirement_key: string | null
  claim_id: number | null
  spec: Record<string, unknown>
  outcome: Record<string, unknown>
}

export interface CorrectionRound {
  planned_round: number
  corrective_round: number
  executed: boolean
  coverage_before: Record<string, unknown> | null
  coverage_after: Record<string, unknown> | null
  tasks: CorrectionTask[]
}

export interface Revision {
  claim_id: number
  version: number
  parent_claim_id: number | null
  text: string
  status: string
  integrity_verdict: string | null
  semantic_verdict: string | null
  eligible_for_report: boolean
  excluded: { reason: string } | null
  superseded_by: number | null
  provenance: Record<string, unknown>
}

export interface Corrections {
  rounds: CorrectionRound[]
  revisions: Revision[][]
  exclusions: { claim_id: number; reason: string; planned_round: number }[]
}

export interface SearchRow {
  id: number
  query: string
  provider: string
  created_at: string
  error: string | null
  selections: { url: string; outcome: string; reason: string | null; source_id: number | null }[]
}

export interface ModelCall {
  id: number
  agent: string
  model: string
  ok: boolean
  error: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
}

/** GET /demo. `read_only` is true only for the hosted recorded demo; the other fields exist then. */
export interface DemoInfo {
  read_only: boolean
  label: string
  notice: string
  withholding: string
  limitations: string[]
  runs: { run_id: number; note: string }[]
}
