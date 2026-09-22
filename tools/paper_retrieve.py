"""paper_retrieve tool (B.7): hybrid search over the Doc Pool, returning Evidence with P:<chunk_id> ids."""
from __future__ import annotations

import threading
from typing import Optional

from langchain_core.tools import tool

from graph.runtime import config, llm_text, rt
from graph.state import Evidence
from tools.evidence import paper_id, paper_origin

# same question-only rewrite prompt as eval/build_qa.py (REWRITE_SYS): the answer text never leaks into the query
REWRITE_SYS = """Rewrite the Korean question into a concise English search query for retrieving passages from English
research papers on LLM KV-cache optimization. Keep technique names and technical terms; translate the information need;
do not add facts, numbers, or terms that are not implied by the question. Return only the query."""


def to_english(query: str) -> str:
    if query.isascii():
        return query
    return llm_text("generator", REWRITE_SYS, query, tag="query_en").strip().strip('"')


_SEARCH_LOCK = threading.Lock()  # the embedding/reranker models are not thread-safe on MPS: one search at a time


def search_papers(query: str, camp: Optional[str] = None, tech: Optional[str] = None, role: Optional[str] = None,
                  k: Optional[int] = None, query_en: Optional[str] = None) -> list[Evidence]:
    cfg = config()["retrieval"]
    k = k or cfg["top_k"]
    q_en = query_en or to_english(query)
    with _SEARCH_LOCK:
        hits = rt().retriever().search(query, k=k, query_en=q_en, camp=camp, tech=tech, role=role,
                                       pool=cfg["rerank_pool"])
    out = []
    for h in hits:
        c = h.chunk
        group, cls, origin = paper_origin(c.tech, c.role, c.doc_id)
        out.append(Evidence(
            evidence_id=paper_id(c.chunk_id), kind="paper", summary=c.text[:1200], doc_id=c.doc_id, title=c.title,
            publisher="arXiv", published_at=c.published_at, page=c.page, section=c.section, tech=c.tech,
            source_url=f"https://arxiv.org/abs/{c.doc_id}", scope="tech_specific", source_class=cls,
            source_group=group, origin_group=origin))
    return out


@tool
def paper_retrieve(query: str, camp: Optional[str] = None, tech: Optional[str] = None, role: Optional[str] = None,
                   k: int = 5) -> list[dict]:
    """Search the six Doc Pool papers with 3-way hybrid retrieval (Korean dense + English dense + English BM25, RRF k=60)
    and bge-reranker-v2-m3. Filters: camp (SW|HW), tech (turboquant|itme|kivi|mla|infinigen|cxl_pnm),
    role (primary|baseline|alternative). Returns evidence dicts with ids 'P:<chunk_id>', page and section."""
    return [e.model_dump() for e in search_papers(query, camp, tech, role, k)]
