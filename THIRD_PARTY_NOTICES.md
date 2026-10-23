# Third-party notices (frontend)

The React frontend (`frontend/`) uses open-source packages installed from npm; their licenses are in `frontend/node_modules/<package>/LICENSE`
and in `frontend/package-lock.json`. Direct dependencies: React, Vite, Tailwind CSS, Radix UI primitives, lucide-react (ISC),
class-variance-authority, clsx, tailwind-merge, Vitest and Testing Library (all MIT or ISC).

## shadcn/ui (MIT)

`frontend/src/components/ui/{button,badge,card,tabs}.tsx` were added with the shadcn CLI and lightly adjusted (import paths).

Copyright (c) 2023 shadcn

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to
the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions
of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# Third-party text in the evaluation data

`evaluation/data/claim_evidence.json` and the review sheets and result files derived from it quote short excerpts of web pages as test evidence. The original (private) dataset has
**23 passages from six sources**. This public export (`public-export-v1`, see [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md)) publishes the text of **14 passages from the two sources whose terms allow it
(8 Wikipedia, 6 PostgreSQL documentation)** and **withholds the other 9 passages from the four all-rights-reserved sources** (DataCamp 2, Tinybird 3, DEV 2, Logz.io 2). For the withheld passages only the
URL, title, retrieval date, the source's content hash, and SHA-256 hashes and lengths of the passage and quote are published. Excerpts are used only to check whether a claim is supported by the quoted
text; no full page snapshot is committed (the run databases, which hold fetched page text, are excluded by `.gitignore`). Rights status as understood at review time (not legal advice):

| Source | Passages | Status in this repository |
|---|---|---|
| en.wikipedia.org, "SQLite" | 8 retained (6,434 characters); further Wikipedia excerpts appear in end-to-end result files | CC BY-SA 4.0 |
| postgresql.org documentation, "Transaction Isolation" | 6 retained (5,208 characters) | PostgreSQL License |
| datatracker.ietf.org RFC text (e.g. RFC 9113) | only inside end-to-end result files, as retrieved by the pipeline | IETF Trust Legal Provisions |
| datacamp.com, tinybird.co, dev.to, logz.io blog posts | **9 withheld** (DataCamp 2, Tinybird 3, DEV 2, Logz.io 2) | all rights reserved by their authors; text not published, names and URLs kept for attribution |

## Attribution for the retained text

**Wikipedia (CC BY-SA 4.0).** Excerpts are taken from the English Wikipedia article "SQLite" (<https://en.wikipedia.org/wiki/SQLite>, retrieved 2026-10-20) and, in result files, other Wikipedia pages whose
URL and title are stored next to each excerpt; the authors are listed in each page's history. Excerpts are shortened and whitespace-normalised, and are not endorsed by Wikipedia or its authors. The excerpts, and the review sheets
and result files that reproduce them, are licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), not under any licence of this project.

**PostgreSQL documentation (PostgreSQL License).** Excerpts are from <https://www.postgresql.org/docs/current/transaction-iso.html> (retrieved 2026-10-20). The documentation's legal notice, reproduced as the licence requires
(page <https://www.postgresql.org/docs/current/legalnotice.html>, read 2026-10-23):

> PostgreSQL Database Management System (also known as Postgres, formerly known as Postgres95)
>
> Portions Copyright (c) 1996-2026, PostgreSQL Global Development Group
>
> Portions Copyright (c) 1994, The Regents of the University of California
>
> Permission to use, copy, modify, and distribute this software and its documentation for any purpose, without fee, and without a written agreement is hereby granted, provided that the above copyright notice and this paragraph and the following two paragraphs appear in all copies.
>
> IN NO EVENT SHALL THE UNIVERSITY OF CALIFORNIA BE LIABLE TO ANY PARTY FOR DIRECT, INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES, INCLUDING LOST PROFITS, ARISING OUT OF THE USE OF THIS SOFTWARE AND ITS DOCUMENTATION, EVEN IF THE UNIVERSITY OF CALIFORNIA HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
>
> THE UNIVERSITY OF CALIFORNIA SPECIFICALLY DISCLAIMS ANY WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE SOFTWARE PROVIDED HEREUNDER IS ON AN "AS-IS" BASIS, AND THE UNIVERSITY OF CALIFORNIA HAS NO OBLIGATIONS TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS, OR MODIFICATIONS.

**IETF RFC text.** Pages such as RFC 9113 appear as excerpts in end-to-end result files. RFC text is subject to the IETF Trust Legal Provisions (<https://trustee.ietf.org/license-info>); each RFC carries its own copyright
notice naming the IETF Trust and the document's authors, which applies to the reproduced excerpts, and the source URL stored with each excerpt leads to the original with that notice.

**Withheld sources.** DataCamp, Tinybird, DEV (author "lovestaco") and Logz.io are named and linked as the sources of withheld test evidence; none of their text is published here. The claim texts of two dataset examples (CE04, CE10) restated a sentence of those pages in similar words and are withheld too; see PUBLIC_RELEASE.md.
