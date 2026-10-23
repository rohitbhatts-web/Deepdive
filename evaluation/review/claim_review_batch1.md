# Claim-evidence label review: 12 examples

> PUBLIC EXPORT (public-export-v1): the quote and evidence text of CE30 and CE31 are withheld (rights reserved). This sheet is a record of the original review; those two examples cannot be re-reviewed from it.

Label rubric version: **claim-labels-v2**. Label provenance when imported: AI-assisted human review; not independent or blinded.

Labels (judge the claim ONLY against the evidence text shown below each claim, which is exactly what the verifier receives):
- supported: every assertion, including numbers, dates, qualifiers and attribution, is established by the evidence.
- partially_supported: some assertions are established and at least one is missing, stronger, broader or more certain, WITHOUT explicit contradiction.
- unsupported: the evidence does not establish the claim's central fact.
- contradicted: the evidence explicitly states something incompatible with any assertion of the claim. An explicit contradiction takes precedence over
  partial support, even when another part of the claim is established.

## Rules that decide ambiguous cases

- **R1 Evidence boundary.** Judge ONLY the cited text exactly as shown on the review sheet (that is what the verifier receives, cut at 1,500 characters, which cuts none of these). Ignore other parts of the page, other passages and what you know. A frozen example has no neighbouring-passage context; the production pipeline adds neighbours.
- **R2 Decompose the claim.** List every assertion: entities, numbers, dates, scope words (always, only, all, at most), hedges and attribution (who said it).
- **R3 contradicted.** The shown text explicitly states something incompatible with an assertion of the claim (a different number or date, the opposite status, a negation, a different named entity). An explicit evidential contradiction takes precedence over partial support: if any assertion is explicitly contradicted, label contradicted even when another assertion is established. Something merely implied or merely not mentioned is never a contradiction.
- **R4 Precedence when parts disagree.** First apply R3: an explicitly contradicted assertion makes the claim contradicted. Only when no assertion is explicitly contradicted: if at least one assertion is established AND at least one other is missing, stronger, broader or more certain than the text, label partially_supported. partially_supported therefore means support that is incomplete or overstated WITHOUT explicit contradiction. Supported never wins when any part fails.
- **R5 Weaker core versus stronger claim.** When the text establishes the claim's central fact but in a weaker form (a suggestion stated as a rule, a list entry stated as the largest, a hedge dropped), label partially_supported.
- **R6 unsupported.** The text does not establish the claim's central fact: off-topic, a genuine quote that says nothing about it, a typical use case restated as a limit, a plan or forecast restated as something that happened, a different assertion on the same topic.
- **R7 Attribution.** A claim that attributes a statement or recommendation to its source ('Tinybird says ...') is judged on whether the source says it. A claim that states a source's opinion, ranking or forecast as plain fact is not fully supported.

Fill the fields under each example. `Proposed` is a PROVISIONAL suggestion from a language model: it is not the answer, it may be wrong, and nothing in it is a label until you write one. Leave `human label` empty to skip. Frozen examples show no neighbouring passages; the production verifier would also receive neighbours.

---

## CE01  (dev)

**Claim:** SQLite releases since version 3.6.17 (10 August 2009) have 100% branch test coverage.

**Source** - SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

> Cited quote: "Starting with the August 10, 2009 release of SQLite 3.6.17, SQLite releases have 100% branch test coverage"

Evidence text the verifier receives  (complete: nothing is cut):

```text
SQLite uses automated regression testing prior to each release. Over 2 million tests are run as part of a release's verification. The SQLite library has 156,000 lines of source code, while all the test suites combined add up to 92 million lines of test code. SQLite's tests simulate a number of exceptional scenarios, such as power loss and I/O errors, in addition to testing the library's functionality. Starting with the August 10, 2009 release of SQLite 3.6.17, SQLite releases have 100% branch test coverage, one of the components of code coverage. SQLite has four different test harnesses: the original public-domain TCL tests, the proprietary C-language TH3 test suite, the SQL Logic Tests, which check SQLite against other SQL databases, and the dbsqlfuzz proprietary fuzzing engine.[46]

Notable uses

[edit]

Operating systems

[edit]

SQLite is included by default in:

Android[14][47]

BlackBerry Tablet OS[48]

iOS[13]
```

**Proposed (PROVISIONAL, not a label):** `supported` - The quote states the date, the version and the coverage figure; the claim repeats exactly those.

human label: ______________   reviewer: ______________   note: ______________

---

## CE08  (dev)

**Claim:** Wikipedia's infobox for SQLite lists 2004-06-18 as its initial release.

**Source** - SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

> Cited quote: "Initial release

2004-06-18"

Evidence text the verifier receives  (complete: nothing is cut):

