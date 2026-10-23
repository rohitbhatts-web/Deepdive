"""Generic, lexical checks for the difference between an ORIGINAL publication date and the date of a later revision.

Nothing here knows any document, protocol, number or date. It only looks for the wording a document uses when it says it replaces an earlier one, and
for whether a date-seeking requirement is about a date. The checks are conservative heuristics: a false positive turns a date answer into context (the
gap stays visible); it never turns anything into an answer. They are deterministic and are not a substitute for reading the source.
"""

import re

# Wording by which a cited document says IT replaces an earlier version (a header "Obsoletes: ..." / "Updates: ...", "this document supersedes ...",
# "replaces the previous edition"). Deliberately narrow: ordinary uses ("the new engine replaces the old one", "an earlier revision of the record") do not match.
_SUPERSEDES = re.compile(r"\b(?:obsoletes|updates)\s*:|\bthis\s+(?:\w+\s+){0,2}?(?:obsoletes|supersedes|replaces|revises)\b|\b(?:obsoletes|supersedes|replaces)\s+(?:the\s+)?(?:previous|earlier|prior|older|old)\s+(?:version|edition|revision|release|document|specification|standard)", re.IGNORECASE)
# (Only the forms in which the document replaces an EARLIER one. "Obsoleted by" / "superseded by" / "updated by" describe an earlier document that a
# later one replaced; a date taken from such a page is the earlier document's own date and is not flagged.)
# Wording by which a CLAIM itself says that what it dates is a revision / replacement / update.
_STATES_REVISION = re.compile(r"\b(?:obsolet\w*|supersed\w*|replac\w*|revis\w*|updat\w*|successor|earlier|original(?:ly)?|previous|prior|first)\b", re.IGNORECASE)
# A requirement or obligation that asks for a date or a point in time.
_DATE_SEEKING = re.compile(r"\b(?:date|dated|year|month|when|publish\w*|publication|released?|issued|introduc\w*|announced|launched|founded|established)\b", re.IGNORECASE)

REVISION_ONLY_GAP = "only the date of a later revision is established; the original publication date is not"
ORIGINAL_ASKED_REASON = "the original publication date was asked for; this claim dates a later revision of the document, which is not that date"
HIDDEN_REVISION_REASON = ("the cited document says it supersedes an earlier version, but the claim gives its date without saying so; "
                          "it does not establish an original publication date")


def evidence_shows_supersession(passage_texts: list[str]) -> bool:
    return any(_SUPERSEDES.search(t or "") for t in passage_texts)


def text_states_revision(claim_text: str) -> bool:
    return bool(_STATES_REVISION.search(claim_text or ""))


def is_date_seeking(*texts: str) -> bool:
    return any(_DATE_SEEKING.search(t or "") for t in texts)


# What KIND of date a requirement asks for. Only an explicit request for the ORIGINAL / first date, or for the date of the REVISED / current document, changes how
# a revision date is treated; anything else is ambiguous and keeps the distinction.
_ORIGINAL = re.compile(r"\b(?:original(?:ly)?|first|initial(?:ly)?|earliest|history|historical|inception|debut|first\s+published)\b", re.IGNORECASE)
_REVISED = re.compile(r"\b(?:latest|current(?:ly)?|newest|newer|most\s+recent|revised|revision|revisions|updated|replacement|successor|new\s+version)\b", re.IGNORECASE)


# Words that say WHICH VERSION of a subject is meant, not what the subject is ("the current edition of X", "the original X"). The subject-mention backstop
# ignores them: a claim about X does not have to repeat "current" to be about the current X.
VERSION_QUALIFIERS = frozenset({"original", "originally", "first", "initial", "initially", "earliest", "current", "currently", "latest", "newest", "newer", "revised",
                                "revision", "revisions", "updated", "version", "versions", "edition", "editions"})


def date_scope(*texts: str) -> str:
    """'original' (asks for the first publication / history), 'revision' (asks for the date of the revised or current document) or 'ambiguous'
    (neither, or both). Read from the requirement, its dimension and its subquestion only."""
    joined = " ".join(t or "" for t in texts)
    original, revised = bool(_ORIGINAL.search(joined)), bool(_REVISED.search(joined))
    return "original" if original and not revised else "revision" if revised and not original else "ambiguous"
