"""Figure 1 of the design doc: team-measured embedding / retrieval results (42-question Korean->English eval set).

Reads the implementation-setting measurements (max_seq_length 2,048) in outputs/eval/v2_maxlen2048/
and writes outputs/eval/embedding_eval_ko.png. The original English chart (embedding_eval.png) is kept as is.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "outputs/eval/v2_maxlen2048"
OUT = ROOT / "outputs/eval/embedding_eval_ko.png"

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]  # fixed categorical order (validated, light surface)

MODEL_LABEL = {"bge-m3": "bge-m3", "qwen3-embedding-0.6b": "Qwen3-Emb-0.6B",
               "multilingual-e5-large": "mE5-large", "multilingual-minilm-l12": "MiniLM-L12"}
RET_LABEL = {
    "dense (KO query)": "Dense (한국어)",
    "BM25 (KO query)": "BM25 (한국어)",
    "BM25 (EN rewrite)": "BM25 (영어 재작성)",
    "hybrid RRF: dense KO + BM25 EN": "2중 RRF",
    "hybrid RRF: dense KO + dense EN + BM25 EN": "3중 RRF",
    "hybrid RRF: dense KO + BM25 EN + rerank(bge-reranker-v2-m3)": "2중 RRF + reranker",
    "hybrid RRF: dense KO + dense EN + BM25 EN + rerank(bge-reranker-v2-m3)": "3중 RRF + reranker (채택)",
}


def _korean_font() -> None:
    for name in ("Apple SD Gothic Neo", "AppleGothic", "Malgun Gothic", "NanumGothic", "Noto Sans CJK KR"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)


def main() -> Path:
    _korean_font()
    emb = pd.read_csv(SRC / "embedding_eval.csv").set_index("model")
    ret = pd.read_csv(SRC / "retrieval_config_eval.csv")
    order = ["bge-m3", "qwen3-embedding-0.6b", "multilingual-e5-large", "multilingual-minilm-l12"]

    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.4), gridspec_kw={"width_ratios": [1, 1.25]})
    fig.patch.set_facecolor(SURFACE)

    # (a) embedding candidates: dense only, Korean query
    x = np.arange(len(order))
    metrics = [("hit@1", "Hit@1"), ("hit@5", "Hit@5"), ("mrr@10", "MRR@10")]
    w = 0.26
    for i, (col, lab) in enumerate(metrics):
        a.bar(x + (i - 1) * w, emb.loc[order, col], w - 0.03, color=SERIES[i], label=lab, zorder=2)
    a.set_xticks(x, [MODEL_LABEL[m] for m in order])
    a.set_ylim(0, 1.12)
    a.set_yticks(np.linspace(0, 1, 6))
    a.yaxis.grid(True, color=GRID, zorder=0)
    a.set_title("(a) 임베딩 후보 (dense, 한국어 질의)", fontsize=11, color=INK, loc="left")
    a.legend(frameon=False, fontsize=9, ncol=3, loc="upper right", labelcolor=INK2)
    _style(a)

    # (b) retrieval configurations on bge-m3
    names = [RET_LABEL[c] for c in ret["config"]][::-1]
    y = np.arange(len(names))
    for i, (col, lab) in enumerate([("hit@5", "Hit@5"), ("mrr@10", "MRR@10")]):
        b.barh(y + (0.19 if i == 0 else -0.19), ret[col][::-1], 0.34, color=SERIES[i], label=lab, zorder=2)
    b.set_yticks(y, names)
    b.set_xlim(0, 1)
    b.xaxis.grid(True, color=GRID, zorder=0)
    b.set_title("(b) 검색 구성 (bge-m3 기준)", fontsize=11, color=INK, loc="left")
    b.legend(frameon=False, fontsize=9, ncol=2, loc="lower right", bbox_to_anchor=(1.0, 1.0), labelcolor=INK2)
    _style(b)
    for t in b.get_yticklabels():
        if "채택" in t.get_text():
            t.set_color(INK)
            t.set_fontweight("bold")

    fig.tight_layout()
    fig.savefig(OUT, dpi=200, facecolor=SURFACE)
    print(OUT)
    return OUT


if __name__ == "__main__":
    main()
