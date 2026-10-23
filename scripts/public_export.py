"""Build the sanitized PUBLIC copy of the evaluation data from the full private working tree.

This script is part of the public tree for transparency. It needs the FULL evidence (the private repository's
evaluation/data/claim_evidence.json), which is not published, so it cannot be re-run from the public tree alone.

    python scripts/public_export.py --full PATH/TO/PRIVATE/deepdive --public .

What it does, and nothing else:
  * claim_evidence.json: for the four rights-reserved sources (datacamp, tinybird, devto, logz) the nine passages' text and every quote taken from
    them is replaced by its SHA-256 and length; the source URL, title, retrieval date and content hash stay. Every example that cites a withheld
    passage records the claim hash computed on the full evidence, so human labels and the split lock keep their meaning.
  * claims_withheld (CE04, CE10): the claim text itself is withheld (their sentence restates a withheld page); its hash and length are kept. Not reworded.
  * claim_examples_source.py: quotes from withheld passages, and those two claim texts, are replaced by a marker.
  * review sheets that embed withheld passages: the quote and the passage text are replaced by a marker.
  * two frozen result files: the recorded local database path is redacted and the file is marked as a sanitized derivative.
Nothing is invented: no replacement evidence, no changed label, no changed claim text.
"""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

WITHHELD_SOURCES = {"datacamp", "tinybird", "devto", "logz"}
# Two claim texts restate one sentence of a withheld page in similar words (a nine-word run each): the CLAIM text is withheld too, not reworded.
CLAIM_WITHHELD = ("CE04", "CE10")
MARKER = "[withheld: rights-reserved third-party excerpt; see PUBLIC_RELEASE.md]"
VARIANT = "public-export-v1"
REDACTED_PATH = "<local path redacted>"
NOTE = ("Sanitized derivative: the only change from the original frozen result is that the local absolute path in metadata.settings.database_path "
        "was replaced by a placeholder; every measured value is unchanged. The original stays in the private working repository.")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_example(example: dict, passages: dict) -> str:
    """The same function as evaluation.datasets.claim_hash applied to the FULL evidence (kept in sync by tests/test_public_export.py)."""
    ev = [(passages[i["passage"]]["text"], i["quote"]) for i in example["evidence"]]
    return sha(json.dumps({"claim": example["claim"], "evidence": ev}, sort_keys=True, ensure_ascii=False))


def sanitize_dataset(full: dict) -> dict:
    out = json.loads(json.dumps(full))
    passages = full["passages"]
    withheld = {k for k, p in passages.items() if p["source"] in WITHHELD_SOURCES}
    for k in withheld:
        p = out["passages"][k]
        text = passages[k]["text"]
        p.update({"text": None, "withheld": True, "text_sha256": sha(text), "text_chars": len(text),
                  "withheld_reason": "all rights reserved by the publisher; URL, retrieval date and source content hash are kept"})
    for key in WITHHELD_SOURCES:
        out["sources"][key]["withheld"] = True
    n_examples = 0
    for e_full, e in zip(full["examples"], out["examples"]):
        if not any(i["passage"] in withheld for i in e_full["evidence"]):
            continue
        n_examples += 1
        e["evidence_withheld"] = True
        e["claim_sha256_full_evidence"] = _hash_example(e_full, passages)
        if e["id"] in CLAIM_WITHHELD:
            e["claim_withheld"] = True
            e["claim_sha256"] = sha(e_full["claim"])
            e["claim_chars"] = len(e_full["claim"])
            e["claim"] = None
        for i_full, i in zip(e_full["evidence"], e["evidence"]):
            if i_full["passage"] in withheld:
                i["quote"] = None
                i["quote_sha256"] = sha(i_full["quote"])
                i["quote_chars"] = len(i_full["quote"])
    for e in out["examples"]:  # a short quoted fragment inside a model-written rationale
        r = e["proposed"].get("rationale", "")
        if "; it gives no 300 KB figure" in r:  # one rationale quotes a phrase of a withheld passage in its first clause
            e["proposed"]["rationale"] = re.sub(r"^The source says [^;]*;", "The source gives a figure under 600 KB (wording withheld);", r)
    out["public_export"] = {
        "variant": VARIANT,
        "derived_from_dataset_built": full["built"],
        "withheld_sources": sorted(WITHHELD_SOURCES),
        "passages_total": len(passages), "passages_withheld": len(withheld), "passages_retained": len(passages) - len(withheld),
        "examples_total": len(full["examples"]), "examples_with_withheld_evidence": n_examples,
        "examples_with_full_evidence": len(full["examples"]) - n_examples,
        "claims_withheld": list(CLAIM_WITHHELD),
        "hash_policy": ("Content that is published can be re-hashed here. Withheld content keeps the hash recorded on the original: text_sha256 / quote_sha256 for "
                        "passages and quotes, claim_sha256 for a withheld claim text, claim_sha256_full_evidence for the claim-plus-evidence hash that labels "
                        "and the split lock use. Those retained hashes cannot be verified from this export."),
        "note": ("Text of the withheld passages is not published. Hashes below let a holder of the full evidence verify it. Claim texts (except the two in "
                 "claims_withheld), proposed labels, split assignment and the split lock are unchanged; for an example with withheld evidence the claim hash recorded on the full evidence is used, "
                 "so human labels and the held-out lock keep their meaning. Anything that needs the evidence text refuses such an example unless an "
                 "available-evidence subset is selected explicitly."),
    }
    return out


