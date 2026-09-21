"""PDF loading: page text extraction, header/footer removal, section tracking, reference cut-off."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PAPER_DIR = ROOT / "data" / "papers"

# Numbered headings such as "3 Method", "4.2 Needle-In-A-Haystack", "III. DESIGN", or common unnumbered ones.
_HEADING_RE = re.compile(
    r"^(?:(?:\d{1,2}(?:\.\d{1,2}){0,2})|(?:[IVX]{1,4}\.))\s+[A-Z][A-Za-z0-9\-–:,()/ &']{2,80}$"
)
_UNNUMBERED = {"abstract", "introduction", "conclusion", "conclusions", "related work", "background",
               "evaluation", "discussion", "acknowledgments", "acknowledgements", "appendix"}
SECTION_MARK = "§§ "  # in-text marker so the chunker can track section changes mid-page
_REFERENCES_RE = re.compile(r"^(?:\d+\s+|[IVX]+\.\s+)?(references|bibliography)$", re.I)


@dataclass
class PageText:
    doc_id: str
    page: int  # 1-based
    section: str
    text: str


def _is_heading(line: str) -> bool:
    s = line.strip()
    if len(s) < 4 or len(s) > 90:
        return False
    if s.lower() in _UNNUMBERED:
        return True
    return bool(_HEADING_RE.match(s)) and not s.endswith(".")


def _line_iter(doc):
    """Yield (page_no, size, bold, text) for every text line, plus the body font size."""
    lines, sizes = [], Counter()
    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line["spans"]
                text = "".join(sp["text"] for sp in spans).strip()
                if not text:
                    continue
                size = round(max(sp["size"] for sp in spans), 1)
                bold = any("Bold" in sp["font"] or sp["flags"] & 16 for sp in spans)
                sizes[size] += len(text)
                lines.append((pno, size, bold, text))
    body = sizes.most_common(1)[0][0]
    return lines, body


def _is_font_heading(size: float, bold: bool, text: str, body: float) -> bool:
    if not bold or size < body + 0.8 or size >= 16:
        return False
    if not (3 <= len(text) <= 80) or not re.match(r"^[A-Z0-9]", text):
        return False
    return not re.match(r"^(Figure|Table|Fig\.|Algorithm|arXiv)", text)


def load_pdf(path: Path, doc_id: str) -> list[PageText]:
    doc = pymupdf.open(path)
    lines, body = _line_iter(doc)

    # Lines repeated on more than 40% of pages are running headers/footers.
    per_page: dict[int, set[str]] = {}
    for pno, _, _, t in lines:
        per_page.setdefault(pno, set()).add(t)
    counts = Counter(t for s in per_page.values() for t in s)
    threshold = max(3, int(0.4 * doc.page_count))
    boiler = {t for t, c in counts.items() if c >= threshold and len(t) < 120}

    pages: list[PageText] = []
    section, in_refs = "Front matter", False
    buf: dict[int, list[str]] = {}
    first_section: dict[int, str] = {}
    for pno, size, bold, t in lines:
        if t in boiler or re.fullmatch(r"\d{1,3}", t) or t.startswith("arXiv:"):
            continue
        heading = _is_font_heading(size, bold, t, body) or (_is_heading(t) and bold)
        if heading and _REFERENCES_RE.match(t.strip()):
            in_refs = True
            continue
        if in_refs:
            if heading and re.match(r"^(Appendix|[A-H]\s|[A-H]\.\d?\s)", t):
                in_refs = False
            else:
                continue
        if heading:
            section = re.sub(r"^\d+(\.\d+)*\s+", "", t).strip()
            buf.setdefault(pno, []).append("\n\n" + SECTION_MARK + section + "\n\n")
            first_section.setdefault(pno, section)
            continue
        first_section.setdefault(pno, section)
        buf.setdefault(pno, []).append(t)
    for pno in sorted(buf):
        text = _rejoin(buf[pno])
        if len(text) > 200:
            pages.append(PageText(doc_id=doc_id, page=pno, section=first_section[pno], text=text))
    return pages


def _rejoin(lines: list[str]) -> str:
    """Join PDF lines into paragraphs; fix hyphenation at line ends."""
    out = ""
    for ln in lines:
        if ln.startswith("\n\n"):
            out += ln
        elif out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln
        else:
            out += (" " if out and not out.endswith("\n") else "") + ln
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", out)).strip()
