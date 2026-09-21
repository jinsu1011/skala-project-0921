"""Measurement-condition check: model-native max input (model card/config) vs the max_seq_length used in the
embedding eval, and how many indexed chunks exceeded it (i.e. were truncated when the reported numbers were measured).
No retrieval metric is recomputed here."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.chunker import embed_text, load_or_build_chunks  # noqa: E402
from rag.embedder import CANDIDATES  # noqa: E402

# model-native maximum input length, read from each model's config / sentence-transformers config (2026-09-21)
NATIVE_MAX = {
    "bge-m3": 8192,                   # sentence_bert_config.max_seq_length, max_position_embeddings 8194
    "multilingual-e5-large": 512,     # max_position_embeddings 514, model_max_length 512
    "qwen3-embedding-0.6b": 32768,    # max_position_embeddings 32768
    "multilingual-minilm-l12": 128,   # sentence_bert_config.max_seq_length 128 (trained length)
}


def main() -> None:
    from transformers import AutoTokenizer

    texts = [embed_text(c) for c in load_or_build_chunks()]
    rows = []
    for key, spec in CANDIDATES.items():
        tok = AutoTokenizer.from_pretrained(spec.hf_id)
        lens = [len(tok(spec.doc_prefix + t)["input_ids"]) for t in texts]
        rows.append({"model": key, "native_max": NATIVE_MAX[key], "eval_max_seq_length": spec.max_seq_length,
                     "chunk_tokens_max": max(lens), "chunk_tokens_mean": round(sum(lens) / len(lens)),
                     "truncated_chunks": sum(l > spec.max_seq_length for l in lens), "n_chunks": len(lens)})
    df = pd.DataFrame(rows)
    out = ROOT / "outputs" / "eval" / "truncation_check.csv"
    df.to_csv(out, index=False)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
