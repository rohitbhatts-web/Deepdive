"""Build the sanitized seed of the hosted recorded demo from a PRIVATE run database.

    python scripts/build_demo_seed.py --source PRIVATE_RUNS.db --out demo/seed.sql.gz [--extra-withheld PRIVATE/evaluation/data/claim_evidence.json]

The private database holds the full text of every fetched page, which includes pages whose rights are not cleared for redistribution. The hosted demo
must not serve them, so this script never copies the file: it creates a fresh database with the current schema and inserts rows one by one, with these rules.

  * A source is PUBLISHABLE only if its host is on ALLOWED_HOSTS (Wikipedia: CC BY-SA; PostgreSQL documentation; IETF RFC text), the same sources whose
    text the public evaluation export keeps. Every other source keeps its URL, title, retrieval date and content hash, but its snapshot and every passage
    text become a marker.
  * A claim quote that cites a withheld source's passage becomes a marker.
  * Search-result snippets for hosts that are not allowed become a marker.
  * Every other text value (claims, explanations, audit payloads, report text, gaps, notes) is scanned for any run of SCRUB_WORDS or more words shared with
    a withheld page; a value with such a run has the run replaced by "[withheld]". (--extra-withheld adds the four rights-reserved evaluation sources.)
  * Text of publishable sources is never altered: quotes must stay verbatim in their passages.
Nothing is written to the source database. The output is a gzip-compressed SQL dump (deterministic) that `python -m backend.demo_seed` loads.
It also prints a manifest of what was withheld and replaced, and re-checks the result before writing it.
"""

import argparse
import gzip
import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import db

ALLOWED_HOSTS = {"en.wikipedia.org", "datatracker.ietf.org", "www.rfc-editor.org", "rfc-editor.org", "www.postgresql.org"}
SCRUB_WORDS = 6
SOURCE_MARKER = ("[Text withheld in the public demo: the rights to redistribute this page are not cleared. "
                 "The URL, title, retrieval date and content hash are kept.]")
QUOTE_MARKER = "[quote withheld in the public demo]"
SNIPPET_MARKER = "[snippet withheld in the public demo]"
WORD = re.compile(r"[a-z0-9]+(?:[.'’-][a-z0-9]+)*")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def shingles(texts) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for t in texts:
        w = words(t)
        out.update(tuple(w[i:i + SCRUB_WORDS]) for i in range(len(w) - SCRUB_WORDS + 1))
    return out


def scrub_text(text: str, sh: set, hits: list) -> str:
    """Replace every maximal run of >= SCRUB_WORDS words that occurs in a withheld page."""
    spans = [(m.start(), m.end(), m.group()) for m in WORD.finditer(text.lower())]
    n = SCRUB_WORDS
    cuts, i = [], 0
    while i <= len(spans) - n:
        if tuple(s[2] for s in spans[i:i + n]) in sh:
            j = i + n
            while j < len(spans) and tuple(s[2] for s in spans[j - n + 1:j + 1]) in sh:
                j += 1
            cuts.append((spans[i][0], spans[j - 1][1]))
            i = j
        else:
            i += 1
    for a, b in reversed(cuts):
        hits.append(text[a:b])
        text = text[:a] + "[withheld]" + text[b:]
    return text


def scrub_value(v, sh: set, hits: list):
    if isinstance(v, str):
        if v[:1] in "{[" and v[-1:] in "}]":
            try:
                parsed = json.loads(v)
            except ValueError:
                parsed = None
            if parsed is not None:
                before = len(hits)
                new = scrub_value(parsed, sh, hits)
                return json.dumps(new, ensure_ascii=False) if len(hits) > before else v
        return scrub_text(v, sh, hits)
    if isinstance(v, list):
        return [scrub_value(x, sh, hits) for x in v]
    if isinstance(v, dict):
        return {k: scrub_value(x, sh, hits) for k, x in v.items()}
    return v


def host(url: str) -> str:
    return urlparse(url or "").netloc.lower()


def tables(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]


