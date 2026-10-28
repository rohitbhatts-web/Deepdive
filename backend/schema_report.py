"""Phase 4 tables: claim exclusions, coverage assessments, correction tasks, and immutable report snapshots.

Rules enforced in SQLite itself (a bug in application code cannot break them):
- Exclusions, coverage rows and every report table are append-only: no UPDATE, no DELETE. A generated report is a
  snapshot; a later revision creates a NEW claim version (and possibly a new report) and cannot change an old report.
- A claim can enter a report only if, at insert time, it is a naturally drafted claim of the report's run, not excluded,
  not superseded by a newer version, its latest integrity result passed and its latest semantic verdict is 'supported'.
  The stored text and version must equal the claim row exactly (the report copies verified text; it never rewrites it).
  This mirrors `claims.is_report_eligible`; the Python function additionally checks the verifier rubric version.
- A report citation must be a passage the claim's latest semantic verdict actually relied on.
- Correction tasks are the only mutable table here: they have a lifecycle (planned -> done / skipped / failed).
- Coverage obligations (the subject x dimension pairs a requirement asks about) and their per-round assessments are
  append-only and confined to their run; a requirement's status is DERIVED from its obligations in code, never entered.
"""

REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_dispositions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    claim_id INTEGER NOT NULL UNIQUE REFERENCES claims(id),
    disposition TEXT NOT NULL CHECK (disposition IN ('excluded')),
    reason TEXT NOT NULL,
    planned_round INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage_assessments (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    round INTEGER NOT NULL,
    requirement_key TEXT NOT NULL,
    subquestion_id INTEGER NOT NULL REFERENCES subquestions(id),
    requirement_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('addressed', 'partially_addressed', 'unresolved')),
    gap TEXT NOT NULL DEFAULT '',
    missing_kind TEXT NOT NULL DEFAULT 'none',
    model TEXT,
    model_call_id INTEGER REFERENCES model_calls(id),
    notes TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (run_id, round, requirement_key)
);
CREATE TABLE IF NOT EXISTS coverage_supporting_claims (
    assessment_id INTEGER NOT NULL REFERENCES coverage_assessments(id),
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    PRIMARY KEY (assessment_id, claim_id)
);
CREATE TABLE IF NOT EXISTS correction_tasks (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    planned_round INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('rejudge', 'claim_repair', 'missing_evidence', 'claim_exclusion')),
    subquestion_id INTEGER REFERENCES subquestions(id),
    claim_id INTEGER REFERENCES claims(id),
    requirement_key TEXT,
    spec TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'done', 'skipped', 'failed')),
    outcome TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('insufficient_evidence', 'claims_with_gaps', 'claims_all_requirements_addressed')),
    coverage_round INTEGER,
    corrective_rounds INTEGER NOT NULL,
    stop_reason TEXT,
    ordering TEXT NOT NULL CHECK (ordering IN ('model', 'deterministic')),
    ordering_reason TEXT,
    model TEXT,
    model_call_id INTEGER REFERENCES model_calls(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_sections (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    subquestion_id INTEGER NOT NULL REFERENCES subquestions(id),
    subquestion_text TEXT NOT NULL,
    UNIQUE (report_id, position)
);
CREATE TABLE IF NOT EXISTS report_claims (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    section_id INTEGER NOT NULL REFERENCES report_sections(id),
    position INTEGER NOT NULL,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    claim_version INTEGER NOT NULL,
    claim_text TEXT NOT NULL,
    semantic_result_id INTEGER NOT NULL REFERENCES verification_results(id),
    UNIQUE (report_id, claim_id)
);
CREATE TABLE IF NOT EXISTS report_citations (
    id INTEGER PRIMARY KEY,
    report_claim_id INTEGER NOT NULL REFERENCES report_claims(id),
    passage_id TEXT NOT NULL REFERENCES passages(id),
    role TEXT NOT NULL,
    quotes TEXT NOT NULL DEFAULT '[]',
    source_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    UNIQUE (report_claim_id, passage_id)
);
CREATE TABLE IF NOT EXISTS report_gaps (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    requirement_key TEXT NOT NULL,
    subquestion_id INTEGER NOT NULL REFERENCES subquestions(id),
    requirement_text TEXT NOT NULL,
    status TEXT NOT NULL,
    gap TEXT NOT NULL DEFAULT '',
    missing_kind TEXT NOT NULL DEFAULT 'none'
);
CREATE TABLE IF NOT EXISTS report_omissions (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    reason TEXT NOT NULL,
    UNIQUE (report_id, claim_id)
);
CREATE INDEX IF NOT EXISTS idx_coverage_run ON coverage_assessments(run_id, round);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON correction_tasks(run_id, planned_round);
CREATE INDEX IF NOT EXISTS idx_reports_run ON reports(run_id, id);

CREATE TRIGGER IF NOT EXISTS dispositions_same_run BEFORE INSERT ON claim_dispositions
BEGIN
    SELECT RAISE(ABORT, 'a claim may only be excluded within its own run')
    WHERE (SELECT run_id FROM claims WHERE id = NEW.claim_id) IS NOT NEW.run_id;
END;
CREATE TRIGGER IF NOT EXISTS coverage_support_same_run BEFORE INSERT ON coverage_supporting_claims
BEGIN
    SELECT RAISE(ABORT, 'a coverage assessment may only cite claims of its own run')
    WHERE (SELECT run_id FROM claims WHERE id = NEW.claim_id)
          IS NOT (SELECT run_id FROM coverage_assessments WHERE id = NEW.assessment_id);
END;
CREATE TRIGGER IF NOT EXISTS report_claims_eligible BEFORE INSERT ON report_claims
BEGIN
    SELECT RAISE(ABORT, 'report claim must belong to the report''s run')
    WHERE (SELECT run_id FROM claims WHERE id = NEW.claim_id) IS NOT (SELECT run_id FROM reports WHERE id = NEW.report_id);
    SELECT RAISE(ABORT, 'report claim text must equal the stored claim text exactly')
    WHERE NEW.claim_text IS NOT (SELECT text FROM claims WHERE id = NEW.claim_id);
    SELECT RAISE(ABORT, 'report claim version must equal the stored version')
    WHERE NEW.claim_version IS NOT (SELECT version FROM claims WHERE id = NEW.claim_id);
    SELECT RAISE(ABORT, 'only naturally drafted claims may enter a report')
    WHERE (SELECT origin FROM claims WHERE id = NEW.claim_id) IS NOT 'drafted';
    SELECT RAISE(ABORT, 'excluded claims may not enter a report')
    WHERE EXISTS (SELECT 1 FROM claim_dispositions WHERE claim_id = NEW.claim_id);
    SELECT RAISE(ABORT, 'superseded claim versions may not enter a report')
    WHERE EXISTS (SELECT 1 FROM claims WHERE parent_claim_id = NEW.claim_id);
    SELECT RAISE(ABORT, 'claim integrity has not passed')
    WHERE (SELECT verdict FROM verification_results WHERE claim_id = NEW.claim_id AND stage = 'integrity'
           ORDER BY id DESC LIMIT 1) IS NOT 'passed';
    SELECT RAISE(ABORT, 'claim is not supported')
    WHERE (SELECT verdict FROM verification_results WHERE claim_id = NEW.claim_id AND stage = 'semantic'
           ORDER BY id DESC LIMIT 1) IS NOT 'supported';
    SELECT RAISE(ABORT, 'semantic result must be the claim''s latest')
    WHERE NEW.semantic_result_id IS NOT (SELECT id FROM verification_results WHERE claim_id = NEW.claim_id
           AND stage = 'semantic' ORDER BY id DESC LIMIT 1);
END;
CREATE TRIGGER IF NOT EXISTS report_citations_used BEFORE INSERT ON report_citations
BEGIN
    SELECT RAISE(ABORT, 'a report citation must be a passage the verdict actually relied on')
    WHERE NOT EXISTS (
        SELECT 1 FROM verification_result_passages vp
        WHERE vp.passage_id = NEW.passage_id
          AND vp.result_id = (SELECT semantic_result_id FROM report_claims WHERE id = NEW.report_claim_id));
END;
CREATE TRIGGER IF NOT EXISTS report_omissions_same_run BEFORE INSERT ON report_omissions
BEGIN
    SELECT RAISE(ABORT, 'an omitted claim must belong to the report''s run')
    WHERE (SELECT run_id FROM claims WHERE id = NEW.claim_id) IS NOT (SELECT run_id FROM reports WHERE id = NEW.report_id);
END;

CREATE TRIGGER IF NOT EXISTS dispositions_no_update BEFORE UPDATE ON claim_dispositions
BEGIN SELECT RAISE(ABORT, 'exclusions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS dispositions_no_delete BEFORE DELETE ON claim_dispositions
BEGIN SELECT RAISE(ABORT, 'exclusions are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS coverage_no_update BEFORE UPDATE ON coverage_assessments
BEGIN SELECT RAISE(ABORT, 'coverage assessments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coverage_no_delete BEFORE DELETE ON coverage_assessments
BEGIN SELECT RAISE(ABORT, 'coverage assessments are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS coverage_support_no_update BEFORE UPDATE ON coverage_supporting_claims
BEGIN SELECT RAISE(ABORT, 'coverage assessments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coverage_support_no_delete BEFORE DELETE ON coverage_supporting_claims
BEGIN SELECT RAISE(ABORT, 'coverage assessments are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS reports_no_update BEFORE UPDATE ON reports
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS reports_no_delete BEFORE DELETE ON reports
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_sections_no_update BEFORE UPDATE ON report_sections
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_sections_no_delete BEFORE DELETE ON report_sections
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_claims_no_update BEFORE UPDATE ON report_claims
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_claims_no_delete BEFORE DELETE ON report_claims
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_citations_no_update BEFORE UPDATE ON report_citations
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_citations_no_delete BEFORE DELETE ON report_citations
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_gaps_no_update BEFORE UPDATE ON report_gaps
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_gaps_no_delete BEFORE DELETE ON report_gaps
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_omissions_no_update BEFORE UPDATE ON report_omissions
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_omissions_no_delete BEFORE DELETE ON report_omissions
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;

CREATE TABLE IF NOT EXISTS coverage_obligations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    requirement_key TEXT NOT NULL,
    subquestion_id INTEGER NOT NULL REFERENCES subquestions(id),
    obligation_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    dimension TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('derived', 'fallback')),
    model TEXT,
    model_call_id INTEGER REFERENCES model_calls(id),
    created_at TEXT NOT NULL,
    UNIQUE (run_id, obligation_key)
);
CREATE TABLE IF NOT EXISTS obligation_assessments (
    id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL REFERENCES coverage_assessments(id),
    obligation_id INTEGER NOT NULL REFERENCES coverage_obligations(id),
    status TEXT NOT NULL CHECK (status IN ('addressed', 'partially_addressed', 'unresolved')),
    gap TEXT NOT NULL DEFAULT '',
    missing_kind TEXT NOT NULL DEFAULT 'none',
    notes TEXT NOT NULL DEFAULT '{}',
    UNIQUE (assessment_id, obligation_id)
);
CREATE TABLE IF NOT EXISTS obligation_supporting_claims (
    obligation_assessment_id INTEGER NOT NULL REFERENCES obligation_assessments(id),
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    PRIMARY KEY (obligation_assessment_id, claim_id)
);
CREATE INDEX IF NOT EXISTS idx_obligations_run ON coverage_obligations(run_id, requirement_key);

-- Answer relevance, separate from support: does an eligible claim ANSWER a requirement (direct_answer), only relate to it (relevant_context), or neither.
CREATE TABLE IF NOT EXISTS claim_relevance (
    id INTEGER PRIMARY KEY,
    obligation_assessment_id INTEGER NOT NULL REFERENCES obligation_assessments(id),
    requirement_key TEXT NOT NULL,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    role TEXT NOT NULL CHECK (role IN ('direct_answer', 'relevant_context', 'does_not_answer')),
    reason TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL CHECK (origin IN ('model', 'rule')),
    UNIQUE (obligation_assessment_id, claim_id)
);
CREATE TABLE IF NOT EXISTS report_claim_roles (
    report_claim_id INTEGER PRIMARY KEY REFERENCES report_claims(id),
    role TEXT NOT NULL CHECK (role IN ('direct_answer', 'relevant_context')),
    requirement_keys TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS report_relevance (
    report_id INTEGER PRIMARY KEY REFERENCES reports(id),
    judged INTEGER NOT NULL,
    coverage_round INTEGER
);
CREATE TRIGGER IF NOT EXISTS claim_relevance_valid BEFORE INSERT ON claim_relevance
BEGIN
    SELECT RAISE(ABORT, 'a relevance judgment may only concern a claim of its own run')
    WHERE (SELECT run_id FROM claims WHERE id = NEW.claim_id)
          IS NOT (SELECT c.run_id FROM obligation_assessments o JOIN coverage_assessments c ON c.id = o.assessment_id
                  WHERE o.id = NEW.obligation_assessment_id);
    SELECT RAISE(ABORT, 'the requirement of a relevance judgment must be the requirement of its obligation assessment')
    WHERE NEW.requirement_key IS NOT (SELECT o.requirement_key FROM obligation_assessments oa JOIN coverage_obligations o ON o.id = oa.obligation_id
                                      WHERE oa.id = NEW.obligation_assessment_id);
END;
CREATE TRIGGER IF NOT EXISTS claim_relevance_no_update BEFORE UPDATE ON claim_relevance
BEGIN SELECT RAISE(ABORT, 'coverage assessments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS claim_relevance_no_delete BEFORE DELETE ON claim_relevance
BEGIN SELECT RAISE(ABORT, 'coverage assessments are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_claim_roles_no_update BEFORE UPDATE ON report_claim_roles
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_claim_roles_no_delete BEFORE DELETE ON report_claim_roles
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_relevance_no_update BEFORE UPDATE ON report_relevance
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_relevance_no_delete BEFORE DELETE ON report_relevance
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;

CREATE TRIGGER IF NOT EXISTS obligations_same_run BEFORE INSERT ON coverage_obligations
BEGIN
    SELECT RAISE(ABORT, 'an obligation must belong to a subquestion of its own run')
    WHERE (SELECT run_id FROM subquestions WHERE id = NEW.subquestion_id) IS NOT NEW.run_id;
END;
CREATE TRIGGER IF NOT EXISTS obligation_assessments_same_run BEFORE INSERT ON obligation_assessments
BEGIN
    SELECT RAISE(ABORT, 'an obligation assessment must stay within one run')
    WHERE (SELECT run_id FROM coverage_obligations WHERE id = NEW.obligation_id)
          IS NOT (SELECT run_id FROM coverage_assessments WHERE id = NEW.assessment_id);
END;
CREATE TRIGGER IF NOT EXISTS obligation_support_same_run BEFORE INSERT ON obligation_supporting_claims
BEGIN
    SELECT RAISE(ABORT, 'an obligation assessment may only cite claims of its own run')
    WHERE (SELECT run_id FROM claims WHERE id = NEW.claim_id)
          IS NOT (SELECT c.run_id FROM obligation_assessments o JOIN coverage_assessments c ON c.id = o.assessment_id
                  WHERE o.id = NEW.obligation_assessment_id);
END;
CREATE TRIGGER IF NOT EXISTS obligations_no_update BEFORE UPDATE ON coverage_obligations
BEGIN SELECT RAISE(ABORT, 'coverage obligations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS obligations_no_delete BEFORE DELETE ON coverage_obligations
BEGIN SELECT RAISE(ABORT, 'coverage obligations are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS obligation_assessments_no_update BEFORE UPDATE ON obligation_assessments
BEGIN SELECT RAISE(ABORT, 'coverage assessments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS obligation_assessments_no_delete BEFORE DELETE ON obligation_assessments
BEGIN SELECT RAISE(ABORT, 'coverage assessments are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS obligation_support_no_update BEFORE UPDATE ON obligation_supporting_claims
BEGIN SELECT RAISE(ABORT, 'coverage assessments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS obligation_support_no_delete BEFORE DELETE ON obligation_supporting_claims
BEGIN SELECT RAISE(ABORT, 'coverage assessments are never deleted'); END;

-- A verified claim that coverage recorded as a DIRECT ANSWER to a requirement of ANOTHER subquestion is shown in that subquestion's section too. It stays ONE report
-- claim (same text, same citations, owned by its own section); this row only points to it. Context-only judgments never create one.
CREATE TABLE IF NOT EXISTS report_answer_refs (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    section_id INTEGER NOT NULL REFERENCES report_sections(id),
    report_claim_id INTEGER NOT NULL REFERENCES report_claims(id),
    requirement_keys TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    UNIQUE (report_id, section_id, report_claim_id)
);
CREATE TRIGGER IF NOT EXISTS report_answer_refs_valid BEFORE INSERT ON report_answer_refs
BEGIN
    SELECT RAISE(ABORT, 'the section and the report claim must belong to this report')
    WHERE (SELECT report_id FROM report_sections WHERE id = NEW.section_id) IS NOT NEW.report_id
       OR (SELECT report_id FROM report_claims WHERE id = NEW.report_claim_id) IS NOT NEW.report_id;
    SELECT RAISE(ABORT, 'a reference must point into a section other than the claim''s own')
    WHERE (SELECT section_id FROM report_claims WHERE id = NEW.report_claim_id) IS NEW.section_id;
    SELECT RAISE(ABORT, 'a reference needs a stored direct-answer judgment, in this report''s run and coverage round, for a requirement of that section''s subquestion')
    WHERE NOT EXISTS (
        SELECT 1 FROM claim_relevance cr
        JOIN obligation_assessments oa ON oa.id = cr.obligation_assessment_id
        JOIN coverage_assessments a ON a.id = oa.assessment_id
        JOIN report_claims rc ON rc.id = NEW.report_claim_id
        JOIN reports r ON r.id = NEW.report_id
        JOIN report_sections s ON s.id = NEW.section_id
        WHERE cr.claim_id = rc.claim_id AND cr.role = 'direct_answer'
          AND a.run_id = r.run_id AND a.round IS r.coverage_round AND a.subquestion_id = s.subquestion_id);
END;
CREATE TRIGGER IF NOT EXISTS report_answer_refs_no_update BEFORE UPDATE ON report_answer_refs
BEGIN SELECT RAISE(ABORT, 'report answer references are immutable'); END;
CREATE TRIGGER IF NOT EXISTS report_answer_refs_no_delete BEFORE DELETE ON report_answer_refs
BEGIN SELECT RAISE(ABORT, 'report answer references are never deleted'); END;
-- Cross-claim consistency. A check is the model's (or a skip's) record about ONE exact input, identified by its fingerprint; it says how much was examined (status)
-- and, separately, what was flagged (findings). Both tables and the report snapshots are append-only.
CREATE TABLE IF NOT EXISTS consistency_checks (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    fingerprint TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reviewed', 'partial', 'failed', 'not_checked')),
    cause TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    model TEXT,
    model_call_id INTEGER REFERENCES model_calls(id),
    prompt_version TEXT NOT NULL,
    input_chars INTEGER NOT NULL DEFAULT 0,
    shown_claim_ids TEXT NOT NULL DEFAULT '[]',
    unchecked_claim_ids TEXT NOT NULL DEFAULT '[]',
    claims_reviewed TEXT NOT NULL DEFAULT '[]',
    claim_states TEXT NOT NULL DEFAULT '{}',
    dropped_findings TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consistency_findings (
    id INTEGER PRIMARY KEY,
    check_id INTEGER NOT NULL REFERENCES consistency_checks(id),
    kind TEXT NOT NULL CHECK (kind IN ('conflict', 'scope', 'condition', 'time', 'terminology')),
    explanation TEXT NOT NULL,
    claim_ids TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS report_consistency (
    report_id INTEGER PRIMARY KEY REFERENCES reports(id),
    check_id INTEGER REFERENCES consistency_checks(id),
    status TEXT NOT NULL CHECK (status IN ('reviewed', 'partial', 'failed', 'not_checked', 'not_applicable')),
    cause TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    shown_claim_ids TEXT NOT NULL DEFAULT '[]',
    unchecked_claim_ids TEXT NOT NULL DEFAULT '[]',
    claim_states TEXT NOT NULL DEFAULT '{}',
    dropped_findings TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS report_consistency_findings (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    kind TEXT NOT NULL,
    explanation TEXT NOT NULL,
    claim_ids TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',
    carried_from_check INTEGER REFERENCES consistency_checks(id)
);
CREATE INDEX IF NOT EXISTS idx_consistency_run ON consistency_checks(run_id, fingerprint, id);
CREATE TRIGGER IF NOT EXISTS consistency_checks_no_update BEFORE UPDATE ON consistency_checks
BEGIN SELECT RAISE(ABORT, 'consistency checks are immutable history'); END;
CREATE TRIGGER IF NOT EXISTS consistency_checks_no_delete BEFORE DELETE ON consistency_checks
BEGIN SELECT RAISE(ABORT, 'consistency checks are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS consistency_findings_no_update BEFORE UPDATE ON consistency_findings
BEGIN SELECT RAISE(ABORT, 'consistency findings are immutable history'); END;
CREATE TRIGGER IF NOT EXISTS consistency_findings_no_delete BEFORE DELETE ON consistency_findings
BEGIN SELECT RAISE(ABORT, 'consistency findings are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_consistency_no_update BEFORE UPDATE ON report_consistency
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_consistency_no_delete BEFORE DELETE ON report_consistency
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS report_consistency_findings_no_update BEFORE UPDATE ON report_consistency_findings
BEGIN SELECT RAISE(ABORT, 'reports are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS report_consistency_findings_no_delete BEFORE DELETE ON report_consistency_findings
BEGIN SELECT RAISE(ABORT, 'reports are never deleted'); END;
"""
