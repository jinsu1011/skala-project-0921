"""Embedding & retrieval-configuration evaluation on the corpus-specific Korean->English eval set.

Stages (run in order; each is cached):
  encode  : for each candidate model (separate subprocess) encode corpus + queries; record index time, peak memory
  pool    : pooled relevance judging -> multi-gold (union of top-10 from every model + BM25, judged by the judge LLM)
  select  : pick the final balanced question set (same count per paper, SW == HW)
  score   : Hit Rate@1/3/5, MRR@10 per model; then retrieval configs with the chosen model; CSV + charts
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.chunker import embed_text, load_or_build_chunks  # noqa: E402
from rag.embedder import CANDIDATES  # noqa: E402

DATA = ROOT / "eval" / "data"
EMB = ROOT / "data" / "index" / "eval_embeddings"
OUT = ROOT / "outputs" / "eval"
CAND = DATA / "qa_candidates.jsonl"
FINAL = DATA / "qa_eval_set.jsonl"
POOL = DATA / "pool_judgments.jsonl"
PER_PAPER = 7
RERANKER = "BAAI/bge-reranker-v2-m3"


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------- encode
def encode_one(key: str) -> None:
    """Runs inside a fresh subprocess so memory numbers are per-model."""
    import psutil
    import torch

    from rag.embedder import Embedder

    proc = psutil.Process()
    peak = {"rss": proc.memory_info().rss}
    stop = False

    def watch():
        while not stop:
            peak["rss"] = max(peak["rss"], proc.memory_info().rss)
            time.sleep(0.05)

    threading.Thread(target=watch, daemon=True).start()
    base_rss = proc.memory_info().rss
    t0 = time.perf_counter()
    emb = Embedder(key)
    load_s = time.perf_counter() - t0

    chunks = load_or_build_chunks()
    t0 = time.perf_counter()
    doc = emb.embed_documents([embed_text(c) for c in chunks])
    index_s = time.perf_counter() - t0

    qs = _rows(CAND)
    t0 = time.perf_counter()
    q = emb.embed_queries([r["question"] for r in qs])
    q_ms = (time.perf_counter() - t0) / len(qs) * 1000
    qen = emb.embed_queries([r["query_en"] for r in qs])
    stop = True
    mps_mb = torch.mps.driver_allocated_memory() / 2**20 if torch.backends.mps.is_available() else 0.0

    EMB.mkdir(parents=True, exist_ok=True)
    np.save(EMB / f"{key}_doc.npy", doc)
    np.save(EMB / f"{key}_q.npy", q)
    np.save(EMB / f"{key}_qen.npy", qen)
    perf = {"model": key, "hf_id": emb.spec.hf_id, "dim": int(doc.shape[1]), "max_seq_length": emb.model.max_seq_length,
            "load_s": round(load_s, 1), "index_s": round(index_s, 1), "query_ms": round(q_ms, 1),
            "peak_rss_mb": round((peak["rss"] - base_rss) / 2**20), "mps_alloc_mb": round(mps_mb),
            "index_mb": round(doc.nbytes / 2**20, 2), "n_chunks": len(chunks)}
    (EMB / f"{key}_perf.json").write_text(json.dumps(perf))
    print(perf)


def encode(models: list[str]) -> None:
    for key in models:
        if (EMB / f"{key}_perf.json").exists():
            print(f"skip {key} (cached)")
            continue
        subprocess.run([sys.executable, __file__, "encode-one", key], check=True,
                       env={**os.environ, "TOKENIZERS_PARALLELISM": "false"})


# ---------------------------------------------------------------- helpers
def _dense_rank(key: str, which: str = "q") -> np.ndarray:
    doc, q = np.load(EMB / f"{key}_doc.npy"), np.load(EMB / f"{key}_{which}.npy")
    return np.argsort(-(q @ doc.T), axis=1)


def _bm25_rank(queries: list[str]) -> np.ndarray:
    from rank_bm25 import BM25Okapi

    from rag.retriever import bm25_tokenize

    chunks = load_or_build_chunks()
    bm = BM25Okapi([bm25_tokenize(embed_text(c)) for c in chunks])
    return np.array([np.argsort(-bm.get_scores(bm25_tokenize(q))) for q in queries])


# ---------------------------------------------------------------- pool (multi-gold)
JUDGE_SYS = """You judge retrieval relevance strictly. You get a Korean question, its reference answer, and ONE candidate
English passage. Mark relevant=true ONLY if the candidate passage itself states the key information of the reference
answer (the same specific fact/number/mechanism), so a reader could answer the question from this passage alone.
Same topic, same paper, or a vaguer mention is NOT enough.
Return JSON only: {"relevant": true/false, "reason": "<=15 words"}"""


def pool() -> None:
    from langchain_core.messages import HumanMessage, SystemMessage

    from eval.build_qa import _json
    from rag.llm import get_llm

    chunks = load_or_build_chunks()
    qs = _rows(CAND)
    ranks = {k: _dense_rank(k) for k in CANDIDATES}
    bm = _bm25_rank([r["query_en"] for r in qs])
    llm = get_llm("judge")
    jobs = []
    for i, r in enumerate(qs):
        if not r["qc"]["pass"]:
            continue
        cand = {int(j) for k in ranks for j in ranks[k][i][:10]} | {int(j) for j in bm[i][:10]}
        cand.discard(next(n for n, c in enumerate(chunks) if c.chunk_id == r["gold_chunk"]))
        jobs += [(r, chunks[j]) for j in sorted(cand)]

    def one(job):
        r, c = job
        d = _json(llm.invoke([SystemMessage(JUDGE_SYS),
                              HumanMessage(f"[question]\n{r['question']}\n[reference answer]\n{r.get('answer', '')}\n"
                                           f"[candidate passage]\n{c.text}")]).content)
        return {"qid": r["qid"], "chunk_id": c.chunk_id, "relevant": bool(d.get("relevant")), "reason": d.get("reason", "")}

    with ThreadPoolExecutor(8) as ex:
        out = list(ex.map(one, jobs))
    POOL.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in out))
    print(f"judged {len(out)} pooled candidates; relevant extra {sum(o['relevant'] for o in out)}")


# ---------------------------------------------------------------- select
def select(max_gold: int = 4) -> None:
    qs = [r for r in _rows(CAND) if r["qc"]["pass"]]
    extra = defaultdict(list)
    for o in _rows(POOL):
        if o["relevant"]:
            extra[o["qid"]].append(o["chunk_id"])
    fixes = json.loads((DATA / "qa_manual_fixes.json").read_text()) if (DATA / "qa_manual_fixes.json").exists() else {}
    for qid, cids in fixes.get("remove_gold", {}).items():
        extra[qid] = [c for c in extra[qid] if c not in cids]
    qs = [r for r in qs if r["qid"] not in fixes.get("drop_question", {})]
    final, dropped = [], []
    for tech in dict.fromkeys(r["tech"] for r in qs):
        kept = 0
        for r in (x for x in qs if x["tech"] == tech):
            golds = [r["gold_chunk"]] + sorted(extra[r["qid"]])
            if len(golds) > max_gold:  # too generic: many chunks answer it -> drop rather than mis-score
                dropped.append({"qid": r["qid"], "n_gold": len(golds)})
                continue
            if kept < PER_PAPER:
                final.append({**r, "gold_chunks": golds})
                kept += 1
    FINAL.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in final))
    c = Counter(r["tech"] for r in final)
    print(f"final {len(final)} questions per paper {dict(c)}; SW {sum(r['camp']=='SW' for r in final)} "
          f"HW {sum(r['camp']=='HW' for r in final)}; multi-gold {sum(len(r['gold_chunks'])>1 for r in final)}; "
          f"dropped(too generic) {dropped}")


# ---------------------------------------------------------------- score
def _metrics(ranked_ids: list[list[str]], golds: list[list[str]]) -> dict:
    def first_hit(rk, g):
        return next((i for i, cid in enumerate(rk[:10]) if cid in g), None)

    pos = [first_hit(rk, set(g)) for rk, g in zip(ranked_ids, golds)]
    return {f"hit@{k}": round(np.mean([p is not None and p < k for p in pos]), 3) for k in (1, 3, 5)} | {
        "mrr@10": round(np.mean([0 if p is None else 1 / (p + 1) for p in pos]), 3)}


def score() -> None:
    import pandas as pd

    from rag.retriever import rrf

    chunks = load_or_build_chunks()
    ids = [c.chunk_id for c in chunks]
    allq = _rows(CAND)
    qidx = {r["qid"]: i for i, r in enumerate(allq)}
    fin = _rows(FINAL)
    sel = [qidx[r["qid"]] for r in fin]
    golds = [r["gold_chunks"] for r in fin]
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) embedding models: dense-only, Korean query as-is (the agents' real condition)
    rows = []
    for key, spec in CANDIDATES.items():
        rk = _dense_rank(key)[sel]
        perf = json.loads((EMB / f"{key}_perf.json").read_text())
        m = _metrics([[ids[j] for j in r] for r in rk], golds)
        sw = [i for i, r in enumerate(fin) if r["camp"] == "SW"]
        hw = [i for i, r in enumerate(fin) if r["camp"] == "HW"]
        m_sw = _metrics([[ids[j] for j in rk[i]] for i in sw], [golds[i] for i in sw])
        m_hw = _metrics([[ids[j] for j in rk[i]] for i in hw], [golds[i] for i in hw])
        rows.append({"model": key, "hf_id": spec.hf_id, "license": spec.license, "params": spec.params, **m,
                     "mrr@10_SW": m_sw["mrr@10"], "mrr@10_HW": m_hw["mrr@10"], **{k: perf[k] for k in (
                         "dim", "max_seq_length", "load_s", "index_s", "query_ms", "peak_rss_mb", "mps_alloc_mb", "index_mb")}})
    df = pd.DataFrame(rows).sort_values("mrr@10", ascending=False)
    df.to_csv(OUT / "embedding_eval.csv", index=False)
    print(df.to_string(index=False))

    # 2) retrieval configurations with the chosen model
    best = df.iloc[0]["model"] if len(sys.argv) < 3 else sys.argv[2]
    d_ko = _dense_rank(best)[sel]
    d_en = _dense_rank(best, "qen")[sel]
    bm_ko = _bm25_rank([allq[i]["question"] for i in sel])
    bm_en = _bm25_rank([allq[i]["query_en"] for i in sel])
    configs = {
        "dense (KO query)": [list(r[:20]) for r in d_ko],
        "BM25 (KO query)": [list(r[:20]) for r in bm_ko],
        "BM25 (EN rewrite)": [list(r[:20]) for r in bm_en],
        "hybrid RRF: dense KO + BM25 EN": [rrf([list(a[:20]), list(b[:20])])[:20] for a, b in zip(d_ko, bm_en)],
        "hybrid RRF: dense KO + dense EN + BM25 EN": [rrf([list(a[:20]), list(c[:20]), list(b[:20])])[:20]
                                                      for a, b, c in zip(d_ko, bm_en, d_en)],
    }
    res = [{"config": n, "model": best, **_metrics([[ids[j] for j in r] for r in rk], golds)} for n, rk in configs.items()]

    # 3) + cross-encoder reranker on the best hybrid pool (Tier 3: kept only if it improves)
    try:
        from sentence_transformers import CrossEncoder

        ce = CrossEncoder(RERANKER, max_length=1024)
        base = max(res, key=lambda x: x["mrr@10"])["config"]
        reranked = []
        t0 = time.perf_counter()
        for qi, cand in zip(sel, configs[base]):
            s = ce.predict([(allq[qi]["question"], embed_text(chunks[j])) for j in cand], show_progress_bar=False)
            reranked.append([cand[j] for j in np.argsort(-s)])
        ms = (time.perf_counter() - t0) / len(sel) * 1000
        res.append({"config": f"{base} + rerank(bge-reranker-v2-m3)", "model": best,
                    **_metrics([[ids[j] for j in r] for r in reranked], golds), "rerank_ms_per_query": round(ms)})
    except Exception as e:  # reranker is optional
        print("reranker skipped:", e)
    rdf = pd.DataFrame(res)
    rdf.to_csv(OUT / "retrieval_config_eval.csv", index=False)
    print(rdf.to_string(index=False))
    _charts(df, rdf)


def _charts(df, rdf) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    x = np.arange(len(df))
    w = 0.2
    for i, m in enumerate(["hit@1", "hit@3", "hit@5", "mrr@10"]):
        axes[0].bar(x + (i - 1.5) * w, df[m], w, label=m)
    axes[0].set_xticks(x, df["model"], rotation=12, fontsize=8)
    axes[0].set_ylim(0, 1)
    axes[0].set_title("Embedding candidates (dense, Korean query -> English chunks)", fontsize=9)
    axes[0].legend(fontsize=7, ncol=4)
    axes[0].grid(axis="y", alpha=0.3)
    y = np.arange(len(rdf))
    axes[1].barh(y - 0.2, rdf["hit@5"], 0.4, label="hit@5")
    axes[1].barh(y + 0.2, rdf["mrr@10"], 0.4, label="mrr@10")
    axes[1].set_yticks(y, [c.replace(" + ", "\n+ ") for c in rdf["config"]], fontsize=7)
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, 1)
    axes[1].set_title(f"Retrieval configurations ({rdf['model'].iloc[0]})", fontsize=9)
    axes[1].legend(fontsize=7, loc="lower left")
    axes[1].grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "embedding_eval.png", dpi=180)
    print("chart ->", OUT / "embedding_eval.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["encode", "encode-one", "pool", "select", "score"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    if a.stage == "encode":
        encode([a.arg] if a.arg else list(CANDIDATES))
    elif a.stage == "encode-one":
        encode_one(a.arg)
    else:
        {"pool": pool, "select": select, "score": score}[a.stage]()
