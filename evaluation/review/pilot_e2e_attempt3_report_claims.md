# Final-report claims to label (end-to-end pilot)

Label provenance when imported: AI-assisted human review; not independent or blinded.

Report-claim labels (judge the claim ONLY against the cited passage text shown with it):
- supported: every assertion is established by the cited evidence. partially_supported: some are, others missing or overstated, with no explicit contradiction.
- unsupported: the cited evidence does not establish it. contradicted: the cited evidence explicitly states something incompatible with any assertion.

---

## Q02@1: When were HTTP/2 and HTTP/3 published as RFCs, and how do their transport layers differ?

### Claim 4

**Claim:** HTTP/2 was published in June 2022 as RFC 9113.

**Source:** RFC 9113 - HTTP/2 - <https://datatracker.ietf.org/doc/html/rfc9113>

> Cited quote: "RFC 9113

HTTP/2

June 2022"

Cited passage text:

```text
Light

Dark

Auto

RFC 9113

HTTP/2

June 2022

Thomson & Benfield

Standards Track

[Page]

Stream:

Internet Engineering Task Force (IETF)

RFC:

9113

Obsoletes:

7540, 8740

Category:

Standards Track

Published:

June 2022

ISSN:

2070-1721

Authors:

M. Thomson, Ed.

Mozilla

C. Benfield, Ed.

Apple Inc.

RFC 9113

HTTP/2

Abstract

This specification describes an optimized expression of the semantics of the Hypertext Transfer Protocol (HTTP), referred to as HTTP version 2 (HTTP/2). HTTP/2 enables a more efficient use of network resources and a reduced latency by introducing field compression and allowing multiple concurrent exchanges on the same connection.¶

This document obsoletes RFCs 7540 and 8740.¶

Status of This Memo

This is an Internet Standards Track document.¶
```

human label: ______________   reviewer: ______________   note: ______________

---

### Claim 2

**Claim:** HTTP/3 was published as a Proposed Standard in RFC 9114 on 6 June 2022.

**Source:** HTTP/3 - Wikipedia - <https://en.wikipedia.org/wiki/HTTP/3>

> Cited quote: "On 6 June 2022, IETF published HTTP/3 as a Proposed Standard in RFC 9114[1]."

Cited passage text:

```text
Support for HTTP/3 was added to Chrome (Canary build) in September 2019 and then eventually reached stable builds, but was disabled by a feature flag. It was enabled by default in April 2020.[9] Firefox added support for HTTP/3 in November 2019 through a feature flag[7][16][17] and started enabling it by default in April 2021 in Firefox 88.[7][10] Experimental support for HTTP/3 was added to Safari Technology Preview on April 8, 2020[18] and was included with Safari 14 that ships with iOS 14 and macOS 11,.[19][20] Starting September 2024, HTTP/3 is supported for all users on Safari 16 or newer.[11]

On 6 June 2022, IETF published HTTP/3 as a Proposed Standard in RFC 9114[1].

Comparison with HTTP/1.1 and HTTP/2

[edit]
```

human label: ______________   reviewer: ______________   note: ______________

---

### Claim 3

**Claim:** HTTP/2 uses TCP as its transport layer protocol.

**Source:** HTTP/3 - Wikipedia - <https://en.wikipedia.org/wiki/HTTP/3>

> Cited quote: "Both HTTP/1.1 and HTTP/2 use TCP as their transport."

Cited passage text:

```text
HTTP semantics are consistent across versions: the same request methods, status codes, and message fields are typically applicable to all versions. The differences are in the mapping of these semantics to underlying transports. Both HTTP/1.1 and HTTP/2 use TCP as their transport. HTTP/3 uses QUIC, a transport layer network protocol which uses user space congestion control over the User Datagram Protocol (UDP). The switch to QUIC aims to fix a major problem of HTTP/2 called "head-of-line blocking": because the parallel nature of HTTP/2's multiplexing is not visible to TCP's loss recovery mechanisms, a lost or reordered packet causes all active transactions to experience a stall regardless of whether that transaction was impacted by the lost packet. Because QUIC provides native multiplexing, lost packets only impact the streams where data has been lost.
```

human label: ______________   reviewer: ______________   note: ______________

---

## Q05@2: Has Mistral AI publicly reported a net profit or loss for fiscal year 2024, and if so, what is the figure and which primary source states it?

