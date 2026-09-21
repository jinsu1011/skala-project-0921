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


def _between(text: str, a: str, b: str) -> str:
    return text.split(a, 1)[1].split(b, 1)[0].strip()


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
        "인덱싱(s)": emb["index_s"], "질의(ms)": emb["query_ms"], "MPS 메모리(MB)": emb["mps_alloc_mb"],
    })
    rep["{{EMB_TABLE}}"] = _md_table(t, "4.0,1.2,1.2,1.2,1.4,2.4,1.4,1.3,1.9") + \
        "\n\n> 측정 환경: Apple M5(16 GB, MPS) · 159 청크 · 42문항 · `max_seq_length`는 ① 표의 평가 설정. 인덱싱은 전체 청크 인코딩 시간, 질의는 1건당 평균. " \
        "Hit@k는 복수 정답 중 하나라도 top-k 안에 있으면 적중, MRR은 첫 적중 순위의 역수 평균(top-10)."
    tr = pd.read_csv(ROOT / "outputs/eval/truncation_check.csv").set_index("model")
    lic = emb.set_index("model")["license"]
    order = ["bge-m3", "qwen3-embedding-0.6b", "multilingual-e5-large", "multilingual-minilm-l12"]
    s1 = pd.DataFrame({
        "모델": order,
        "다국어": ["O"] * 4,
        "모델 고유 최대 입력(모델 카드·config)": [f"{tr.loc[m, 'native_max']:,}" for m in order],
        "평가 설정 길이": [f"{tr.loc[m, 'eval_max_seq_length']:,}" for m in order],
        "평가 시 잘린 청크": [f"{tr.loc[m, 'truncated_chunks']}/{tr.loc[m, 'n_chunks']}" for m in order],
        "라이선스": [lic[m] for m in order],
        "로컬 실행": ["O"] * 4,
        "① 판정": ["**통과**" if tr.loc[m, "native_max"] >= 900 else "탈락 (참고 기준선)" for m in order],
    })
    rep["{{STAGE1_TABLE}}"] = _md_table(s1, "3.4,1.1,2.4,1.8,1.7,1.6,1.1,2.9")
    b0 = emb.set_index("model")
    s3 = pd.DataFrame({
        "모델": order,
        "① 결과": ["통과", "통과", "탈락", "탈락"],
        "min(SW, HW) MRR": [f"{min(b0.loc[m, 'mrr@10_SW'], b0.loc[m, 'mrr@10_HW']):.3f}" for m in order],
        "SW / HW MRR": [f"{b0.loc[m, 'mrr@10_SW']:.3f} / {b0.loc[m, 'mrr@10_HW']:.3f}" for m in order],
        "인덱싱(s)": [b0.loc[m, "index_s"] for m in order], "질의(ms)": [b0.loc[m, "query_ms"] for m in order],
        "MPS 메모리(MB)": [b0.loc[m, "mps_alloc_mb"] for m in order], "인덱스(MB)": [b0.loc[m, "index_mb"] for m in order],
    })
    rep["{{STAGE3_TABLE}}"] = _md_table(s3, "3.6,1.4,2.1,2.4,1.5,1.4,2.0,1.6")
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
    best = ret.iloc[-1]
    hyb = ret.iloc[-2]
    rep |= {
        "{{RERANK_S}}": f"{best['rerank_ms_per_query'] / 1000:.1f}",
        "{{RERANK_GAIN}}": f"{best['mrr@10'] - hyb['mrr@10']:.3f}",
        "{{BGE_H1}}": f3(b.loc["bge-m3", "hit@1"]), "{{BGE_H5}}": f3(b.loc["bge-m3", "hit@5"]),
        "{{BGE_MRR}}": f3(b.loc["bge-m3", "mrr@10"]),
        "{{BEST_H1}}": f3(best["hit@1"]), "{{BEST_H5}}": f3(best["hit@5"]), "{{BEST_MRR}}": f3(best["mrr@10"]),
        "{{MERMAID_A}}": (ROOT / "docs/graph_overview.mmd").read_text().rstrip(),
        "{{MERMAID_B}}": (ROOT / "docs/graph_quality.mmd").read_text().rstrip(),
        "{{REVISION_TABLE}}": _between((ROOT / "docs/REVISION_v1.1.md").read_text(), "<!--CHANGES-->", "<!--/CHANGES-->"),
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
                      version="v1.1 (설계 개정)")
    rb = ReportBuilder(cover)
    rb.markdown(md, ROOT / "docs")
    out_dir = ROOT / "deliverables"
    docx = rb.save(out_dir / f"{OUT_NAME}.docx")
    pdf = docx_to_pdf(docx, out_dir / f"{OUT_NAME}.pdf")
    print(pdf)
    return pdf


if __name__ == "__main__":
    main()