```text
application/vnd.sqlite3[3]

Magic number

53 51 4c 69 74 65 20 66 6f 72 6d 61 74 20 33 00 (zero-terminated ASCII "SQLite format 3")

Initial release

2004-06-18

Open format?

yes (Public Domain)

Website

sqlite.org/fileformat.html

This article is part of a series on the

Structured Query Language

List of RDBMSs

4th Dimension

Access Database Engine

Actian Zen

Adabas D

SAP Advantage Database Server

Airtable

Altibase

Amazon Aurora

Amazon Redshift

Apache Derby

Apache Ignite

Aster Data Systems

CA Datacom

Clarion

ClickHouse

Clustrix

CockroachDB

CUBRID

DataEase

DataFlex

Dataphor

dBase

DuckDB

Empress Embedded Database

EnterpriseDB

eXtremeDB

Exasol

Extensible Storage Engine

FileMaker Pro

Firebird

FoundationDB

FrontBase

Google BigQuery

Greenplum

H2

Helix

HSQLDB

IBM Db2

IBM Lotus Approach

CA IDMS

Infobright

Informix

Ingres

InterBase

InterSystems Caché

InterSystems IRIS

Linter SQL RDBMS

MariaDB

MaxDB

Microsoft Azure SQL Database
```

**Proposed (PROVISIONAL, not a label):** `supported` - The shown text lists 2004-06-18 as the initial release and the claim only reports what the infobox lists. Deciding rule: R1.

**Why a reviewer might hesitate:** Elsewhere on the same Wikipedia page (a passage the verifier does not receive for this claim) version 1.0 is dated August 2000. Under R1 that is outside the evidence; a reviewer who feels the claim is misleading anyway should say so in the note.

human label: ______________   reviewer: ______________   note: ______________

---

## CE11  (dev)

**Claim:** SQLite allows concurrent reads and writes with write-ahead logging, and its database files can be at most 281 terabytes.

**Source** - SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

> Cited quote: "relaxed in version 3.7 as write-ahead logging (WAL) enables concurrent reads and writes"

Evidence text the verifier receives  (complete: nothing is cut):

```text
SQLite stores the entire database, consisting of definitions, tables, indices, and data, as a single cross-platform file, allowing several processes or threads to access the same database concurrently. It implements this simple design by locking the database file during writing.[20] Write access may fail with an error code, or it can be retried until a configurable timeout expires. SQLite read operations can be multitasked, though due to the serverless design, writes can only be performed sequentially. This concurrent access restriction does not apply to temporary tables, and it is relaxed in version 3.7 as write-ahead logging (WAL) enables concurrent reads and writes.[21] Since SQLite has to rely on file-system locks, it is not the preferred choice for write-intensive deployments.[22]
```

**Proposed (PROVISIONAL, not a label):** `partially_supported` - The WAL half is supported by the cited passage; the 281 TB half is not in that passage.

human label: ______________   reviewer: ______________   note: ______________

---

## CE12  (dev)

**Claim:** Read Committed is PostgreSQL's default isolation level and it prevents phantom reads.

**Source** - PostgreSQL: Documentation: 18: 13.2. Transaction Isolation - <https://www.postgresql.org/docs/current/transaction-iso.html> - retrieved 2026-10-20

> Cited quote: "Read Committed is the default isolation level in PostgreSQL."

Evidence text the verifier receives  (complete: nothing is cut):

```text
Read Committed is the default isolation level in PostgreSQL. When a transaction uses this isolation level, a SELECT query (without a FOR UPDATE/SHARE clause) sees only data committed before the query began; it never sees either uncommitted data or changes committed by concurrent transactions during the query's execution. In effect, a SELECT query sees a snapshot of the database as of the instant the query begins to run. However, SELECT does see the effects of previous updates executed within its own transaction, even though they are not yet committed. Also note that two successive SELECT commands can see different data, even though they are within a single transaction, if other transactions commit changes after the first SELECT starts and before the second SELECT starts.
```

**Proposed (PROVISIONAL, not a label):** `partially_supported` - The default-level half is supported. Nothing cited says Read Committed prevents phantom reads, and the passage notes successive SELECTs can see different data. Deciding rule: R4.

**Why a reviewer might hesitate:** The shown text says two successive SELECTs can see different data, which implies phantom reads are possible but does not say so. R3 needs an explicit statement, so the second half is not established rather than contradicted, and R4 gives partially_supported.

human label: ______________   reviewer: ______________   note: ______________

---

## CE16  (dev)

**Claim:** The SQLite library has 156,000 lines of source code, and its test suites add up to 920 million lines of test code.

**Source** - SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

> Cited quote: "The SQLite library has 156,000 lines of source code, while all the test suites combined add up to 92 million lines of test code."

Evidence text the verifier receives  (complete: nothing is cut):

