# Claim-evidence label review: batch 1 (12 development examples), rubric claim-labels-v2

> PUBLIC EXPORT (public-export-v1): the quote and evidence text of CE30 and CE31 are withheld (rights reserved). This sheet is a record of the original review; those two examples cannot be re-reviewed from it.

> **Label provenance: AI-assisted human review; not independent or blinded.**
>
> Rubric version: **claim-labels-v2**. Reviewer on every row: Rohit Bhatnagar, 2026-10-21. Nothing here is imported until the reviewer runs the import.
>
> All 12 rows are **HUMAN-CONFIRMED (AI-assisted)**: the reviewer saw AI-suggested labels, discussed them with the assistant and confirmed each one. Not an independent or blinded annotation.

## The batch at a glance

| ID | Label | Status | Rationale |
|---|---|---|---|
| CE01 | `supported` | HUMAN-CONFIRMED (AI-assisted) | Release 3.6.17 and 100% branch coverage are both stated. |
| CE08 | `partially_supported` | HUMAN-CONFIRMED (AI-assisted) | The date is present, but the supplied context identifies a file-format infobox rather than establishing the software-release claim. |
| CE11 | `partially_supported` | HUMAN-CONFIRMED (AI-assisted) | WAL concurrency is supported; the 281 TB limit is not in the passage. |
| CE16 | `contradicted` | HUMAN-CONFIRMED (AI-assisted) | 920 million explicitly conflicts with 92 million (rubric v2 R3). |
| CE12 | `partially_supported` | HUMAN-CONFIRMED (AI-assisted) | Default isolation is supported; phantom-read prevention is not established explicitly by the supplied passage under our rubric. |
| CE21 | `unsupported` | HUMAN-CONFIRMED (AI-assisted) | Isolation-level evidence does not establish partitioning support. |
| CE23 | `unsupported` | HUMAN-CONFIRMED (AI-assisted) | Announcing plans does not establish implementation. |
| CE30 | `partially_supported` | HUMAN-CONFIRMED (AI-assisted) | A Docker suggestion does not establish "always". |
| CE31 | `contradicted` | HUMAN-CONFIRMED (AI-assisted) | The ACID section explicitly names PostgreSQL, not SQLite, as winner. |
| CE36 | `contradicted` | HUMAN-CONFIRMED (AI-assisted) | Sequential writes contradict multiple simultaneous writers; concurrent reads and writes do not establish multiple writers. |
| CE37 | `contradicted` | HUMAN-CONFIRMED (AI-assisted) | Four distinct levels conflicts with three. |
| CE40 | `contradicted` | HUMAN-CONFIRMED (AI-assisted) | 156 million conflicts with 156,000. |

## How to finish

Run the import commands given in the chat yourself. Labels are versioned and never overwritten; a later change needs `--correct` and a `correction_reason`.

## Rubric claim-labels-v2 (what the labels are judged against)

Judge the claim ONLY against the evidence text shown (exactly what the verifier receives).

- **R1 Evidence boundary.** Judge ONLY the cited text exactly as shown on the review sheet (that is what the verifier receives, cut at 1,500 characters, which cuts none of these). Ignore other parts of the page, other passages and what you know. A frozen example has no neighbouring-passage context; the production pipeline adds neighbours.
- **R2 Decompose the claim.** List every assertion: entities, numbers, dates, scope words (always, only, all, at most), hedges and attribution (who said it).
- **R3 contradicted.** The shown text explicitly states something incompatible with an assertion of the claim (a different number or date, the opposite status, a negation, a different named entity). An explicit evidential contradiction takes precedence over partial support: if any assertion is explicitly contradicted, label contradicted even when another assertion is established. Something merely implied or merely not mentioned is never a contradiction.
- **R4 Precedence when parts disagree.** First apply R3: an explicitly contradicted assertion makes the claim contradicted. Only when no assertion is explicitly contradicted: if at least one assertion is established AND at least one other is missing, stronger, broader or more certain than the text, label partially_supported. partially_supported therefore means support that is incomplete or overstated WITHOUT explicit contradiction. Supported never wins when any part fails.
- **R5 Weaker core versus stronger claim.** When the text establishes the claim's central fact but in a weaker form (a suggestion stated as a rule, a list entry stated as the largest, a hedge dropped), label partially_supported.
- **R6 unsupported.** The text does not establish the claim's central fact: off-topic, a genuine quote that says nothing about it, a typical use case restated as a limit, a plan or forecast restated as something that happened, a different assertion on the same topic.
- **R7 Attribution.** A claim that attributes a statement or recommendation to its source ('Tinybird says ...') is judged on whether the source says it. A claim that states a source's opinion, ranking or forecast as plain fact is not fully supported.

