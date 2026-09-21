"""Pooling coverage for a re-measurement: how many (question, chunk) pairs inside the reported top-k were never
relevance-judged (they are scored as non-relevant, so the metrics are a lower bound). No LLM calls."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("ev", ROOT / "eval" / "run_embedding_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

from rag.retriever import rrf  # noqa: E402


def main(k: int = 5) -> dict:
    ids = [c.chunk_id for c in ev.load_or_build_chunks()]
    allq = ev._rows(ev.CAND)
    qidx = {r["qid"]: i for i, r in enumerate(allq)}
    fin = ev._rows(ev.FINAL)
    sel = [qidx[r["qid"]] for r in fin]
    judged = {(o["qid"], o["chunk_id"]) for o in ev._rows(ev.POOL)} | {(r["qid"], r["gold_chunk"]) for r in fin}

    def count(rankings):
        return sum((r["qid"], ids[j]) not in judged for r, row in zip(fin, rankings) for j in list(row)[:k])

    out = {f"unjudged_in_top{k}": {m: count(ev._dense_rank(m)[sel]) for m in ev.CANDIDATES}}
    d_ko, d_en = ev._dense_rank("bge-m3")[sel], ev._dense_rank("bge-m3", "qen")[sel]
    bm = ev._bm25_rank([allq[i]["query_en"] for i in sel])
    out[f"unjudged_in_top{k}"]["hybrid3 (bge-m3)"] = count(
        [rrf([list(a[:20]), list(c[:20]), list(b[:20])]) for a, b, c in zip(d_ko, bm, d_en)])
    for tag in ("2way", "3way"):
        f = ev.OUT / f"reranked_{tag}.json"
        if f.exists():
            rr = json.loads(f.read_text())
            out[f"unjudged_in_top{k}"][f"hybrid {tag} + rerank"] = sum(
                (r["qid"], c) not in judged for r in fin for c in rr[r["qid"]][:k])
    out["n_questions"] = len(fin)
    out["variant"] = ev.VARIANT
    (ev.OUT / "pooling_coverage.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main()