```text
SQLite uses automated regression testing prior to each release. Over 2 million tests are run as part of a release's verification. The SQLite library has 156,000 lines of source code, while all the test suites combined add up to 92 million lines of test code. SQLite's tests simulate a number of exceptional scenarios, such as power loss and I/O errors, in addition to testing the library's functionality. Starting with the August 10, 2009 release of SQLite 3.6.17, SQLite releases have 100% branch test coverage, one of the components of code coverage. SQLite has four different test harnesses: the original public-domain TCL tests, the proprietary C-language TH3 test suite, the SQL Logic Tests, which check SQLite against other SQL databases, and the dbsqlfuzz proprietary fuzzing engine.[46]

Notable uses

[edit]

Operating systems

[edit]

SQLite is included by default in:

Android[14][47]

BlackBerry Tablet OS[48]

iOS[13]
```

**Proposed (PROVISIONAL, not a label):** `partially_supported` - The source-code figure matches; the test-code figure is altered (92 million became 920 million). SYNTHETIC alteration (changed_quantity): 92 million lines of test code was changed to 920 million in the second half of a two-part claim.

**FLAGGED FOR REVIEW under claim-labels-v2:** the proposed label was made under the superseded rule; rubric v2 would give `contradicted`. The 156,000 figure matches; the test-code figure (920 million vs 92 million) is explicitly contradicted. Matches the reviewer's stated decision (contradicted); the provisional label still shows the v1 result.

human label: ______________   reviewer: ______________   note: ______________

---

## CE21  (dev)

**Claim:** PostgreSQL supports native declarative table partitioning.

**Source** - PostgreSQL: Documentation: 18: 13.2. Transaction Isolation - <https://www.postgresql.org/docs/current/transaction-iso.html> - retrieved 2026-10-20

> Cited quote: "Read Committed is the default isolation level in PostgreSQL."

Evidence text the verifier receives  (complete: nothing is cut):

```text
Read Committed is the default isolation level in PostgreSQL. When a transaction uses this isolation level, a SELECT query (without a FOR UPDATE/SHARE clause) sees only data committed before the query began; it never sees either uncommitted data or changes committed by concurrent transactions during the query's execution. In effect, a SELECT query sees a snapshot of the database as of the instant the query begins to run. However, SELECT does see the effects of previous updates executed within its own transaction, even though they are not yet committed. Also note that two successive SELECT commands can see different data, even though they are within a single transaction, if other transactions commit changes after the first SELECT starts and before the second SELECT starts.
```

**Proposed (PROVISIONAL, not a label):** `unsupported` - The quote is genuine and about PostgreSQL but says nothing about partitioning.

human label: ______________   reviewer: ______________   note: ______________

---

## CE23  (dev)

**Claim:** SQLite added a NoSQL interface in 2011.

**Source** - SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

> Cited quote: "In 2011, Hipp announced his plans to add a NoSQL interface to SQLite"

Evidence text the verifier receives  (complete: nothing is cut):

```text
In August 2000, version 1.0 of SQLite was released, with storage based on gdbm (GNU Database Manager). In September 2001, SQLite 2.0 replaced gdbm with a custom B-tree implementation,[a] adding transaction capability. In June 2004, SQLite 3.0 added internationalization, manifest typing, and other major improvements, partially funded by America Online. In 2011, Hipp announced his plans to add a NoSQL interface to SQLite, as well as announcing UnQL, a functional superset of SQL designed for document-oriented databases.[15]

SQLite is one of four formats recommended for long-term storage of datasets approved for use by the United States Library of Congress.[16][17][18]

Design

[edit]
```

**Proposed (PROVISIONAL, not a label):** `unsupported` - The passage reports an announced PLAN in 2011; the claim turns it into a completed event. SYNTHETIC alteration (forecast_to_fact): 'announced his plans to add' was restated as 'added'. Deciding rule: R6.

human label: ______________   reviewer: ______________   note: ______________

---

## CE30  (dev)

**Claim:** Tinybird says teams should always run Postgres locally through Docker.

**Source** - Postgres vs SQLite | Tinybird - <https://www.tinybird.co/blog/postgres-vs-sqlite> - retrieved 2026-10-20

> Cited quote: "[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]"

Evidence text the verifier receives  (complete: nothing is cut):

```text
[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]
```

**Proposed (PROVISIONAL, not a label):** `partially_supported` - Tinybird does suggest running Postgres locally via Docker (the central fact); it offers it as 'a useful middle ground', not as something teams should always do. Deciding rule: R5.

**Why a reviewer might hesitate:** Revised from unsupported before any human review: the suggestion itself is established, only 'always' is not (R5).

human label: ______________   reviewer: ______________   note: ______________

---

## CE31  (dev)

**Claim:** DataCamp names SQLite as the winner for ACID compliance.

