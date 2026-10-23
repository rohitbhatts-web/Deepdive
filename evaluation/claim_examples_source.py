"""PUBLIC EXPORT (public-export-v1): quotes taken from the four rights-reserved sources, and the claim text of CE04 and CE10, are replaced by WITHHELD. This module is the authoring
source of the full private dataset and is kept for its claims, rules and rationales; it cannot rebuild the data without the private evidence.

Authoring source for the claim-evidence dataset (evaluation/data/claim_evidence.json).

Every example pairs ONE claim with the exact stored passage(s) it cites. The evidence is real stored evidence (see build_claim_dataset.py);
the CLAIMS were written for this dataset, some as faithful restatements and some as deliberate alterations (flagged `synthetic`).

`proposed_label` and `rationale` are PROVISIONAL, suggested by the model that wrote this file. They are not ground truth. Authoritative
labels exist only in data/human_labels.json, entered by a human reviewer; nothing in the harness treats a proposed label as correct.
Examples flagged `ambiguity` are ones where a reasonable reviewer could choose a neighbouring label.
"""

# source key -> (stored database file, source id). Passage ids are S<source>-P<ordinal> inside that database.
SOURCES = {
    "wikipedia_sqlite": ("phase1-live.db", 1),
    "postgresql_docs": ("live-corrective.db", 4),
    "datacamp": ("live-corrective.db", 5),
    "tinybird": ("live-corrective.db", 2),
    "devto": ("live-corrective.db", 1),
    "logz": ("live-corrective.db", 3),
}

HELD_OUT = {"CE05", "CE09", "CE14", "CE17", "CE19", "CE22", "CE27", "CE29", "CE33", "CE38"}

Q_ARCH = "How does each engine handle concurrency and transactions?"
Q_FACTS = "What are verifiable facts about SQLite (versions, dates, limits, licence)?"
Q_PG = "What does the PostgreSQL manual say about transaction isolation and concurrency?"
Q_REC = "When is each engine recommended, and by whom?"


WITHHELD = "[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]"


def ev(source, ordinal, quote):
    return {"source": source, "ordinal": ordinal, "quote": quote}


def ex(label, claim, evidence, *, subquestion, tags, rationale, synthetic=None, ambiguity=None, rule=None):
    return {"proposed_label": label, "claim": claim, "evidence": evidence, "subquestion": subquestion, "tags": tags,
            "rationale": rationale, "synthetic": synthetic, "ambiguity": ambiguity, "rule": rule}


# Label rules, written down so that every ambiguous case is decided by a stated rule and not case by case. They follow the verifier's own
# definitions (backend/agents/verifier.py: SYSTEM) where those exist; the one the verifier leaves open is precedence when a claim has both
# supported and contradicted parts (R3/R4). Since rubric v2 an explicit contradiction wins (see RUBRIC_HISTORY). All labels below stay
# PROVISIONAL until a human reviews them.
RUBRIC_VERSION = "claim-labels-v2"
LABEL_RULES = [
    {"id": "R1", "title": "Evidence boundary",
     "text": "Judge ONLY the cited text exactly as shown on the review sheet (that is what the verifier receives, cut at 1,500 characters, which cuts none "
             "of these). Ignore other parts of the page, other passages and what you know. A frozen example has no neighbouring-passage context; the "
             "production pipeline adds neighbours."},
    {"id": "R2", "title": "Decompose the claim",
     "text": "List every assertion: entities, numbers, dates, scope words (always, only, all, at most), hedges and attribution (who said it)."},
    {"id": "R3", "title": "contradicted",
     "text": "The shown text explicitly states something incompatible with an assertion of the claim (a different number or date, the opposite status, a "
             "negation, a different named entity). An explicit evidential contradiction takes precedence over partial support: if any assertion is "
             "explicitly contradicted, label contradicted even when another assertion is established. Something merely implied or merely not "
             "mentioned is never a contradiction."},
    {"id": "R4", "title": "Precedence when parts disagree",
     "text": "First apply R3: an explicitly contradicted assertion makes the claim contradicted. Only when no assertion is explicitly contradicted: if at "
             "least one assertion is established AND at least one other is missing, stronger, broader or more certain than the text, label "
             "partially_supported. partially_supported therefore means support that is incomplete or overstated WITHOUT explicit contradiction. "
             "Supported never wins when any part fails."},
    {"id": "R5", "title": "Weaker core versus stronger claim",
     "text": "When the text establishes the claim's central fact but in a weaker form (a suggestion stated as a rule, a list entry stated as the largest, a "
             "hedge dropped), label partially_supported."},
    {"id": "R6", "title": "unsupported",
     "text": "The text does not establish the claim's central fact: off-topic, a genuine quote that says nothing about it, a typical use case restated as a "
             "limit, a plan or forecast restated as something that happened, a different assertion on the same topic."},
    {"id": "R7", "title": "Attribution",
     "text": "A claim that attributes a statement or recommendation to its source ('Tinybird says ...') is judged on whether the source says it. A claim that "
             "states a source's opinion, ranking or forecast as plain fact is not fully supported."},
]


