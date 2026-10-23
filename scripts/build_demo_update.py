"""Build a versioned UPDATE PACKAGE that adds one recorded run to an existing hosted demo database.

    python scripts/build_demo_update.py --source PRIVATE_FOLLOWUP.db --id 0002-a1a2-v5 --title "..." \
        --out demo/updates/0002-a1a2-v5.json.gz --extra-withheld PRIVATE/evaluation/data/claim_evidence.json \
        --also-withheld-from PRIVATE_CONTINUATION.db

Why a package and not a new seed: a deployed demo keeps its database on a persistent volume, so a rebuilt seed would never reach it. The package holds the new
run's rows with every id already shifted past the base seed's ids (so nothing collides and the existing runs keep their ids), the same publication sanitization
as the seed (scripts/build_demo_seed.py: only allow-listed sources keep their text), and the facts `backend.demo_update` checks before it touches a database.

Id remapping is mechanical and checked: integer ids move by the base seed's per-table maximum; foreign keys follow the schema's declared references (plus
report_citations.source_id); passage ids (`S<source>-P<n>`) are rewritten wherever they occur, in text and JSON; id-bearing keys inside JSON columns are mapped by an
explicit table, and an unknown id-like key stops the build. Nothing is invented or edited: claims, verdicts, answer/context roles and gaps are the recorded rows.
"""

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_demo_seed as seedlib

from backend import db

PASSAGE = re.compile(r"\bS(\d+)-P(\d+)\b")
# id-bearing keys inside JSON columns -> the table whose ids they hold
JSON_KEYS = {"claim_id": "claims", "claim_ids": "claims", "eligible_claim_ids": "claims", "rejected_ids": "claims", "report_id": "reports",
             "evidence_id": "claim_evidence", "source_id": "sources", "subquestion_id": "subquestions", "run_id": "runs", "model_call_id": "model_calls",
             "result_id": "verification_results", "obligation_id": "coverage_obligations", "assessment_id": "coverage_assessments"}
EXTRA_FKS = {("report_citations", "source_id"): "sources"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_key(question: str, created_at: str) -> str:
    return sha(f"{question}\n{created_at}")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_base(seed: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(gzip.decompress(seed.read_bytes()).decode("utf-8"))
    return conn


def table_info(conn: sqlite3.Connection) -> dict:
    out = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"):
        fks = {r[3]: r[2] for r in conn.execute(f"PRAGMA foreign_key_list({t})")}
        fks.update({c: tt for (tn, c), tt in EXTRA_FKS.items() if tn == t})
        cols = [(r[1], r[2]) for r in conn.execute(f"PRAGMA table_info({t})")]
        out[t] = {"cols": [c for c, _ in cols], "fks": fks, "int_id": any(c == "id" and ty.upper() == "INTEGER" for c, ty in cols)}
    return out


def build_update(args) -> dict:
    dst, manifest, sh = seedlib.build(args.source, args.extra_withheld, None, tuple(args.also_withheld_from or ()))
    check = seedlib.verify(dst, sh)
    if check["problems"] or check["leaks_outside_published_text"]:
        raise SystemExit(f"refusing: sanitization checks failed: {check}")
    base = load_base(args.base_seed)
    info = table_info(dst)
    offset = {t: (base.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0] if i["int_id"] else 0) for t, i in info.items()}
    src_offset = offset["sources"]
    maps: dict[str, dict[int, int]] = {t: {} for t, i in info.items() if i["int_id"]}
    for t, table_map in maps.items():
        for (old,) in dst.execute(f"SELECT id FROM {t}"):
            table_map[old] = old + offset[t]

    def passage(m):
        return f"S{int(m.group(1)) + src_offset}-P{m.group(2)}"

    def remap_json(v, where):
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                tgt = JSON_KEYS.get(k)
                if tgt and isinstance(x, int):
                    out[k] = maps[tgt][x]
                elif tgt and isinstance(x, list):
                    out[k] = [maps[tgt][i] if isinstance(i, int) else remap_json(i, where) for i in x]
                elif k.endswith(("_id", "_ids")) and not tgt and (isinstance(x, int) or (isinstance(x, list) and any(isinstance(i, int) for i in x))):
                    raise SystemExit(f"unmapped id-like JSON key {k!r} in {where}; add it to JSON_KEYS after checking what it refers to")
                else:
                    out[k] = remap_json(x, where)
            return out
        if isinstance(v, list):
            return [remap_json(x, where) for x in v]
        if isinstance(v, str):
            return PASSAGE.sub(passage, v)
        return v

    def remap_value(table, col, v, fks):
        if v is None:
            return v
        if col in fks and fks[col] != "passages":
            return maps[fks[col]][v]
        if isinstance(v, str):
            if v[:1] in "{[" and v[-1:] in "}]":
                try:
                    return json.dumps(remap_json(json.loads(v), f"{table}.{col}"), ensure_ascii=False)
                except ValueError:
                    pass
            return PASSAGE.sub(passage, v)
        return v

    tables, run_rows = {}, []
    for t, i in info.items():
        rows = []
        for r in dst.execute(f"SELECT * FROM {t} ORDER BY rowid"):
            row = {c: remap_value(t, c, r[c], i["fks"]) for c in i["cols"]}
            if i["int_id"]:
                row["id"] = maps[t][r["id"]]
            rows.append(row)
        if rows:
            tables[t] = rows
    for r in dst.execute("SELECT id, question, created_at FROM runs ORDER BY id"):
        run_rows.append({"key": run_key(r["question"], r["created_at"]), "question": r["question"], "created_at": r["created_at"], "new_run_id": maps["runs"][r["id"]]})
    base_runs = [run_key(r["question"], r["created_at"]) for r in base.execute("SELECT question, created_at FROM runs ORDER BY id")]
    body = {"tables": tables, "runs": run_rows}
    package = {
        "format": 1, "id": args.id, "title": args.title, "schema_version": db.SCHEMA_VERSION,
        "requires": {"base_run_keys": base_runs, "base_seed_sha256": hashlib.sha256(args.base_seed.read_bytes()).hexdigest()},
        "sanitization": {"allowed_hosts": manifest["allowed_hosts"], "published_sources": manifest["published_sources"], "withheld_sources": manifest["withheld_sources"],
                         "counts": manifest["counts"], "scrubbed_runs": manifest["scrubbed_runs"]},
        "id_offsets": {t: o for t, o in offset.items() if o},
        "runs": run_rows, "tables": tables, "body_sha256": sha(canonical(body)),
    }
    return package


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path, help="private run database holding the run to add")
    ap.add_argument("--base-seed", type=Path, default=Path("demo/seed.sql.gz"))
    ap.add_argument("--id", required=True, help="update id, e.g. 0002-a1a2-v5 (applied in id order; recorded in the database)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--extra-withheld", type=Path)
    ap.add_argument("--also-withheld-from", type=Path, nargs="*")
    args = ap.parse_args()
    package = build_update(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(gzip.compress(json.dumps(package, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8"), mtime=0))
    print(json.dumps({"id": package["id"], "runs": package["runs"], "rows": {t: len(r) for t, r in package["tables"].items()}, "id_offsets": package["id_offsets"],
                      "sanitization": package["sanitization"]}, indent=1, ensure_ascii=False))
    print("wrote", args.out, f"({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
