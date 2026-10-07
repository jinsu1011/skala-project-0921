"""Render the compiled LangGraph (draw_mermaid) to docs/graph.png locally (report/mermaid_render.py, no upload).

LangGraph's drawer adds a dotted edge from every conditional-edge source to a deferred node (here synthesizer) so that
the deferred node stays reachable in the picture. Those edges (e.g. retrieval_grader -> result_aggregator,
report_quality_evaluator -> result_aggregator) are not routes: the router functions return only their declared targets. They are removed before
rendering and the removal is recorded in docs/graph.mmd as a comment.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DRAW_ONLY_EDGES = [r"\t(retrieval_grader|perspective_judge|report_quality_evaluator|retry_router) -\.-> result_aggregator;\n"]


def main() -> Path:
    from graph.workflow import build_graph
    from report.mermaid_render import render

    src = build_graph().get_graph().draw_mermaid()
    for pat in DRAW_ONLY_EDGES:
        src = re.sub(pat, "", src)
    src = src.replace("graph TD;", "graph TD;\n\t%% drawer-only edges to the deferred result_aggregator removed (see scripts/render_graph.py)", 1)
    (ROOT / "docs" / "graph.mmd").write_text(src)
    out = render(src, ROOT / "docs" / "graph.png", width=1400, font_px=15, rank_spacing=40, node_spacing=30)
    print(out)
    return out


if __name__ == "__main__":
    main()