**Source** - SQLite vs PostgreSQL: A Detailed Comparison | DataCamp - <https://www.datacamp.com/blog/sqlite-vs-postgresql-detailed-comparison> - retrieved 2026-10-20

> Cited quote: "[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]"

Evidence text the verifier receives  (complete: nothing is cut):

```text
[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]
```

**Proposed (PROVISIONAL, not a label):** `contradicted` - The same passage names PostgreSQL as the winner for ACID compliance. SYNTHETIC alteration (swapped_entity): PostgreSQL was replaced by SQLite as the winner.

human label: ______________   reviewer: ______________   note: ______________

---

## CE36  (dev)

**Claim:** SQLite lets several processes write to the same database file at the same time.

**Source** - SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

> Cited quote: "due to the serverless design, writes can only be performed sequentially"

Evidence text the verifier receives  (complete: nothing is cut):

```text
SQLite stores the entire database, consisting of definitions, tables, indices, and data, as a single cross-platform file, allowing several processes or threads to access the same database concurrently. It implements this simple design by locking the database file during writing.[20] Write access may fail with an error code, or it can be retried until a configurable timeout expires. SQLite read operations can be multitasked, though due to the serverless design, writes can only be performed sequentially. This concurrent access restriction does not apply to temporary tables, and it is relaxed in version 3.7 as write-ahead logging (WAL) enables concurrent reads and writes.[21] Since SQLite has to rely on file-system locks, it is not the preferred choice for write-intensive deployments.[22]
```

**Proposed (PROVISIONAL, not a label):** `contradicted` - Writes are sequential per the passage. SYNTHETIC alteration (negation): 'writes can only be performed sequentially' was restated as writers being able to write at the same time. Deciding rule: R3.

**Why a reviewer might hesitate:** The shown passage also says WAL 'enables concurrent reads and writes', which is reads alongside a writer, not several writers. The claim is about several writers writing at once, which the text explicitly rules out ('writes can only be performed sequentially'): R3, contradicted.

human label: ______________   reviewer: ______________   note: ______________

---

## CE37  (dev)

**Claim:** PostgreSQL internally implements four distinct transaction isolation levels.

**Source** - PostgreSQL: Documentation: 18: 13.2. Transaction Isolation - <https://www.postgresql.org/docs/current/transaction-iso.html> - retrieved 2026-10-20

> Cited quote: "internally only three distinct isolation levels are implemented"

Evidence text the verifier receives  (complete: nothing is cut):

```text
Serializable

Not possible

Not possible

Not possible

Not possible

In PostgreSQL, you can request any of the four standard transaction isolation levels, but internally only three distinct isolation levels are implemented, i.e., PostgreSQL's Read Uncommitted mode behaves like Read Committed. This is because it is the only sensible way to map the standard isolation levels to PostgreSQL's multiversion concurrency control architecture.

The table also shows that PostgreSQL's Repeatable Read implementation does not allow phantom reads. This is acceptable under the SQL standard because the standard specifies which anomalies must not occur at certain isolation levels; higher guarantees are acceptable. The behavior of the available isolation levels is detailed in the following subsections.

To set the transaction isolation level of a transaction, use the command SET TRANSACTION.

Important
```

**Proposed (PROVISIONAL, not a label):** `contradicted` - The manual says only three are implemented internally even though four can be requested. SYNTHETIC alteration (changed_quantity): three was changed to four.

human label: ______________   reviewer: ______________   note: ______________

---

## CE40  (dev)

**Claim:** The SQLite library has 156 million lines of source code.

**Source** - SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

> Cited quote: "The SQLite library has 156,000 lines of source code"

Evidence text the verifier receives  (complete: nothing is cut):

```text
SQLite uses automated regression testing prior to each release. Over 2 million tests are run as part of a release's verification. The SQLite library has 156,000 lines of source code, while all the test suites combined add up to 92 million lines of test code. SQLite's tests simulate a number of exceptional scenarios, such as power loss and I/O errors, in addition to testing the library's functionality. Starting with the August 10, 2009 release of SQLite 3.6.17, SQLite releases have 100% branch test coverage, one of the components of code coverage. SQLite has four different test harnesses: the original public-domain TCL tests, the proprietary C-language TH3 test suite, the SQL Logic Tests, which check SQLite against other SQL databases, and the dbsqlfuzz proprietary fuzzing engine.[46]

Notable uses

[edit]

Operating systems

[edit]

SQLite is included by default in:

Android[14][47]

BlackBerry Tablet OS[48]

iOS[13]
```

**Proposed (PROVISIONAL, not a label):** `contradicted` - The passage says 156,000 lines. SYNTHETIC alteration (changed_quantity): 156,000 was changed to 156 million.

human label: ______________   reviewer: ______________   note: ______________

---
