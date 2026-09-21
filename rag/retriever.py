"""Hybrid retriever: FAISS dense + BM25 sparse, fused with Reciprocal Rank Fusion, metadata filters, optional reranker."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from rag.chunker import Chunk, corpus_fingerprint, embed_text, load_or_build_chunks
from rag.embedder import Embedder
from rag.loader import ROOT

INDEX_DIR = ROOT / "data" / "index"
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]+|\d+(?:\.\d+)?|[가-힣]+")
_STOP = set("the a an of and or to in on for with by is are was were be as at from that this these those it its we our "
            "which can than into over under via using use used".split())


def bm25_tokenize(text: str) -> list[str]:
    return [t for t in (m.lower() for m in _TOKEN_RE.findall(text)) if t not in _STOP]


def rrf(rankings: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


@dataclass
class Hit:
    chunk: Chunk
    score: float
    rank: int


class HybridRetriever:
    """Auto-builds the FAISS index if missing, reuses it if the corpus fingerprint matches."""

    def __init__(self, model_key: str, rrf_k: int = 60, reranker: str | None = None, rebuild: bool = False):
        self.chunks = load_or_build_chunks(rebuild=rebuild)
        self.embedder = Embedder(model_key)
        self.rrf_k = rrf_k
        self.index = self._load_or_build_index(model_key, rebuild)
        self.bm25 = BM25Okapi([bm25_tokenize(embed_text(c)) for c in self.chunks])
        self.reranker = None
        if reranker:
            from sentence_transformers import CrossEncoder

            self.reranker = CrossEncoder(reranker, max_length=1024)

    def _load_or_build_index(self, model_key: str, rebuild: bool) -> faiss.Index:
        d = INDEX_DIR / model_key
        fp = corpus_fingerprint(self.chunks)
        meta = d / "meta.json"
        if not rebuild and meta.exists() and json.loads(meta.read_text()).get("fingerprint") == fp:
            return faiss.read_index(str(d / "faiss.index"))
        vecs = self.embedder.embed_documents([embed_text(c) for c in self.chunks])
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        d.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(d / "faiss.index"))
        meta.write_text(json.dumps({"fingerprint": fp, "n": len(self.chunks), "dim": int(vecs.shape[1])}))
        return index

    def _mask(self, camp: str | None, tech: str | None, role: str | None) -> np.ndarray:
        return np.array([(camp is None or c.camp == camp) and (tech is None or c.tech == tech)
                         and (role is None or c.role == role) for c in self.chunks])

    def dense(self, query: str, n: int, mask: np.ndarray) -> list[int]:
        q = self.embedder.embed_queries([query])
        scores, ids = self.index.search(q, len(self.chunks))
        return [int(i) for i in ids[0] if mask[i]][:n]

    def sparse(self, query: str, n: int, mask: np.ndarray) -> list[int]:
        s = self.bm25.get_scores(bm25_tokenize(query))
        s = np.where(mask, s, -np.inf)
        return [int(i) for i in np.argsort(-s)[:n] if np.isfinite(s[i]) and s[i] > 0]

    def search(self, query: str, k: int = 5, *, query_en: str | None = None, mode: str = "hybrid",
               camp: str | None = None, tech: str | None = None, role: str | None = None,
               pool: int = 20) -> list[Hit]:
        """mode: dense | sparse | hybrid. `query_en` (English rewrite) feeds BM25 and a second dense ranking."""
        mask = self._mask(camp, tech, role)
        rankings: list[list[int]] = []
        if mode in ("dense", "hybrid"):
            rankings.append(self.dense(query, pool, mask))
        if mode in ("sparse", "hybrid"):
            rankings.append(self.sparse(query_en or query, pool, mask))
        if mode == "hybrid" and query_en:
            rankings.append(self.dense(query_en, pool, mask))
        fused = rrf(rankings, self.rrf_k) if len(rankings) > 1 else rankings[0]
        fused = fused[:pool]
        if self.reranker is not None and fused:
            pairs = [(query_en or query, embed_text(self.chunks[i])) for i in fused]
            rs = self.reranker.predict(pairs, show_progress_bar=False)
            fused = [fused[j] for j in np.argsort(-np.asarray(rs))]
        return [Hit(self.chunks[i], 1.0 / (r + 1), r) for r, i in enumerate(fused[:k])]
