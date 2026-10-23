from backend.extract import extract, extract_html, split_passages

HTML = """<html><head><title> My  Page </title><style>p{color:red}</style><script>var x = "secret";</script></head>
<body><nav>Menu Home About</nav><h1>Heading</h1><p>First   paragraph
with <b>bold</b> text &amp; entity.</p><div>Second<br>line</div>
<aside>Ad block</aside><footer>Copyright</footer></body></html>"""


def test_html_extraction_skips_boilerplate():
    title, snap = extract_html(HTML)
    assert title == "My Page"
    assert snap == "Heading\n\nFirst paragraph with bold text & entity.\n\nSecond\n\nline"
    for junk in ("secret", "Menu", "color:red", "Ad block", "Copyright"):
        assert junk not in snap


def test_plain_text_and_charset():
    title, snap = extract(b"Para one.\r\n\r\n\r\nPara two.", "text/plain; charset=utf-8")
    assert (title, snap) == (None, "Para one.\n\nPara two.")
    _, snap = extract("café".encode("latin-1"), "text/html; charset=latin-1")
    assert snap == "café"


def test_passages_are_exact_slices_within_limits():
    sentences = [f"Sentence number {i} says something specific." for i in range(40)]
    snapshot = "Short intro.\n\n" + " ".join(sentences) + "\n\n" + "x" * 1200 + "\n\nEnd."
    spans = split_passages(snapshot, target_chars=300, max_chars=500)
    assert spans == sorted(spans)
    for start, end in spans:
        text = snapshot[start:end]
        assert text == text.strip() and 0 < len(text) <= 500
    covered = "".join(snapshot[s:e] for s, e in spans).replace(" ", "").replace("\n", "")
    assert covered == snapshot.replace(" ", "").replace("\n", "")  # nothing dropped or invented


def test_short_paragraphs_are_packed_with_context():
    snapshot = "One.\n\nTwo.\n\nThree."
    assert split_passages(snapshot, 300, 500) == [(0, len(snapshot))]


def test_long_paragraph_splits_on_sentence_boundaries():
    snapshot = " ".join(f"Sentence {i:02d} ends here." for i in range(30))
    for start, end in split_passages(snapshot, 100, 150):
        assert snapshot[start:end].endswith(".")
