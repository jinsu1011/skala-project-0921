"""Build the design deliverable (RAG-Design_...pdf) from docs/DESIGN.md + measured eval results, on the SKALA template."""
from __future__ import annotations

import collections
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.chunker import load_or_build_chunks  # noqa: E402
from report.docx_builder import CoverInfo, ReportBuilder, docx_to_pdf  # noqa: E402

TEAM = yaml.safe_load((ROOT / "config.yaml").read_text())["team"]
MEMBERS = [(m["id"], m["name"]) for m in TEAM["members"]]
NAMES = "+".join(n for _, n in MEMBERS)
OUT_NAME = f"RAG-Design_{TEAM['campus']}-{TEAM['class']}_{NAMES}"


def _md_table(df: pd.DataFrame, widths: str) -> str:
    lines = [f"<!--w:{widths}-->", "| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def fill(md: str) -> str:
    chunks = load_or_build_chunks()
    cnt = collections.Counter(c.tech for c in chunks)
    rep = {f"{{{{CH_{k}}}}}": str(v) for k, v in cnt.items()} | {"{{CH_total}}": str(len(chunks))}

    emb = pd.read_csv(ROOT / "outputs/eval/embedding_eval.csv")
    ret = pd.read_csv(ROOT / "outputs/eval/retrieval_config_eval.csv")
    f3 = lambda x: f"{x:.3f}"  # noqa: E731
    t = pd.DataFrame({
        "모델": [f"**{m}**" if i == 0 else m for i, m in enumerate(emb["model"])],
        "Hit@1": emb["hit@1"].map(f3), "Hit@3": emb["hit@3"].map(f3), "Hit@5": emb["hit@5"].map(f3),
        "MRR@10": emb["mrr@10"].map(f3), "MRR SW / HW": [f"{a:.3f} / {b:.3f}" for a, b in zip(emb["mrr@10_SW"], emb["mrr@10_HW"])],
        "최대 토큰": emb["max_seq_length"], "인덱싱(s)": emb["index_s"], "질의(ms)": emb["query_ms"],
        "MPS 메모리(MB)": emb["mps_alloc_mb"],
    })
    rep["{{EMB_TABLE}}"] = _md_table(t, "3.6,1.1,1.1,1.1,1.3,2.1,1.2,1.3,1.1,2.1") + \
        "\n\n> 측정 환경: Apple M5(16 GB, MPS) · 159 청크 · 42문항. 인덱싱은 전체 청크 인코딩 시간, 질의는 1건당 평균. " \
        "Hit@k는 복수 정답 중 하나라도 top-k 안에 있으면 적중, MRR은 첫 적중 순위의 역수 평균(top-10)."
    names = {
        "dense (KO query)": "Dense (한국어 질의)", "BM25 (KO query)": "BM25 (한국어 질의)",
        "BM25 (EN rewrite)": "BM25 (영어 재작성 질의)",
        "hybrid RRF: dense KO + BM25 EN": "하이브리드 RRF: Dense(KO) + BM25(EN)",
        "hybrid RRF: dense KO + dense EN + BM25 EN": "3중 하이브리드 RRF: Dense(KO) + Dense(EN) + BM25(EN)",
    }
    rr = ret.copy()
    rr["config"] = [names.get(c, "**3중 하이브리드 RRF + reranker (bge-reranker-v2-m3) ← 채택**") for c in rr["config"]]
    rt = pd.DataFrame({"검색 구성": rr["config"], "Hit@1": rr["hit@1"].map(f3), "Hit@3": rr["hit@3"].map(f3),
                       "Hit@5": rr["hit@5"].map(f3), "MRR@10": rr["mrr@10"].map(f3)})
    rep["{{RET_TABLE}}"] = _md_table(rt, "8.4,1.9,1.9,1.9,1.9")

    b = emb.set_index("model")
    order = ["bge-m3", "multilingual-e5-large", "qwen3-embedding-0.6b", "multilingual-minilm-l12"]
    dec = pd.DataFrame({
        "기준 (가중)": ["교차언어 정확도 (40%)", "진영 간 균형 (20%)", "청크 수용 길이 (15%)", "로컬 비용 (15%)",
                     "라이선스 (10%)", "판정"],
        **{m: [f"MRR {b.loc[m,'mrr@10']:.3f} · Hit@5 {b.loc[m,'hit@5']:.3f}",
               f"SW {b.loc[m,'mrr@10_SW']:.3f} / HW {b.loc[m,'mrr@10_HW']:.3f}",
               f"{b.loc[m,'max_seq_length']} 토큰" + (" (900 토큰 청크 잘림)" if b.loc[m, 'max_seq_length'] < 900 else " (전체 수용)"),
               f"인덱싱 {b.loc[m,'index_s']}s · 질의 {b.loc[m,'query_ms']}ms · {b.loc[m,'mps_alloc_mb']}MB",
               b.loc[m, "license"],
               {"bge-m3": "**채택**", "multilingual-e5-large": "차선 (512 토큰 제한)",
                "qwen3-embedding-0.6b": "HW 질의 편차 큼", "multilingual-minilm-l12": "경량 기준선"}[m]]
           for m in order},
    })
    rep["{{DECISION_TABLE}}"] = _md_table(dec, "3.0,3.3,3.3,3.3,3.1")

    best = ret.iloc[-1]
    hyb = ret.iloc[-2]
    rep |= {
        "{{RERANK_S}}": f"{best['rerank_ms_per_query'] / 1000:.1f}",
        "{{RERANK_GAIN}}": f"{best['mrr@10'] - hyb['mrr@10']:.3f}",
        "{{BGE_H1}}": f3(b.loc["bge-m3", "hit@1"]), "{{BGE_H5}}": f3(b.loc["bge-m3", "hit@5"]),
        "{{BGE_MRR}}": f3(b.loc["bge-m3", "mrr@10"]),
        "{{BEST_H1}}": f3(best["hit@1"]), "{{BEST_H5}}": f3(best["hit@5"]), "{{BEST_MRR}}": f3(best["mrr@10"]),
        "{{MERMAID}}": (ROOT / "docs/graph_design.mmd").read_text().rstrip(),
    }
    for k, v in rep.items():
        md = md.replace(k, v)
    import re

    left = re.findall(r"\{\{[A-Z_a-z]+\}\}", md)
    if left:
        raise ValueError(f"unfilled placeholders: {left}")
    return md


def main() -> Path:
    md = fill((ROOT / "docs/DESIGN.md").read_text())
    (ROOT / "docs/DESIGN_filled.md").write_text(md)
    cover = CoverInfo(title="KV cache 최적화 기술 다관점 평가 Agentic RAG 설계서", members=MEMBERS,
                      date=TEAM["submit_date"], report_kind="과제 제출 보고서 · 설계 산출물",
                      version="v1.0 (설계)")
    rb = ReportBuilder(cover)
    rb.markdown(md, ROOT / "docs")
    out_dir = ROOT / "deliverables"
    docx = rb.save(out_dir / f"{OUT_NAME}.docx")
    pdf = docx_to_pdf(docx, out_dir / f"{OUT_NAME}.pdf")
    print(pdf)
    return pdf


if __name__ == "__main__":
    main()
