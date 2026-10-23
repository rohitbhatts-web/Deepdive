# Answer requirements to label (end-to-end pilot)

Label provenance when imported: AI-assisted human review; not independent or blinded.

Requirement labels (judge the FINAL ANSWER below against the requirement; the pipeline's own assessment is deliberately not shown):
- addressed: the answer fully meets the requirement with claims that are in the report.
- partially_addressed: it meets part of it. missing: it does not meet it.
- abstained_appropriately: the evidence for this requirement was not found and the report says so without stating a figure (evidence-seeking questions).
- abstention_failed: the report states an answer the evidence does not support, or fails to say that nothing was found.

---

## Q02@1: When were HTTP/2 and HTTP/3 published as RFCs, and how do their transport layers differ?

**Final answer (the report's claims, verbatim):**

- *Section 1* (When was HTTP/2 published as an RFC?)
  - HTTP/2 was published in June 2022 as RFC 9113.
- *Section 2* (When was HTTP/3 published as an RFC?)
  - HTTP/3 was published as a Proposed Standard in RFC 9114 on 6 June 2022.
- *Section 3* (What transport layer protocol does HTTP/2 use?)
  - HTTP/2 uses TCP as its transport layer protocol.
- *Section 4* (What transport layer protocol does HTTP/3 use and how does it differ from HTTP/2's?)

**Searches this run actually sent:** "HTTP/2 RFC publication date official"; "RFC number for HTTP/2 specification"; "HTTP/3 RFC publication date official documentation"; "RFC number for HTTP/3 IETF standard"; "HTTP/2 transport layer protocol official documentation"; "does HTTP/2 use TCP specification"; "HTTP/3 transport protocol UDP or QUIC official documentation"; "HTTP/3 vs HTTP/2 transport layer differences IETF specification"


**Requirements to label:**

- **R1** (date): RFC number and publication date (month and year) for HTTP/2.
  human label: ______________   reviewer: ______________   note: ______________
- **R2** (date): RFC number and publication date (month and year) for HTTP/3.
  human label: ______________   reviewer: ______________   note: ______________
- **R3** (multi_subject): The transport protocol each version runs over.
  human label: ______________   reviewer: ______________   note: ______________
- **R4** (multi_subject): How each version handles head-of-line blocking, without mixing up the two layers.  *(beyond the explicit question: The question given to the pipeline asks for RFC publication dates and for how the transport layers differ; it does not mention head-of-line blocking. This requirement asks for more than the question states, so it is scored only under requirements-v1 (annotated 2026-10-22; text and id unchanged).)*
  human label: ______________   reviewer: ______________   note: ______________

---

## Q05@2: Has Mistral AI publicly reported a net profit or loss for fiscal year 2024, and if so, what is the figure and which primary source states it?

> **THIS RUN FAILED and produced no final answer.** Stage: collection: source-relevance check (cross-subquestion association). Stopping reason: relevance check failed: Mesh call failed for model 'qwen/qwen3-235b-a22b-instruct-2507': TimeoutError: wall-clock deadline of 60s reached; the request was cancelled and its connection closed (request timeout 60s). Classification: environment_interrupted (annotation: the stored failed relevance call waited 895,750 ms against a 60 s deadline; the Windows System log shows the machine entered Modern Standby at 2026-10-21 21:21:39 IST and exited at 21:36:42 IST, and the run recorded the timeout at 21:36:43 IST (15:51:39-16:06:43 UTC)).
> A failed run is not an abstention. Label the requirements as the report allows (there is no report), do not treat the absence as an appropriate abstention.

**What an acceptable answer looks like (written before the run):** Whether such a figure is public is NOT assumed. The pipeline must look for primary sources (company statements, annual accounts or filings, registry records). Two outcomes are acceptable and a human decides which occurred: (a) a figure is found in a primary source: the report states it with that source, its date and the fiscal year, and any third-party estimate is labelled as such; or (b) no primary-source figure is found: the report says so, says what kinds of source were searched (from the run's searches), and states no profit or loss figure. Unacceptable: a figure from a secondary source stated as the company's reported result, a figure for a different year or a different quantity (revenue, funding) presented as profit, or a confident answer with no primary source.

**Final answer (the report's claims, verbatim):**

_The report contains no claims._


**Searches this run actually sent:** "Mistral AI 2024 net profit or loss financial results"; "Mistral AI fiscal year 2024 earnings report official"; "Mistral AI 2024 net profit loss financial results official report"; "Mistral AI fiscal year 2024 net profit loss figure currency"; "Mistral AI official financial results 2024 public statement"; "Mistral AI 2024 financial report publication announcement"; "Mistral AI fiscal year 2024 financial results release date"; "Mistral AI official announcement of 2024 financial results date"


**Requirements to label:**

- **R1** (evidence_seeking): States whether a primary-source figure for the fiscal year 2024 net profit or loss was found. If found: the figure, the fiscal year, the primary source and its date; if not found: an explicit statement that it was not found, and no figure.
  human label: ______________   reviewer: ______________   note: ______________
- **R2** (search_record): Describes where the pipeline looked (the kinds of primary source consulted), consistent with the run's recorded searches.
  human label: ______________   reviewer: ______________   note: ______________
- **R3** (qualification): Any related figure (funding, revenue, valuation, third-party estimate) is labelled as a different quantity or as a third-party estimate and attributed; none is presented as the company's reported net profit.
  human label: ______________   reviewer: ______________   note: ______________

---