# The rubric the labels are judged against is versioned. A rule change adds an entry here; the earlier text is kept word for word. Human labels
# record the rubric_version they were made under (evaluation/datasets.py), so a label made under v1 stays identifiable after v2.
RUBRIC_HISTORY = [
    {"version": "claim-labels-v1", "date": "2026-10-21", "status": "superseded by claim-labels-v2",
     "changed_rules": {
         "R3": ("The shown text states something incompatible with the claim (a different number or date, the opposite status, a negation, a different named "
                "entity) AND no assertion of the claim is established. A single-assertion claim whose assertion is explicitly opposed is contradicted. "
                "Something merely implied or merely not mentioned is never a contradiction."),
         "R4": ("If at least one assertion is established AND at least one other is missing, stronger, broader, more certain or explicitly contradicted, label "
                "partially_supported. A contradicted part does NOT make the whole claim contradicted when another part is supported (this follows the "
                "verifier's rule for bundled claims). Supported never wins when any part fails.")}},
    {"version": "claim-labels-v2", "date": "2026-10-21", "status": "current",
     "change": ("An explicit evidential contradiction takes precedence over partial support. partially_supported applies when support is incomplete or "
                "overstated without explicit contradiction. R3 and R4 were rewritten; R1, R2, R5, R6 and R7 are unchanged."),
     "reason": ("v1 R4 said a contradicted part next to a supported part is partially_supported, while the reviewer's decision for CE16 (one figure matches, one "
                "is explicitly contradicted) was contradicted. The two could not both be applied, so the rule was reconciled before any label was imported."),
     "verifier_note": ("When v2 was adopted the product verifier's bundled-claim sentence ('any that the evidence does not establish makes the claim "
                       "partially_supported') conflicted with it. It was aligned afterwards as verifier rubric attribution-v2 (backend/claims.py RUBRIC_HISTORY): an "
                       "explicit contradiction of any asserted part takes precedence. Verdicts made earlier keep their stamp attribution-v1 and are not rewritten. "
                       "A scripted test checks the instruction text and output schema, not model judgment."),
     "labels_affected": "No human label existed when v2 was adopted (nothing had been imported), so no confirmed label changed under v2.",
     "held_out_note": ("Held-out CE19 has the same shape (supported part plus an explicitly contradicted part). Its provisional label and its v1 annotation are "
                       "deliberately left as written; it is reviewed with the other held-out examples, not before.")},
]