## Evidence

Complete verifier-visible evidence for all 12 examples.

### CE01: `supported`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** SQLite releases since version 3.6.17 (10 August 2009) have 100% branch test coverage.

**Rationale:** Release 3.6.17 and 100% branch coverage are both stated.

**Source:** SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

**Cited quote:** "Starting with the August 10, 2009 release of SQLite 3.6.17, SQLite releases have 100% branch test coverage"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

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

---

### CE08: `partially_supported`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** Wikipedia's infobox for SQLite lists 2004-06-18 as its initial release.

**Rationale:** The date is present, but the supplied context identifies a file-format infobox rather than establishing the software-release claim.

**Source:** SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

**Cited quote:** "Initial release

2004-06-18"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

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

---

### CE11: `partially_supported`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** SQLite allows concurrent reads and writes with write-ahead logging, and its database files can be at most 281 terabytes.

**Rationale:** WAL concurrency is supported; the 281 TB limit is not in the passage.

**Source:** SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

**Cited quote:** "relaxed in version 3.7 as write-ahead logging (WAL) enables concurrent reads and writes"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

```text
SQLite stores the entire database, consisting of definitions, tables, indices, and data, as a single cross-platform file, allowing several processes or threads to access the same database concurrently. It implements this simple design by locking the database file during writing.[20] Write access may fail with an error code, or it can be retried until a configurable timeout expires. SQLite read operations can be multitasked, though due to the serverless design, writes can only be performed sequentially. This concurrent access restriction does not apply to temporary tables, and it is relaxed in version 3.7 as write-ahead logging (WAL) enables concurrent reads and writes.[21] Since SQLite has to rely on file-system locks, it is not the preferred choice for write-intensive deployments.[22]
```

---

### CE16: `contradicted`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** The SQLite library has 156,000 lines of source code, and its test suites add up to 920 million lines of test code.

**Rationale:** 920 million explicitly conflicts with 92 million (rubric v2 R3).

> Flagged under v2: the original AI-provisional label (`partially_supported`) was made under the superseded v1 R4; v2 gives `contradicted`.

**Source:** SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

**Cited quote:** "The SQLite library has 156,000 lines of source code, while all the test suites combined add up to 92 million lines of test code."

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

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

---

### CE12: `partially_supported`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** Read Committed is PostgreSQL's default isolation level and it prevents phantom reads.

**Rationale:** Default isolation is supported; phantom-read prevention is not established explicitly by the supplied passage under our rubric.

**Source:** PostgreSQL: Documentation: 18: 13.2. Transaction Isolation - <https://www.postgresql.org/docs/current/transaction-iso.html> - retrieved 2026-10-20

**Cited quote:** "Read Committed is the default isolation level in PostgreSQL."

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

```text
Read Committed is the default isolation level in PostgreSQL. When a transaction uses this isolation level, a SELECT query (without a FOR UPDATE/SHARE clause) sees only data committed before the query began; it never sees either uncommitted data or changes committed by concurrent transactions during the query's execution. In effect, a SELECT query sees a snapshot of the database as of the instant the query begins to run. However, SELECT does see the effects of previous updates executed within its own transaction, even though they are not yet committed. Also note that two successive SELECT commands can see different data, even though they are within a single transaction, if other transactions commit changes after the first SELECT starts and before the second SELECT starts.
```

---

### CE21: `unsupported`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** PostgreSQL supports native declarative table partitioning.

**Rationale:** Isolation-level evidence does not establish partitioning support.

**Source:** PostgreSQL: Documentation: 18: 13.2. Transaction Isolation - <https://www.postgresql.org/docs/current/transaction-iso.html> - retrieved 2026-10-20

**Cited quote:** "Read Committed is the default isolation level in PostgreSQL."

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