def sanitize_source_module(text: str, full: dict) -> str:
    tree = ast.parse(text)
    claim_texts = {e["claim"] for e in full["examples"] if e["id"] in CLAIM_WITHHELD}
    lines = text.split("\n")
    lines = [ln + "\n" for ln in lines[:-1]] + [lines[-1]]  # split on newline only, as ast counts lines
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ex" and len(node.args) >= 2:
            c = node.args[1]
            if isinstance(c, ast.Constant) and c.value in claim_texts:
                spans.append((c.lineno, c.col_offset, c.end_lineno, c.end_col_offset))
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ev" and len(node.args) == 3:
            src = node.args[0]
            if isinstance(src, ast.Constant) and src.value in WITHHELD_SOURCES:
                q = node.args[2]
                spans.append((q.lineno, q.col_offset, q.end_lineno, q.end_col_offset))
    for l1, c1, l2, c2 in sorted(spans, reverse=True):
        a, b = lines[l1 - 1].encode("utf-8"), lines[l2 - 1].encode("utf-8")  # ast column offsets are UTF-8 byte offsets
        lines[l1 - 1:l2] = [a[:c1].decode("utf-8") + "WITHHELD" + b[c2:].decode("utf-8")]
    out = "".join(lines)
    out = out.replace("def ev(source, ordinal, quote):", f'WITHHELD = "{MARKER}"\n\n\ndef ev(source, ordinal, quote):', 1)
    out = re.sub(r'(rationale=")The source says [^;"]*(; it gives no 300 KB)', r"\1The source gives a figure under 600 KB (wording withheld)\2", out)
    banner = ('"""PUBLIC EXPORT (public-export-v1): quotes taken from the four rights-reserved sources, and the claim text of CE04 and CE10, are replaced by WITHHELD. This module is the authoring\n'
              'source of the full private dataset and is kept for its claims, rules and rationales; it cannot rebuild the data without the private evidence.\n\n')
    return out.replace('"""', banner, 1)


def sanitize_review_md(text: str, full: dict) -> str:
    texts = {k: p["text"] for k, p in full["passages"].items() if p["source"] in WITHHELD_SOURCES}
    quotes = {i["quote"] for e in full["examples"] for i in e["evidence"] if i["passage"] in texts}
    for t in sorted(texts.values(), key=len, reverse=True):
        text = text.replace(t.strip(), MARKER)
    for q in sorted(quotes, key=len, reverse=True):
        text = text.replace(q, MARKER)
    first, _, rest = text.partition("\n")
    banner = ("\n\n> PUBLIC EXPORT (public-export-v1): the quote and evidence text of CE30 and CE31 are withheld (rights reserved). This sheet is a record of the "
              "original review; those two examples cannot be re-reviewed from it.")
    return first + banner + "\n" + rest