# Provisional DEVELOPMENT labels whose proposed label depends on the superseded v1 R4 and so need a human re-review under v2. Only a flag: the
# proposed label is not edited here and no confirmed label is touched. Held-out examples are never listed (their text is not modified).
RUBRIC_FLAGS = {
    "CE13": {"rubric": "claim-labels-v2", "proposed_under_v1": "partially_supported", "v2_would_give": "contradicted",
             "why": "Public domain is supported; 'accepts contributions from anyone' is explicitly opposed by the text (the project is not open-contribution). "
                    "Under v2 R3 the explicit contradiction takes precedence.",
             "status": "needs_human_review"},
    "CE16": {"rubric": "claim-labels-v2", "proposed_under_v1": "partially_supported", "v2_would_give": "contradicted",
             "why": "The 156,000 figure matches; the test-code figure (920 million vs 92 million) is explicitly contradicted. Matches the reviewer's stated "
                    "decision (contradicted); the provisional label still shows the v1 result.",
             "status": "needs_human_review"},
}


EXAMPLES = [
    # ---- proposed: supported ------------------------------------------------------------------------------------------------------
    ex("supported", "SQLite releases since version 3.6.17 (10 August 2009) have 100% branch test coverage.",
       [ev("wikipedia_sqlite", 16, "Starting with the August 10, 2009 release of SQLite 3.6.17, SQLite releases have 100% branch test coverage")],
       subquestion=Q_FACTS, tags=["date_qualified"], rationale="The quote states the date, the version and the coverage figure; the claim repeats exactly those."),
    ex("supported", "SQLite performs write operations one after another because of its serverless design.",
       [ev("wikipedia_sqlite", 9, "due to the serverless design, writes can only be performed sequentially")],
       subquestion=Q_ARCH, tags=[], rationale="The passage states writes are sequential and ties that to the serverless design."),
    ex("supported", "Read Committed is PostgreSQL's default transaction isolation level.",
       [ev("postgresql_docs", 5, "Read Committed is the default isolation level in PostgreSQL.")],
       subquestion=Q_PG, tags=[], rationale="Direct statement in the manual."),
    ex("supported", WITHHELD,
       [ev("tinybird", 12, WITHHELD)],
       subquestion=Q_ARCH, tags=["attribution_correct"], rationale="The claim attributes the statement to Tinybird, which is the author of the passage."),
    ex("supported", "The maximum supported size of an SQLite database file is 281 terabytes.",
       [ev("wikipedia_sqlite", 14, "The maximum supported size for an SQLite database file is 281 terabytes.")],
       subquestion=Q_FACTS, tags=["number"], rationale="Same figure and unit as the passage."),
    ex("supported", "DataCamp describes PostgreSQL as operating on a client-server model that needs a separate server process.",
       [ev("datacamp", 11, WITHHELD)],
       subquestion=Q_ARCH, tags=["attribution_correct"], rationale="Restates the passage and names its source."),
    ex("supported", "PostgreSQL's predicate locks, used for Serializable transactions, do not cause any blocking.",
       [ev("postgresql_docs", 21, "In PostgreSQL these locks do not cause any blocking and therefore can not play any part in causing a deadlock.")],
       subquestion=Q_PG, tags=[], rationale="The passage says exactly this about the predicate locks it just introduced."),
    ex("supported", "Wikipedia's infobox for SQLite lists 2004-06-18 as its initial release.",
       [ev("wikipedia_sqlite", 1, "Initial release\n\n2004-06-18")],
       subquestion=Q_FACTS, tags=["date_qualified", "conflicting_within_source"],
       rationale="The shown text lists 2004-06-18 as the initial release and the claim only reports what the infobox lists.",
       rule="R1", ambiguity="Elsewhere on the same Wikipedia page (a passage the verifier does not receive for this claim) version 1.0 is dated August 2000. Under R1 that is outside the evidence; a reviewer who feels the claim is misleading anyway should say so in the note."),
    ex("supported", "A DEV Community post says Postgres uses MVCC, so readers do not block writers and vice versa.",
       [ev("devto", 2, WITHHELD)],
       subquestion=Q_ARCH, tags=["attribution_correct"], rationale="Restates the post's sentence and attributes it to the post."),
    ex("supported", WITHHELD,
       [ev("datacamp", 5, WITHHELD)],
       subquestion=Q_ARCH, tags=["attribution_correct", "source_claim_not_checked"],
       rationale="The claim reports DataCamp's statement as DataCamp's, which the passage supports. Whether the statement is historically true is outside the evidence and is not being judged."),

    # ---- proposed: partially_supported ---------------------------------------------------------------------------------------------
    ex("partially_supported", "SQLite allows concurrent reads and writes with write-ahead logging, and its database files can be at most 281 terabytes.",
       [ev("wikipedia_sqlite", 9, "relaxed in version 3.7 as write-ahead logging (WAL) enables concurrent reads and writes")],
       subquestion=Q_ARCH, tags=["compound"], rationale="The WAL half is supported by the cited passage; the 281 TB half is not in that passage."),
    ex("partially_supported", "Read Committed is PostgreSQL's default isolation level and it prevents phantom reads.",
       [ev("postgresql_docs", 5, "Read Committed is the default isolation level in PostgreSQL.")],
       subquestion=Q_PG, tags=["compound"], rationale="The default-level half is supported. Nothing cited says Read Committed prevents phantom reads, and the passage notes successive SELECTs can see different data.",
       rule="R4", ambiguity="The shown text says two successive SELECTs can see different data, which implies phantom reads are possible but does not say so. R3 needs an explicit statement, so the second half is not established rather than contradicted, and R4 gives partially_supported."),
    ex("partially_supported", "SQLite is in the public domain and accepts contributions from anyone.",
       [ev("wikipedia_sqlite", 15, "SQLite is public domain, but not \"open-contribution\"")],
       subquestion=Q_FACTS, tags=["compound"], rationale="Public-domain half supported; the passage says the project is NOT open-contribution, which contradicts the second half.",
       rule="R4"),
    ex("partially_supported", "Tinybird says SQLite's single-writer constraint becomes a bottleneck for concurrent writes and that SQLite cannot be used in production.",
       [ev("tinybird", 21, WITHHELD)],
       subquestion=Q_REC, tags=["compound", "attribution_correct"], rationale="The bottleneck half is supported; the 'cannot be used in production' half is not stated in the cited passage."),
    ex("partially_supported", "The PostgreSQL manual says raising max_pred_locks_per_transaction avoids extra serialization failures when the predicate lock table is short of memory, and that applications should always use index scans.",
       [ev("postgresql_docs", 26, "You can avoid this by increasing max_pred_locks_per_transaction, max_pred_locks_per_relation, and/or max_pred_locks_per_page.")],
       subquestion=Q_PG, tags=["compound", "recommendation_overstated"], rationale="First half supported. The manual says it 'may be helpful to encourage the use of index scans', not that applications should always use them."),
    ex("partially_supported", "The SQLite library has 156,000 lines of source code, and its test suites add up to 920 million lines of test code.",
       [ev("wikipedia_sqlite", 16, "The SQLite library has 156,000 lines of source code, while all the test suites combined add up to 92 million lines of test code.")],
       subquestion=Q_FACTS, tags=["compound", "number"], rationale="The source-code figure matches; the test-code figure is altered (92 million became 920 million).",
       synthetic={"kind": "changed_quantity", "detail": "92 million lines of test code was changed to 920 million in the second half of a two-part claim."}),
    ex("partially_supported", "Postgres is designed for scale, handling terabytes of data and thousands of concurrent connections, and therefore always outperforms SQLite.",
       [ev("devto", 4, WITHHELD)],
       subquestion=Q_ARCH, tags=["compound", "overgeneralization"], rationale="The scale statements are supported; 'always outperforms SQLite' is not in the passage."),
    ex("partially_supported", "Logz.io lists Facebook, Google and Apple as key SQLite customers and says SQLite's paid professional support is provided by Oracle.",
       [ev("logz", 4, WITHHELD)],
       subquestion=Q_FACTS, tags=["compound", "attribution_correct"], rationale="Customer list supported; the passage says paid professional support is offered but does not name Oracle.",
       synthetic={"kind": "added_unsupported_detail", "detail": "'provided by Oracle' was added to a true statement; the passage names no provider."}),
    ex("partially_supported", "SQLite is zero-configuration, so it has no access control of any kind.",
       [ev("wikipedia_sqlite", 8, "Access control is handled through the file-system permissions of the database file.")],
       subquestion=Q_ARCH, tags=["compound", "negation_added"], rationale="Zero-configuration is supported by the passage; 'no access control of any kind' is contradicted by file-system-permission access control.",
       synthetic={"kind": "added_unsupported_detail", "detail": "'so it has no access control of any kind' was appended to a true statement."},
       rule="R4", ambiguity="One part is supported (zero-configuration) and one is explicitly contradicted (file-system permissions are access control). R4: partially_supported, not contradicted."),
    ex("partially_supported", "SQLite 3.50.0, released in May 2025, introduced new Unicode functions and a built-in vector search extension.",
       [ev("wikipedia_sqlite", 14, "SQLite 3.50.0 introduced additional features, including new Unicode functions (unistr() and unistr_quote())")],
       subquestion=Q_FACTS, tags=["compound", "date_qualified"], rationale="Release month and Unicode functions are supported (the month is in the same passage); the vector search extension is not mentioned.",
       synthetic={"kind": "added_unsupported_detail", "detail": "'a built-in vector search extension' was appended to a true statement."}),

    # ---- proposed: unsupported -----------------------------------------------------------------------------------------------------
    ex("unsupported", "PostgreSQL supports native declarative table partitioning.",
       [ev("postgresql_docs", 5, "Read Committed is the default isolation level in PostgreSQL.")],
       subquestion=Q_PG, tags=["genuine_but_irrelevant_quote"], rationale="The quote is genuine and about PostgreSQL but says nothing about partitioning."),
    ex("unsupported", "PostgreSQL's maximum database size is 32 terabytes.",
       [ev("postgresql_docs", 3, "internally only three distinct isolation levels are implemented")],
       subquestion=Q_PG, tags=["genuine_but_irrelevant_quote", "number"], rationale="A genuine quote about isolation levels; no size limit appears in the cited text."),
    ex("unsupported", "SQLite added a NoSQL interface in 2011.",
       [ev("wikipedia_sqlite", 6, "In 2011, Hipp announced his plans to add a NoSQL interface to SQLite")],
       subquestion=Q_FACTS, tags=["forecast_to_fact", "date_qualified"], rationale="The passage reports an announced PLAN in 2011; the claim turns it into a completed event.",
       synthetic={"kind": "forecast_to_fact", "detail": "'announced his plans to add' was restated as 'added'."},
       rule="R6"),
    ex("unsupported", "SQLite is used today in the damage-control systems of US Navy destroyers.",
       [ev("wikipedia_sqlite", 5, "Hipp was designing software used for a damage-control system aboard guided-missile destroyers")],
       subquestion=Q_FACTS, tags=["overreach"], rationale="The passage describes the context in which SQLite was designed in 2000, not current use by the Navy.",
       rule="R3", ambiguity="The text is about the original contract and says nothing about current use. R3 needs an explicit opposite, so this is unsupported, not contradicted."),
    ex("unsupported", "The PostgreSQL manual recommends Repeatable Read over Serializable for new applications.",
       [ev("postgresql_docs", 17, "To retain the legacy Serializable behavior, Repeatable Read should now be requested.")],
       subquestion=Q_REC, tags=["attribution_overreach"], rationale="The manual says how to keep the legacy behaviour; it does not recommend one level over another for new applications."),
    ex("unsupported", "The SQLite library is under 300 KB in every configuration.",
       [ev("logz", 2, WITHHELD)],
       subquestion=Q_FACTS, tags=["changed_quantity", "number"], rationale="The source gives a figure under 600 KB (wording withheld); it gives no 300 KB figure and no 'every configuration' claim.",
       synthetic={"kind": "changed_quantity", "detail": "600 KB with a 'can easily be' qualification became 300 KB in every configuration."}),
    ex("partially_supported", "Facebook is SQLite's largest customer.",
       [ev("logz", 4, WITHHELD)],
       subquestion=Q_FACTS, tags=["ranking_added"], rationale="The text establishes that Facebook is one of three key customers; 'largest' is a ranking it does not give.",
       rule="R5", ambiguity="Revised from unsupported before any human review: the central fact (Facebook is a key customer) is established, only the ranking is not (R5)."),
    ex("unsupported", "SQLite can handle at most 100,000 requests a day.",
       [ev("logz", 2, WITHHELD)],
       subquestion=Q_ARCH, tags=["qualification_dropped", "number"], rationale="The passage gives ~100K requests a day as a typical use case for low-to-medium traffic sites, not as a maximum.",
       synthetic={"kind": "dropped_qualification", "detail": "'~', 'low-to-medium traffic' and 'use case' were replaced by 'at most'."}),
    ex("unsupported", "SQLite supports full-text search through the FTS5 extension.",
       [ev("wikipedia_sqlite", 16, "SQLite uses automated regression testing prior to each release.")],
       subquestion=Q_FACTS, tags=["genuine_but_irrelevant_quote", "true_but_unsupported"],
       rationale="A genuine quote about testing. The claim happens to be commonly true, and appears elsewhere on the same page, but the CITED text does not support it; the verifier judges the cited evidence only."),
    ex("partially_supported", "Tinybird says teams should always run Postgres locally through Docker.",
       [ev("tinybird", 18, WITHHELD)],
       subquestion=Q_REC, tags=["recommendation_overstated", "attribution_correct"],
       rationale="Tinybird does suggest running Postgres locally via Docker (the central fact); it offers it as 'a useful middle ground', not as something teams should always do.",
       rule="R5", ambiguity="Revised from unsupported before any human review: the suggestion itself is established, only 'always' is not (R5)."),

    # ---- proposed: contradicted ---------------------------------------------------------------------------------------------------
    ex("contradicted", "DataCamp names SQLite as the winner for ACID compliance.",
       [ev("datacamp", 11, WITHHELD)],
       subquestion=Q_ARCH, tags=["swapped_entity", "attribution_correct"], rationale="The same passage names PostgreSQL as the winner for ACID compliance.",
       synthetic={"kind": "swapped_entity", "detail": "PostgreSQL was replaced by SQLite as the winner."}),
    ex("contradicted", "PostgreSQL's Serializable isolation level adds extra blocking on top of Repeatable Read.",
       [ev("postgresql_docs", 18, "This monitoring does not introduce any blocking beyond that present in repeatable read")],
       subquestion=Q_PG, tags=["negation"], rationale="The manual says the monitoring does NOT introduce blocking beyond Repeatable Read.",
       synthetic={"kind": "negation", "detail": "The manual's negated statement was asserted positively."}),
    ex("contradicted", "SQLite has built-in streaming replication.",
       [ev("devto", 4, WITHHELD)],
       subquestion=Q_ARCH, tags=["negation"], rationale="The comparison table lists replication for SQLite as 'None built-in'.",
       synthetic={"kind": "negation", "detail": "'None built-in' was asserted as 'has built-in'."}),
    ex("contradicted", "PostgreSQL is released under the GPL.",
       [ev("datacamp", 11, WITHHELD)],
       subquestion=Q_FACTS, tags=["swapped_entity"], rationale="The passage names the PostgreSQL License, not the GPL.",
       synthetic={"kind": "swapped_entity", "detail": "'the PostgreSQL License' was replaced by 'the GPL'."}),
    ex("contradicted", "Tinybird states that Postgres is the best database for OLAP.",
       [ev("tinybird", 21, WITHHELD)],
       subquestion=Q_REC, tags=["negation", "attribution_correct"], rationale="Tinybird says the opposite.",
       synthetic={"kind": "negation", "detail": "'is not Postgres' was asserted as 'is Postgres'."}),
    ex("contradicted", "SQLite lets several processes write to the same database file at the same time.",
       [ev("wikipedia_sqlite", 9, "due to the serverless design, writes can only be performed sequentially")],
       subquestion=Q_ARCH, tags=["negation"], rationale="Writes are sequential per the passage.",
       synthetic={"kind": "negation", "detail": "'writes can only be performed sequentially' was restated as writers being able to write at the same time."},
       rule="R3", ambiguity="The shown passage also says WAL 'enables concurrent reads and writes', which is reads alongside a writer, not several writers. The claim is about several writers writing at once, which the text explicitly rules out ('writes can only be performed sequentially'): R3, contradicted."),
    ex("contradicted", "PostgreSQL internally implements four distinct transaction isolation levels.",
       [ev("postgresql_docs", 3, "internally only three distinct isolation levels are implemented")],
       subquestion=Q_PG, tags=["changed_quantity", "number"], rationale="The manual says only three are implemented internally even though four can be requested.",
       synthetic={"kind": "changed_quantity", "detail": "three was changed to four."}),
    ex("contradicted", "SQLite 3.0 was released in June 2001.",
       [ev("wikipedia_sqlite", 6, "In June 2004, SQLite 3.0 added internationalization, manifest typing, and other major improvements")],
       subquestion=Q_FACTS, tags=["changed_date", "date_qualified"], rationale="The passage dates SQLite 3.0 to June 2004.",
       synthetic={"kind": "changed_date", "detail": "June 2004 was changed to June 2001."}),
    ex("contradicted", "SQLite releases have had 100% branch test coverage since the 2019 release of version 3.6.17.",
       [ev("wikipedia_sqlite", 16, "Starting with the August 10, 2009 release of SQLite 3.6.17, SQLite releases have 100% branch test coverage")],
       subquestion=Q_FACTS, tags=["changed_date", "date_qualified"], rationale="The passage gives 10 August 2009, not 2019.",
       synthetic={"kind": "changed_date", "detail": "2009 was changed to 2019."}),
    ex("contradicted", "The SQLite library has 156 million lines of source code.",
       [ev("wikipedia_sqlite", 16, "The SQLite library has 156,000 lines of source code")],
       subquestion=Q_FACTS, tags=["changed_quantity", "number"], rationale="The passage says 156,000 lines.",
       synthetic={"kind": "changed_quantity", "detail": "156,000 was changed to 156 million."}),
]


# Every provisional label change made after the first version of this file and before any human review. Provisional labels are only
# suggestions; recording the change keeps the history honest. No evaluation has been run against these examples.
REVIEW_LOG = [
    {"date": "2026-10-21", "example_line": "Facebook is SQLite's largest customer.", "from": "unsupported", "to": "partially_supported", "rule": "R5",
     "reason": "Re-reviewed on the exact text the verifier receives: the central fact (a key customer) is established, only the ranking is not. This example is in the held-out split; the change is to a provisional label, made before any run or human review."},
    {"date": "2026-10-21", "example_line": "Tinybird says teams should always run Postgres locally through Docker.", "from": "unsupported", "to": "partially_supported", "rule": "R5",
     "reason": "Same re-review: the suggestion is established, 'always' is not."},
    {"date": "2026-10-21", "example_line": "(CE08, CE12, CE19, CE24, CE36)", "from": "ambiguity notes", "to": "ambiguity notes plus the deciding rule", "rule": "R1/R3/R4",
     "reason": "Labels unchanged. Each ambiguity is now decided by a stated rule, using the exact verifier-visible text, and the remaining reason for hesitation is kept."},
]
