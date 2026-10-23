"""Phase 3 tables: claims, evidence references, verification results.

Design rules, enforced in SQLite itself so a bug in application code cannot break them:
- A claim row is one immutable *version* of a claim: its text is never edited, only superseded by a new row
  (`parent_claim_id`, `version`). Claims, evidence and verification results can be neither updated nor deleted, so
  rejected claims are always preserved.
- A claim, its subquestion and any evidence passage it resolves to must belong to the same run.
- Verdicts attach to a claim *version* (claim_id), so a verdict can never be reused for a different text.
- `verification_result_passages` records which stored passages a semantic verdict actually relied on: the claim's own
  cited passages ('claim_evidence') and any neighbouring passage the verifier needed ('neighbour_context'). A report
  citation must resolve to these, not to whatever the drafter happened to cite.
- `claim_evidence.passage_ref` is what the drafter wrote (it may be fabricated); `passage_id` is set only when that
  reference resolves to a stored passage of the SAME run, and `resolution` records why it did or did not.
"""

CLAIMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    subquestion_id INTEGER NOT NULL REFERENCES subquestions(id),
    parent_claim_id INTEGER REFERENCES claims(id),
    version INTEGER NOT NULL DEFAULT 1,
    text TEXT NOT NULL,
    origin TEXT NOT NULL,
    drafter_model TEXT,
    draft_call_id INTEGER REFERENCES model_calls(id),
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_evidence (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    position INTEGER NOT NULL,
    passage_ref TEXT NOT NULL,
    passage_id TEXT REFERENCES passages(id),
    resolution TEXT NOT NULL,
    quote TEXT NOT NULL,
    UNIQUE (claim_id, position)
);
CREATE TABLE IF NOT EXISTS verification_results (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    stage TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reason TEXT NOT NULL,
    model TEXT,
    model_call_id INTEGER REFERENCES model_calls(id),
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    CHECK ((stage = 'integrity' AND verdict IN ('passed', 'failed'))
        OR (stage = 'semantic' AND verdict IN ('supported', 'partially_supported', 'unsupported', 'contradicted', 'error')))
);
CREATE TABLE IF NOT EXISTS verification_result_evidence (
    result_id INTEGER NOT NULL REFERENCES verification_results(id),
    evidence_id INTEGER NOT NULL REFERENCES claim_evidence(id),
    PRIMARY KEY (result_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS verification_result_passages (
    result_id INTEGER NOT NULL REFERENCES verification_results(id),
    passage_id TEXT NOT NULL REFERENCES passages(id),
    role TEXT NOT NULL CHECK (role IN ('claim_evidence', 'neighbour_context')),
    PRIMARY KEY (result_id, passage_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_run ON claims(run_id, id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id, position);
CREATE INDEX IF NOT EXISTS idx_verification_claim ON verification_results(claim_id, stage, id);

CREATE TRIGGER IF NOT EXISTS claims_same_run BEFORE INSERT ON claims
BEGIN
    SELECT RAISE(ABORT, 'claim and subquestion must belong to the same run')
    WHERE (SELECT run_id FROM subquestions WHERE id = NEW.subquestion_id) IS NOT NEW.run_id;
    SELECT RAISE(ABORT, 'claim and its parent version must belong to the same run')
    WHERE NEW.parent_claim_id IS NOT NULL
      AND (SELECT run_id FROM claims WHERE id = NEW.parent_claim_id) IS NOT NEW.run_id;
END;
CREATE TRIGGER IF NOT EXISTS claim_evidence_same_run BEFORE INSERT ON claim_evidence
WHEN NEW.passage_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'evidence passage must belong to the claim''s run')
    WHERE (SELECT s.run_id FROM passages p JOIN sources s ON s.id = p.source_id WHERE p.id = NEW.passage_id)
          IS NOT (SELECT run_id FROM claims WHERE id = NEW.claim_id);
END;
CREATE TRIGGER IF NOT EXISTS result_evidence_same_claim BEFORE INSERT ON verification_result_evidence
BEGIN
    SELECT RAISE(ABORT, 'a verification result may only cite its own claim''s evidence')
    WHERE (SELECT claim_id FROM claim_evidence WHERE id = NEW.evidence_id)
          IS NOT (SELECT claim_id FROM verification_results WHERE id = NEW.result_id);
END;

CREATE TRIGGER IF NOT EXISTS result_passages_same_run BEFORE INSERT ON verification_result_passages
BEGIN
    SELECT RAISE(ABORT, 'a verification result may only use passages of its own run')
    WHERE (SELECT s.run_id FROM passages p JOIN sources s ON s.id = p.source_id WHERE p.id = NEW.passage_id)
          IS NOT (SELECT c.run_id FROM verification_results r JOIN claims c ON c.id = r.claim_id WHERE r.id = NEW.result_id);
END;
CREATE TRIGGER IF NOT EXISTS result_passages_no_update BEFORE UPDATE ON verification_result_passages
BEGIN SELECT RAISE(ABORT, 'verification results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS result_passages_no_delete BEFORE DELETE ON verification_result_passages
BEGIN SELECT RAISE(ABORT, 'verification results are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS claims_no_update BEFORE UPDATE ON claims
BEGIN SELECT RAISE(ABORT, 'claims are immutable; create a new version instead'); END;
CREATE TRIGGER IF NOT EXISTS claims_no_delete BEFORE DELETE ON claims
BEGIN SELECT RAISE(ABORT, 'claims are never deleted (rejected claims are kept)'); END;
CREATE TRIGGER IF NOT EXISTS claim_evidence_no_update BEFORE UPDATE ON claim_evidence
BEGIN SELECT RAISE(ABORT, 'claim evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS claim_evidence_no_delete BEFORE DELETE ON claim_evidence
BEGIN SELECT RAISE(ABORT, 'claim evidence is never deleted'); END;
CREATE TRIGGER IF NOT EXISTS verification_results_no_update BEFORE UPDATE ON verification_results
BEGIN SELECT RAISE(ABORT, 'verification results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS verification_results_no_delete BEFORE DELETE ON verification_results
BEGIN SELECT RAISE(ABORT, 'verification results are never deleted'); END;
"""