def build(source: Path, extra: Path | None, runs: list[int] | None, more_withheld: tuple[Path, ...] = ()) -> tuple[sqlite3.Connection, dict, set]:
    src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tmp = Path(tempfile.mkdtemp(prefix="deepdive-seed-")) / "seed.db"
    dst = db.connect(tmp)
    dst.execute("PRAGMA foreign_keys = OFF")
    run_ids = runs or [r[0] for r in src.execute("SELECT id FROM runs ORDER BY id")]

    withheld_sources = {r["id"]: r for r in src.execute("SELECT * FROM sources") if host(r["final_url"]) not in ALLOWED_HOSTS or host(r["requested_url"]) not in ALLOWED_HOSTS}
    withheld_texts = [r["snapshot"] for r in withheld_sources.values()]
    for other in more_withheld:  # pages withheld from OTHER demo runs stay withheld here too (nothing of them may surface in this run's text)
        o = sqlite3.connect(f"file:{other.resolve().as_posix()}?mode=ro", uri=True)
        withheld_texts += [snap for url, snap in o.execute("SELECT final_url, snapshot FROM sources") if host(url) not in ALLOWED_HOSTS]
        o.close()
    if extra:
        data = json.loads(extra.read_text(encoding="utf-8"))
        flagged = {"datacamp", "tinybird", "devto", "logz"}
        withheld_texts += [p["text"] for p in data["passages"].values() if p["source"] in flagged and p.get("text")]
    sh = shingles(withheld_texts)
    # a phrase that also occurs in a PUBLISHABLE page is already public through that page (and is often a quote that must stay verbatim): not scrubbed
    sh -= shingles(r['snapshot'] for r in src.execute('SELECT snapshot, final_url FROM sources') if host(r['final_url']) in ALLOWED_HOSTS)
    withheld_passage_ids = {r["id"] for r in src.execute("SELECT id, source_id FROM passages") if r["source_id"] in withheld_sources}
    hits_by_table: Counter = Counter()
    all_hits: list[tuple[str, str]] = []
    counts: Counter = Counter()

    for table in tables(src):
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
        if not cols or table not in tables(dst):
            continue
        has_run = "run_id" in cols
        for row in src.execute(f"SELECT * FROM {table}"):
            r = dict(row)
            if table == "runs" and r["id"] not in run_ids:
                continue
            if has_run and r["run_id"] not in run_ids:
                continue
            hits: list = []
            if table == "sources" and r["id"] in withheld_sources:
                r["snapshot"] = SOURCE_MARKER
                counts["sources_withheld"] += 1
            elif table == "passages" and r["id"] in withheld_passage_ids:
                r["text"] = SOURCE_MARKER
                counts["passages_withheld"] += 1
            elif table == "claim_evidence" and r["passage_id"] in withheld_passage_ids:
                r["quote"] = QUOTE_MARKER
                counts["quotes_withheld"] += 1
            elif table == "searches":
                try:
                    res = json.loads(r["result_json"] or "null")
                except ValueError:
                    res = None
                if isinstance(res, dict):
                    for hit in res.get("hits", []):
                        if host(hit.get("url", "")) not in ALLOWED_HOSTS:
                            for key in ("snippet", "content", "raw_content"):
                                if hit.get(key):
                                    hit[key] = SNIPPET_MARKER
                                    counts["snippets_withheld"] += 1
                    r["result_json"] = json.dumps(res, ensure_ascii=False)
            # generic scan of text values, except the published sources' own text (quotes must stay verbatim in their passages)
            protected = (table in ("sources", "passages")) or (table == "claim_evidence" and r.get("quote") != QUOTE_MARKER and r.get("passage_id") not in withheld_passage_ids)
            if not protected:
                for k, v in list(r.items()):
                    if isinstance(v, str) and v != SOURCE_MARKER and v != QUOTE_MARKER and not (table == "searches" and k == "result_json"):  # result_json handled above (titles stay)
                        new = scrub_value(v, sh, hits)
                        if new != v:
                            r[k] = new
            if hits:
                hits_by_table[table] += len(hits)
                all_hits += [(table, h) for h in hits]
            dst.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [r[c] for c in cols])
            counts[f"rows:{table}"] += 1
    dst.commit()
    manifest = {
        "source_runs": run_ids,
        "allowed_hosts": sorted(ALLOWED_HOSTS),
        "published_sources": sorted(host(r["final_url"]) + " " + r["final_url"] for r in src.execute("SELECT * FROM sources") if r["id"] not in withheld_sources and r["run_id"] in run_ids),
        "withheld_sources": sorted(r["final_url"] for r in withheld_sources.values() if r["run_id"] in run_ids),
        "counts": {k: v for k, v in sorted(counts.items()) if not k.startswith("rows:")},
        "rows": {k[5:]: v for k, v in sorted(counts.items()) if k.startswith("rows:")},
        "scrubbed_runs_by_table": dict(hits_by_table),
        "scrubbed_runs": [(t, h[:90]) for t, h in all_hits],
    }
    src.close()
    return dst, manifest, sh


