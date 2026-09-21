"""Section-aware token chunking with metadata for filtered retrieval."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import tiktoken
import yaml

from rag.loader import PAPER_DIR, ROOT, SECTION_MARK, load_pdf

CHUNKS_PATH = ROOT / "data" / "index" / "chunks.jsonl"
_ENC = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str          # arXiv id
    tech: str            # turboquant / itme / kivi / mla / infinigen / cxl_pnm
    camp: str            # SW / HW
    role: str            # primary / baseline / alternative
    title: str
    section: str
    page: int            # first page of the chunk (1-based)
    page_end: int
    published_at: str
    source_type: str     # paper
    text: str


def _ntok(s: str) -> int:
    return len(_ENC.encode(s))


def _split_units(text: str) -> list[str]:
    """Paragraphs first, sentences if a paragraph is too long."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if _ntok(para) <= 400:
            units.append(para)
        else:
            for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", para):
                toks = _ENC.encode(sent)
                # hard split pathological units (tables, formula dumps) so no chunk exceeds the budget
                units.extend(_ENC.decode(toks[i:i + 400]) for i in range(0, len(toks), 400) if sent.strip())
    return units


def build_chunks(cfg: dict) -> list[Chunk]:
    size = cfg["chunking"]["chunk_tokens"]
    overlap = int(size * cfg["chunking"]["overlap_ratio"])
    published = cfg["corpus"].get("published", {})
    chunks: list[Chunk] = []
    for paper in cfg["corpus"]["papers"]:
        pages = load_pdf(PAPER_DIR / f"{paper['arxiv']}.pdf", paper["arxiv"])
        # (unit, page, section) stream; a new section forces a chunk boundary.
        stream: list[tuple[str, int, str]] = []
        current = pages[0].section if pages else "Front matter"
        for p in pages:
            for u in _split_units(p.text):
                if u.startswith(SECTION_MARK):
                    current = u[len(SECTION_MARK):].strip()
                    continue
                stream.append((u, p.page, current))
        buf: list[tuple[str, int, str]] = []
        buf_tok = 0

        def flush():
            nonlocal buf, buf_tok
            if not buf:
                return
            text = " ".join(u for u, _, _ in buf)
            if _ntok(text) >= 60:
                cid = f"{paper['tech']}-{len([c for c in chunks if c.doc_id == paper['arxiv']]):03d}"
                chunks.append(Chunk(
                    chunk_id=cid, doc_id=paper["arxiv"], tech=paper["tech"], camp=paper["camp"],
                    role=paper["role"], title=paper["title"], section=buf[0][2], page=buf[0][1],
                    page_end=buf[-1][1], published_at=published.get(paper["arxiv"], ""),
                    source_type="paper", text=text,
                ))
            # keep tail units as overlap
            tail, tok = [], 0
            for u in reversed(buf):
                t = _ntok(u[0])
                if tok + t > overlap:
                    break
                tail.insert(0, u)
                tok += t
            buf, buf_tok = tail, tok

        prev_section = None
        for unit, page, section in stream:
            if prev_section is not None and section != prev_section and buf_tok > size * 0.3:
                flush()
                buf, buf_tok = [], 0  # no overlap across section boundaries
            t = _ntok(unit)
            if buf_tok + t > size and buf:
                flush()
            buf.append((unit, page, section))
            buf_tok += t
            prev_section = section
        flush()
    return chunks


def load_or_build_chunks(cfg: dict | None = None, rebuild: bool = False) -> list[Chunk]:
    cfg = cfg or yaml.safe_load((ROOT / "config.yaml").read_text())
    if CHUNKS_PATH.exists() and not rebuild:
        return [Chunk(**json.loads(l)) for l in CHUNKS_PATH.read_text().splitlines() if l.strip()]
    chunks = build_chunks(cfg)
    CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHUNKS_PATH.write_text("\n".join(json.dumps(asdict(c), ensure_ascii=False) for c in chunks))
    return chunks


def corpus_fingerprint(chunks: list[Chunk]) -> str:
    h = hashlib.sha1()
    for c in chunks:
        h.update(c.chunk_id.encode())
        h.update(c.text.encode())
    return h.hexdigest()[:12]


def embed_text(c: Chunk) -> str:
    """Text used for indexing: a short contextual header (title/section) + body."""
    return f"{c.title} | {c.section}\n{c.text}"