def sanitize_review_csv(text: str, full: dict) -> str:
    texts = [p["text"] for p in full["passages"].values() if p["source"] in WITHHELD_SOURCES]
    quotes = {i["quote"] for e in full["examples"] for i in e["evidence"] if full["passages"][i["passage"]]["source"] in WITHHELD_SOURCES}
    for s in sorted(list(quotes) + texts, key=len, reverse=True):
        text = text.replace(s, MARKER).replace(s.replace('"', '""'), MARKER)
    return text


NOTE_PILOT = ("Sanitized derivative: the only change from the original frozen result is that one quoted phrase of a withheld (rights-reserved) passage inside the verifier's "
              "explanation for CE31 (records[].semantic_reason) was replaced by '[withheld]'; every measured value is unchanged. metadata.dataset_sha256 is the hash of "
              "the original full dataset, not of the public export's claim_evidence.json. The original stays in the private working repository.")


def _mark(raw: str, note: str) -> str:
    eol = "\r\n" if "\r\n" in raw else "\n"
    i = raw.index('"metadata": {') + len('"metadata": {')
    return raw[:i] + f'{eol}  "sanitized_derivative": {json.dumps(note)},' + raw[i:]


def sanitize_result(raw: str) -> str:
    d = json.loads(raw)
    path = d["metadata"]["settings"]["database_path"]
    assert re.match(r"^[A-Za-z]:\\\\?", path) or path.startswith("/"), path
    raw = _mark(raw.replace(json.dumps(path)[1:-1], REDACTED_PATH), NOTE)
    json.loads(raw)  # still valid
    return raw


def sanitize_pilot_verification(raw: str) -> str:
    """The verifier's explanation for CE31 quotes a phrase of the withheld passage after 'winner for offering'; the quoted phrase is replaced."""
    new, n = re.subn(r"(winner for offering )'[^']*'", r"\1'[withheld]'", raw)
    assert n == 1, n
    new = _mark(new, NOTE_PILOT)
    json.loads(new)
    return new


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", required=True, help="the private working tree holding the full evidence")
    ap.add_argument("--public", default=".", help="the public tree to sanitize in place")
    a = ap.parse_args()
    full_root, pub = Path(a.full), Path(a.public)
    full = json.loads((full_root / "evaluation/data/claim_evidence.json").read_text(encoding="utf-8"))
    ev = pub / "evaluation/data/claim_evidence.json"
    ev.write_text(json.dumps(sanitize_dataset(full), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    src = pub / "evaluation/claim_examples_source.py"
    src.write_text(sanitize_source_module(src.read_text(encoding="utf-8"), full), encoding="utf-8")
    for name in ("claim_review_batch1.md", "batch1_review_ai_assisted.md"):
        f = pub / "evaluation/review" / name
        f.write_text(sanitize_review_md(f.read_text(encoding="utf-8"), full), encoding="utf-8")
    f = pub / "evaluation/review/claim_review_batch1.csv"
    f.write_text(sanitize_review_csv(f.read_text(encoding="utf-8"), full), encoding="utf-8")
    for n in (3, 4):
        f = pub / f"evaluation/results/pilot_e2e_evaluation_budget_attempt{n}.json"
        f.write_text(sanitize_result(f.read_bytes().decode("utf-8")), encoding="utf-8", newline="")
    f = pub / "evaluation/results/pilot_verification.json"
    f.write_text(sanitize_pilot_verification(f.read_bytes().decode("utf-8")), encoding="utf-8", newline="")
    print("sanitized")


if __name__ == "__main__":
    main()
