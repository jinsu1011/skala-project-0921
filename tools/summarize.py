"""summarize_sources tool (B.7): topic summary that keeps the original evidence ids and adds no unsupported sentence."""
from __future__ import annotations

import json

from langchain_core.tools import tool

from graph.runtime import llm_json
from graph.state import Evidence

SYSTEM = """너는 근거 요약기이다. 주어진 근거 묶음만 보고 focus 주제로 요약한다.
규칙: 모든 문장 끝에 근거 ID를 [ID] 형식으로 붙인다. 근거에 없는 사실·수치·평가를 만들지 않는다. 우열·추천 표현을 쓰지 않는다.
JSON으로 답한다: {"sentences": [{"text": "...", "evidence_ids": ["P:..", "W:.."]}]}"""


def summarize(evs: list[Evidence], focus: str, tag: str = "summarize") -> list[dict]:
    items = [{"id": e.evidence_id, "title": e.title, "text": (e.claim or e.summary)[:600]}
             for e in sorted(evs, key=lambda x: x.evidence_id)]
    data = llm_json("generator", SYSTEM, json.dumps({"focus": focus, "evidence": items}, ensure_ascii=False), tag=tag)
    valid = {e.evidence_id for e in evs}
    out = []
    for s in data.get("sentences", []):
        ids = [i for i in s.get("evidence_ids", []) if i in valid]
        if ids and s.get("text"):  # sentences without a valid id are dropped
            out.append({"text": s["text"], "evidence_ids": ids})
    return out


@tool
def summarize_sources(docs: list[dict], focus: str) -> list[dict]:
    """Summarise an evidence bundle by topic. Every sentence keeps the original evidence ids; sentences without a
    valid id are dropped, so no unsupported sentence is produced."""
    return summarize([Evidence(**d) for d in docs], focus)
