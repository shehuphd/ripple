"""Shared fixtures.

Parser tests run against the rendered demo corpus rather than hand-built
strings. A string written to match the parser's own assumptions proves the
parser agrees with itself; the corpus is what a user actually uploads.

Hostile artifacts are generated here rather than committed, so the repository
never carries an XML bomb or a malformed PDF as a tracked file.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEMO_SCRIPTS = REPO / "demo-scripts"

SCRIPTS = ("01-night-freight/night-freight", "02-the-understudy/the-understudy")


def _read(relative: str) -> bytes:
    path = DEMO_SCRIPTS / relative
    if not path.exists():
        pytest.skip(f"fixture missing: {path.relative_to(REPO)}; run the renderer")
    return path.read_bytes()


@pytest.fixture(params=SCRIPTS, ids=lambda value: value.split("/")[0])
def corpus_stem(request) -> str:
    """Each demo script in turn, as a path stem under demo-scripts/."""
    return request.param


@pytest.fixture
def fountain_bytes(corpus_stem: str) -> bytes:
    return _read(f"{corpus_stem}.fountain")


@pytest.fixture
def fdx_bytes(corpus_stem: str) -> bytes:
    return _read(f"{corpus_stem}.fdx")


@pytest.fixture
def pdf_bytes(corpus_stem: str) -> bytes:
    return _read(f"{corpus_stem}.pdf")


@pytest.fixture
def text_bytes(corpus_stem: str) -> bytes:
    return _read(f"{corpus_stem}.txt")


@pytest.fixture
def night_freight_fountain() -> bytes:
    """The first demo script, for assertions about specific known content."""
    return _read("01-night-freight/night-freight.fountain")


@pytest.fixture
def understudy_fountain() -> bytes:
    return _read("02-the-understudy/the-understudy.fountain")


@pytest.fixture
def understudy_fdx() -> bytes:
    return _read("02-the-understudy/the-understudy.fdx")


# Hostile payloads, generated rather than stored.

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE FinalDraft [
  <!ENTITY a "aaaaaaaaaa">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
]>
<FinalDraft><Content><Paragraph Type="Action"><Text>&d;</Text></Paragraph></Content></FinalDraft>
"""

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE FinalDraft [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<FinalDraft><Content><Paragraph Type="Action"><Text>&xxe;</Text></Paragraph></Content></FinalDraft>
"""

PROSE = (
    "It was a bright cold day in April, and the clocks were striking thirteen. "
    "Winston Smith, his chin nuzzled into his breast in an effort to escape the "
    "vile wind, slipped quickly through the glass doors of Victory Mansions, "
    "though not quickly enough to prevent a swirl of gritty dust from entering "
    "along with him.\n\n"
) * 12

INVOICE = (
    "INVOICE 2026-0041\nBill to: Albion Freight Ltd\n\n"
    "Line item,Quantity,Unit price,Total\n"
    "Pallet jack hire,2,45.00,90.00\n"
    "Forklift service,1,320.00,320.00\n\n"
    "Subtotal 410.00\nVAT 82.00\nTotal due 492.00\n"
)


@pytest.fixture
def image_only_pdf() -> bytes:
    """A PDF with drawn shapes and no text layer."""
    reportlab_canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    buffer = io.BytesIO()
    pdf = reportlab_canvas.Canvas(buffer)
    for page in range(2):
        pdf.rect(72, 72 + page * 10, 400, 600, fill=1)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def encrypted_pdf(night_freight_fountain: bytes) -> bytes:
    """A password-protected copy of a demo script's PDF."""
    pypdf = pytest.importorskip("pypdf")
    source = DEMO_SCRIPTS / "01-night-freight/night-freight.pdf"
    if not source.exists():
        pytest.skip("rendered PDF missing; run the renderer")
    reader = pypdf.PdfReader(str(source))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("correct-horse-battery-staple")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _no_trace_files(tmp_path, monkeypatch):
    """Route tracing to a temp directory so a test run writes nothing durable."""
    monkeypatch.setenv("RIPPLE_TRACING", "off")
    from ripple import tracing

    monkeypatch.setattr(tracing, "DEFAULT_TRACE_DIR", tmp_path / "traces")
    monkeypatch.setattr(tracing, "_configured", False)
    yield