```text
Read Committed is the default isolation level in PostgreSQL. When a transaction uses this isolation level, a SELECT query (without a FOR UPDATE/SHARE clause) sees only data committed before the query began; it never sees either uncommitted data or changes committed by concurrent transactions during the query's execution. In effect, a SELECT query sees a snapshot of the database as of the instant the query begins to run. However, SELECT does see the effects of previous updates executed within its own transaction, even though they are not yet committed. Also note that two successive SELECT commands can see different data, even though they are within a single transaction, if other transactions commit changes after the first SELECT starts and before the second SELECT starts.
```

---

### CE23: `unsupported`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** SQLite added a NoSQL interface in 2011.

**Rationale:** Announcing plans does not establish implementation.

**Source:** SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

**Cited quote:** "In 2011, Hipp announced his plans to add a NoSQL interface to SQLite"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

```text
In August 2000, version 1.0 of SQLite was released, with storage based on gdbm (GNU Database Manager). In September 2001, SQLite 2.0 replaced gdbm with a custom B-tree implementation,[a] adding transaction capability. In June 2004, SQLite 3.0 added internationalization, manifest typing, and other major improvements, partially funded by America Online. In 2011, Hipp announced his plans to add a NoSQL interface to SQLite, as well as announcing UnQL, a functional superset of SQL designed for document-oriented databases.[15]

SQLite is one of four formats recommended for long-term storage of datasets approved for use by the United States Library of Congress.[16][17][18]

Design

[edit]
```

---

### CE30: `partially_supported`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** Tinybird says teams should always run Postgres locally through Docker.

**Rationale:** A Docker suggestion does not establish "always".

**Source:** Postgres vs SQLite | Tinybird - <https://www.tinybird.co/blog/postgres-vs-sqlite> - retrieved 2026-10-20

**Cited quote:** "[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

```text
[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]
```

---

### CE31: `contradicted`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** DataCamp names SQLite as the winner for ACID compliance.

**Rationale:** The ACID section explicitly names PostgreSQL, not SQLite, as winner.

**Source:** SQLite vs PostgreSQL: A Detailed Comparison | DataCamp - <https://www.datacamp.com/blog/sqlite-vs-postgresql-detailed-comparison> - retrieved 2026-10-20

**Cited quote:** "[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

```text
[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]
```

---

### CE36: `contradicted`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** SQLite lets several processes write to the same database file at the same time.

**Rationale:** Sequential writes contradict multiple simultaneous writers; concurrent reads and writes do not establish multiple writers.

**Source:** SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

**Cited quote:** "due to the serverless design, writes can only be performed sequentially"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

```text
SQLite stores the entire database, consisting of definitions, tables, indices, and data, as a single cross-platform file, allowing several processes or threads to access the same database concurrently. It implements this simple design by locking the database file during writing.[20] Write access may fail with an error code, or it can be retried until a configurable timeout expires. SQLite read operations can be multitasked, though due to the serverless design, writes can only be performed sequentially. This concurrent access restriction does not apply to temporary tables, and it is relaxed in version 3.7 as write-ahead logging (WAL) enables concurrent reads and writes.[21] Since SQLite has to rely on file-system locks, it is not the preferred choice for write-intensive deployments.[22]
```

---

### CE37: `contradicted`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** PostgreSQL internally implements four distinct transaction isolation levels.

**Rationale:** Four distinct levels conflicts with three.

**Source:** PostgreSQL: Documentation: 18: 13.2. Transaction Isolation - <https://www.postgresql.org/docs/current/transaction-iso.html> - retrieved 2026-10-20

**Cited quote:** "internally only three distinct isolation levels are implemented"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

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

---

### CE40: `contradicted`  [HUMAN-CONFIRMED (AI-assisted)]

**Claim:** The SQLite library has 156 million lines of source code.

**Rationale:** 156 million conflicts with 156,000.

**Source:** SQLite - Wikipedia - <https://en.wikipedia.org/wiki/SQLite> - retrieved 2026-10-20

**Cited quote:** "The SQLite library has 156,000 lines of source code"

**Complete evidence text supplied to the verifier** (the whole cited passage; nothing is cut, and no neighbouring passage is supplied for a frozen example):

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

---

