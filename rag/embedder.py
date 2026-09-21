"""Open-source embedding model registry (candidates evaluated in eval/run_embedding_eval.py)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Qwen3 prompt sensitivity (42-question set, MRR@10): custom Korean->English instruction 0.548,
# no prompt 0.607, model-card default "query" prompt 0.631 -> the default is used (best setting per candidate).


@dataclass(frozen=True)
class EmbeddingSpec:
    key: str
    hf_id: str
    license: str
    params: str
    query_prefix: str = ""
    doc_prefix: str = ""
    query_prompt_name: str | None = None  # model-card prompt applied to queries only (Qwen3: "query")
    max_seq_length: int = 1024


CANDIDATES: dict[str, EmbeddingSpec] = {
    "bge-m3": EmbeddingSpec("bge-m3", "BAAI/bge-m3", "MIT", "568M"),
    "multilingual-e5-large": EmbeddingSpec(
        "multilingual-e5-large", "intfloat/multilingual-e5-large", "MIT", "560M",
        query_prefix="query: ", doc_prefix="passage: ", max_seq_length=512),
    "qwen3-embedding-0.6b": EmbeddingSpec(
        "qwen3-embedding-0.6b", "Qwen/Qwen3-Embedding-0.6B", "Apache-2.0", "596M", query_prompt_name="query"),
    "multilingual-minilm-l12": EmbeddingSpec(
        "multilingual-minilm-l12", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "Apache-2.0", "118M", max_seq_length=128),
}


# model-native maximum input (model card / config, checked 2026-09-21)
NATIVE_MAX = {"bge-m3": 8192, "multilingual-e5-large": 512, "qwen3-embedding-0.6b": 32768, "multilingual-minilm-l12": 128}
RUNTIME_MAX_SEQ = 2048  # implementation setting (covers the longest chunk, 1,529 model tokens)


def _device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")


class Embedder:
    def __init__(self, key: str, max_len: int | None = None):
        """max_len overrides the eval-v1 setting but never exceeds the model-native limit (NATIVE_MAX)."""
        from sentence_transformers import SentenceTransformer

        self.spec = CANDIDATES[key]
        self.model = SentenceTransformer(self.spec.hf_id, device=_device())
        want = self.spec.max_seq_length if max_len is None else min(max_len, NATIVE_MAX[key])
        self.model.max_seq_length = want

    def embed_documents(self, texts: list[str], batch_size: int = 8) -> np.ndarray:
        texts = [self.spec.doc_prefix + t for t in texts]
        return self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False).astype("float32")

    def embed_queries(self, queries: list[str]) -> np.ndarray:
        kw = {"prompt_name": self.spec.query_prompt_name} if self.spec.query_prompt_name else {}
        queries = [self.spec.query_prefix + q for q in queries]
        return self.model.encode(queries, batch_size=16, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False, **kw).astype("float32")