def verify(dst: sqlite3.Connection, sh: set) -> dict:
    """Re-check the built database before it is written: nothing withheld remains outside published text, and published text is intact."""
    problems = []
    leaks = Counter()
    for table in tables(dst):
        cols = [r[1] for r in dst.execute(f"PRAGMA table_info({table})")]
        for row in dst.execute(f"SELECT * FROM {table}"):
            for c in cols:
                v = row[c]
                if not isinstance(v, str) or (table in ("sources", "passages")):
                    continue
                if table == "claim_evidence" and c == "quote":
                    continue
                if table == "searches" and c == "result_json":
                    continue
                if scrub_text(v, sh, []) != v:
                    leaks[f"{table}.{c}"] += 1
    published = {r["id"] for r in dst.execute("SELECT id, final_url FROM sources") if host(r["final_url"]) in ALLOWED_HOSTS}
    for p in dst.execute("SELECT p.id, p.text, p.start_offset, p.end_offset, s.snapshot, s.final_url FROM passages p JOIN sources s ON s.id = p.source_id"):
        if host(p["final_url"]) in ALLOWED_HOSTS and p["text"] not in p["snapshot"]:
            problems.append(f"published passage {p['id']} is not inside its snapshot")
        if host(p["final_url"]) not in ALLOWED_HOSTS and (p["text"] != SOURCE_MARKER):
            problems.append(f"withheld passage {p['id']} still has text")
    for r in dst.execute("SELECT s.final_url, s.snapshot FROM sources s"):
        if host(r["final_url"]) not in ALLOWED_HOSTS and r["snapshot"] != SOURCE_MARKER:
            problems.append(f"withheld source {r['final_url']} still has a snapshot")
    for e in dst.execute("SELECT ce.id, ce.quote, p.text, s.final_url FROM claim_evidence ce JOIN passages p ON p.id = ce.passage_id JOIN sources s ON s.id = p.source_id"):
        if host(e["final_url"]) in ALLOWED_HOSTS and e["quote"] not in e["text"]:
            # a quote may have been normalised on storage; the stored 'resolution' says how it was matched
            problems.append(f"evidence {e['id']}: quote not verbatim in its published passage")
    return {"leaks_outside_published_text": dict(leaks), "problems": problems, "published_source_ids": sorted(published)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--runs", type=int, nargs="*")
    ap.add_argument("--extra-withheld", type=Path, help="the private claim_evidence.json: its four rights-reserved sources are added to the withheld set")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dst, manifest, sh = build(a.source, a.extra_withheld, a.runs)
    check = verify(dst, sh)
    print(json.dumps({**manifest, "verification": check}, indent=1, ensure_ascii=False))
    if check["problems"] or check["leaks_outside_published_text"]:
        print("REFUSING to write: the checks above failed", file=sys.stderr)
        return 1
    if not a.dry_run:
        dump = "\n".join(dst.iterdump()) + "\n"
        a.out.parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "wb") as f:
            f.write(gzip.compress(dump.encode("utf-8"), mtime=0))
        print("wrote", a.out, f"({a.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
