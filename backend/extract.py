"""Readable-text extraction and passage splitting. Deterministic; no LLM.

The snapshot is the extracted text: paragraphs joined by a blank line. Every passage is an exact slice
of the snapshot (`snapshot[start:end] == text`). Adjacent short paragraphs are packed into one passage
so each carries its neighbouring context; paragraphs longer than the limit are split at sentence
boundaries (and, for a single over-long sentence, at whitespace).
"""

import re
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "aside"}
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul",
    "ol", "tr", "td", "th", "table", "blockquote", "pre", "br", "figcaption", "dd", "dt", "dl", "hr",
}
_WS = re.compile(r"\s+")


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.title_parts: list[str] = []
        self._buf: list[str] = []
        self._skip: str | None = None
        self._skip_depth = 0
        self._in_title = False

    def _flush(self):
        text = _WS.sub(" ", "".join(self._buf)).strip()
        if text:
            self.paragraphs.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if self._skip:
            if tag == self._skip:
                self._skip_depth += 1
        elif tag in SKIP_TAGS:
            self._flush()
            self._skip, self._skip_depth = tag, 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        if not self._skip and tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if self._skip:
            if tag == self._skip:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip = None
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        (self.title_parts if self._in_title else self._buf).append(data)

    def close(self):
        super().close()
        self._flush()


def extract_html(markup: str) -> tuple[str | None, str]:
    """Return (title, snapshot) for an HTML document."""
    parser = _Extractor()
    parser.feed(markup)
    parser.close()
    title = _WS.sub(" ", "".join(parser.title_parts)).strip() or None
    return title, "\n\n".join(parser.paragraphs)


def extract_plain(text: str) -> tuple[str | None, str]:
    """Return (None, snapshot) for plain text: normalised newlines, blank-line paragraphs preserved."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return None, "\n\n".join(paragraphs)


def decode_body(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    for encoding in ([match.group(1)] if match else []) + ["utf-8"]:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def extract(body: bytes, content_type: str) -> tuple[str | None, str]:
    text = decode_body(body, content_type)
    if content_type.split(";")[0].strip().lower() == "text/plain":
        return extract_plain(text)
    return extract_html(text)


def _spans(text: str, pattern: str) -> list[tuple[int, int]]:
    """Non-empty spans of `text` between matches of `pattern`."""
    spans, pos = [], 0
    for m in re.finditer(pattern, text):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    return spans


def _hard_split(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    out = []
    while end - start > limit:
        cut = text.rfind(" ", start, start + limit)
        if cut <= start:
            cut = start + limit
        out.append((start, cut))
        start = cut
        while start < end and text[start] == " ":
            start += 1
    if start < end:
        out.append((start, end))
    return out


def split_passages(snapshot: str, target_chars: int, max_chars: int) -> list[tuple[int, int]]:
    """Split the snapshot into (start, end) spans, each at most max_chars, packed up to target_chars."""
    units: list[tuple[int, int]] = []
    for p_start, p_end in _spans(snapshot, r"\n\n+"):
        if p_end - p_start <= max_chars:
            units.append((p_start, p_end))
            continue
        for s_start, s_end in _spans(snapshot[p_start:p_end], r"(?<=[.!?])\s+"):
            units.extend(_hard_split(snapshot, p_start + s_start, p_start + s_end, max_chars))
    passages: list[tuple[int, int]] = []
    for start, end in units:
        if passages and end - passages[-1][0] <= target_chars:
            passages[-1] = (passages[-1][0], end)
        else:
            passages.append((start, end))
    return passages
