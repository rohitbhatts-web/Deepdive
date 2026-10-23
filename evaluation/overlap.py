"""Source and passage overlap between the development and held-out claim examples (and with the earlier development runs).

Held-out claims are separate CLAIMS, but they were written from the same few stored web pages as the development claims, so they are NOT an
independent test of how the verifier behaves on unseen sources. This report makes the overlap explicit and countable; it never hides it.
`python -m evaluation overlap` writes data/overlap_report.json; a test fails if the file no longer matches the datasets.
"""

import json
from pathlib import Path

from evaluation.datasets import load_claim_dataset

REPORT = Path(__file__).parent / "data" / "overlap_report.json"


def compute() -> dict:
    data = load_claim_dataset()
    ex = data["examples"]
    by_split = {"dev": [e for e in ex if e["split"] == "dev"], "heldout": [e for e in ex if e["split"] == "heldout"]}

    def passages(e):
        return {i["passage"] for i in e["evidence"]}

    def sources(e):
        return {data["passages"][p]["source"] for p in passages(e)}

    dev_passages = {p for e in by_split["dev"] for p in passages(e)}
    dev_sources = {s for e in by_split["dev"] for s in sources(e)}
    rows = []
    for e in by_split["heldout"]:
        shared_p = sorted(passages(e) & dev_passages)
        rows.append({"example": e["id"], "sources": sorted(sources(e)), "passages": sorted(passages(e)),
                     "source_also_in_dev": sorted(sources(e) & dev_sources),
                     "passage_also_in_dev": shared_p,
                     "dev_examples_citing_same_passage": sorted(d["id"] for d in by_split["dev"] if passages(d) & passages(e)),
                     "passage_cited_by_earlier_development_run_claims": any(data["passages"][p]["cited_by_development_claims"] for p in passages(e))})
    n = len(rows)
    return {
        "scope": data["evaluation_scope"],
        "statement": ("This claim set is single-domain (SQLite/PostgreSQL evidence). It is not a cross-domain evaluation. Its held-out examples "
                      "reuse sources and, in many cases, passages that development examples use."),
        "heldout_examples": n,
        # Every source here comes from the stored databases of the Phase 1-4 development runs, so no source is unseen by development, including a
        # source that no development EXAMPLE happens to use (devto): the page was retrieved, and its passages offered to the drafter, in those runs.
        "sources_retrieved_by_development_runs": {"sources": len(data["sources"]), "of_total": len(data["sources"]),
                                                  "heldout_sources_among_them": len({s_ for e in by_split["heldout"] for s_ in sources(e)})},
        "heldout_with_source_also_in_dev": sum(1 for r in rows if r["source_also_in_dev"]),
        "heldout_with_passage_also_in_dev": sum(1 for r in rows if r["passage_also_in_dev"]),
        "heldout_with_passage_cited_by_earlier_development_run_claims": sum(1 for r in rows if r["passage_cited_by_earlier_development_run_claims"]),
        "distinct_sources": {"total": len({s for e in ex for s in sources(e)}), "in_heldout": len({s for e in by_split["heldout"] for s in sources(e)}),
                             "only_in_heldout": sorted({s for e in by_split["heldout"] for s in sources(e)} - dev_sources)},
        "distinct_passages": {"total": len({p for e in ex for p in passages(e)}), "in_dev": len(dev_passages),
                              "in_heldout": len({p for e in by_split["heldout"] for p in passages(e)}),
                              "only_in_heldout": sorted({p for e in by_split["heldout"] for p in passages(e)} - dev_passages)},
        "per_heldout_example": rows,
        "consequence": ("A verifier tuned on development examples has seen the same pages (and often the same passages) as the held-out claims. A good "
                        "held-out score therefore supports 'the verifier handles new CLAIMS about familiar sources', not 'it generalises to new sources or "
                        "domains'. Independence for sources comes only from end-to-end questions Q09-Q12, whose sources are found by live search."),
    }


def write() -> Path:
    REPORT.write_text(json.dumps(compute(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return REPORT
