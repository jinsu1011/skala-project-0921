"""Synthesis agent (synthesizer, defer=True).

Code computes: perspective x tech matrix, conflicts (gap >= 2.0 상충, 1.0-2.0 부분 상충, < 1.0 일치; within one
technology only, 판단 보류 perspectives skipped), the H1 3x3 grid (TRL mid-point x market score, C.5) and the +/-0.5
sensitivity of every threshold. The LLM only writes explanations and the H2-H4 rationale with evidence ids. No ranking.
"""
from __future__ import annotations

import itertools
import json
import re
from typing import Optional

from graph.runtime import audit, llm_json
from graph.state import Conflict, Hypothesis, MatrixCell, SynthesisResult

SCORED = ("market", "stakeholder", "domain")
KO = {"trl": "TRL", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인 적합성"}
CONFLICT_T = (2.0, 1.0)
TRL_T = (3.5, 6.5)
MARKET_T = (2.0, 4.0)


def conflict_label(gap: float, hi: float = CONFLICT_T[0], lo: float = CONFLICT_T[1]) -> str:
    return "상충" if gap >= hi else ("부분 상충" if gap >= lo else "일치")


def conflicts_for(scores: dict[str, dict[str, Optional[float]]], hi=CONFLICT_T[0], lo=CONFLICT_T[1]) -> list[Conflict]:
    out = []
    for tech, s in scores.items():
        for a, b in itertools.combinations(SCORED, 2):
            if s.get(a) is None or s.get(b) is None:
                continue
            gap = round(abs(s[a] - s[b]), 2)
            out.append(Conflict(tech_id=tech, pair=(a, b), gap=gap, label=conflict_label(gap, hi, lo)))
    return out


def trl_band(mid: float, t=TRL_T) -> int:
    return 0 if mid <= t[0] else (2 if mid >= t[1] else 1)


def market_band(score: float, t=MARKET_T) -> int:
    return 0 if score <= t[0] else (2 if score >= t[1] else 1)


def h1_cell(trl_mid: Optional[float], market: Optional[float], tt=TRL_T, mt=MARKET_T) -> str:
    """C.5 grid: diagonal 일치, one band apart 부분 괴리, two apart 괴리; market ahead = 기대 선행, behind = 채택 신호 지연."""
    if trl_mid is None or market is None:
        return "판단 보류"
    d = market_band(market, mt) - trl_band(trl_mid, tt)
    if d == 0:
        return "일치"
    kind = "기대 선행" if d > 0 else "채택 신호 지연"
    return f"{'부분 괴리' if abs(d) == 1 else '괴리'} ({kind})"


def h1_verdict(cells: dict[str, str]) -> str:
    vals = [v for v in cells.values() if v != "판단 보류"]
    if not vals:
        return "판단 보류"
    if any(v.startswith("괴리") for v in vals):
        return "지지"
    if any(v.startswith("부분 괴리") for v in vals):
        return "부분 지지"
    return "기각"


def sensitivity(scores, trl_mid, market) -> dict:
    base = {(c.tech_id, c.pair): c.label for c in conflicts_for(scores)}
    rows = []
    for name, hi, lo in [("상충 기준 2.0→2.5", 2.5, 1.0), ("상충 기준 2.0→1.5", 1.5, 1.0),
                         ("부분 상충 기준 1.0→1.5", 2.0, 1.5), ("부분 상충 기준 1.0→0.5", 2.0, 0.5)]:
        alt = {(c.tech_id, c.pair): c.label for c in conflicts_for(scores, hi, lo)}
        rows.append({"variant": name, "changed": sum(1 for k in base if alt.get(k) != base[k]), "cells": len(base)})
    base_h1 = {t: h1_cell(trl_mid.get(t), market.get(t)) for t in scores}
    h1_rows = []
    for name, tt, mt in [("TRL 경계 3.5→3.0", (3.0, 6.5), MARKET_T), ("TRL 경계 3.5→4.0", (4.0, 6.5), MARKET_T),
                         ("TRL 경계 6.5→6.0", (3.5, 6.0), MARKET_T), ("TRL 경계 6.5→7.0", (3.5, 7.0), MARKET_T),
                         ("시장 경계 2.0→1.5", TRL_T, (1.5, 4.0)), ("시장 경계 2.0→2.5", TRL_T, (2.5, 4.0)),
                         ("시장 경계 4.0→3.5", TRL_T, (2.0, 3.5)), ("시장 경계 4.0→4.5", TRL_T, (2.0, 4.5))]:
        alt = {t: h1_cell(trl_mid.get(t), market.get(t), tt, mt) for t in scores}
        h1_rows.append({"variant": name, "changed": sum(1 for t in base_h1 if alt[t] != base_h1[t]),
                        "cells": len(base_h1), "verdict": h1_verdict(alt)})
    return {"conflict": rows, "h1": h1_rows}


SYS = """너는 평가 종합 에이전트이다. 상충 판정과 H1 판정은 이미 코드로 계산돼 있으니 바꾸지 않는다. 너는 해설만 쓴다.
규칙: 새로 검색하지 않고 주어진 근거만 쓴다. 문장마다 [근거 ID]를 붙인다. 기술 간 순위·우열·추천을 쓰지 않는다. 가설은 결론이 아니라 검증 대상이다.
1) conflicts: 각 상충·부분 상충 항목(키)에 대해 두 관점 점수가 왜 갈리는지 1~2문장으로 설명하고 관련 가설 태그(H1~H4)를 단다.
2) H2: 이해관계자 근거 중 기술 자체 언급과 생태계·전략 언급의 비중(코드 집계)을 보고 판정한다.
3) H3: 도메인 평가에서 W1과 W2 점수가 3점을 사이에 두고 갈리는 기준(코드 집계)을 보고 판정한다.
4) H4: 두 방식을 함께 쓰는 사례나 도입 주체의 차이를 보여 주는 근거가 있는지 보고 판정한다. 근거가 없으면 판단 보류.
판정은 "지지", "부분 지지", "기각", "판단 보류" 중 하나이다.
5) one_liners: 기술별·관점별 한 줄 요약(근거 ID 포함).
JSON: {"conflicts": {"<키>": {"explanation": "...", "tags": ["H2"]}}, "hypotheses": {"H2": {"verdict": "...", "rationale": "...", "evidence_ids": [...]}, "H3": {...}, "H4": {...}}, "one_liners": {"<tech>": {"<perspective>": "..."}}}"""

_SENT = re.compile(r"(?<=[.다요])\s+(?=\S)")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "") if s.strip()]


def synthesizer(state: dict) -> dict:
    techs = state["selected_techs"]
    results = {p: state.get(f"{p}_result") for p in ("trl", *SCORED)}
    scores = {t.tech_id: {p: (results[p].by_tech[t.tech_id].score if results[p] and t.tech_id in results[p].by_tech
                              else None) for p in SCORED} for t in techs}
    trl_mid, trl_txt = {}, {}
    for t in techs:
        ta = results["trl"].by_tech.get(t.tech_id) if results["trl"] else None
        if ta and ta.trl_low is not None and ta.trl_high is not None:
            trl_mid[t.tech_id] = (ta.trl_low + ta.trl_high) / 2
            trl_txt[t.tech_id] = f"TRL {ta.trl_low}–{ta.trl_high} (신뢰도 {ta.confidence})"
        else:
            trl_mid[t.tech_id] = None
            trl_txt[t.tech_id] = "판단 보류"
    market = {t: s["market"] for t, s in scores.items()}
    conflicts = conflicts_for(scores)
    h1_grid = {t.tech_id: h1_cell(trl_mid[t.tech_id], market[t.tech_id]) for t in techs}
    sens = sensitivity(scores, trl_mid, market)

    # code-side statistics handed to the LLM for H2 / H3
    h2 = {}
    for t in techs:
        ta = results["stakeholder"].by_tech.get(t.tech_id) if results["stakeholder"] else None
        if ta:
            h2[t.tech_id] = {"tech_mentions": len(ta.tech_mention_ids), "ecosystem_mentions": len(ta.ecosystem_mention_ids)}
    h3 = {}
    for t in techs:
        ta = results["domain"].by_tech.get(t.tech_id) if results["domain"] else None
        if not ta:
            continue
        by = {c.name: c.score_1to5 for c in ta.criteria}
        split = [k.split(":")[1] for k in by if k.startswith("W1:") and by.get(k) is not None
                 and by.get("W2:" + k.split(":")[1]) is not None and (by[k] - 3) * (by["W2:" + k.split(":")[1]] - 3) < 0]
        w = {}
        for wl in ("W1", "W2"):
            cs = [c for c in ta.criteria if c.workload == wl and c.score_1to5 is not None]
            w[wl] = round(sum(c.score_1to5 for c in cs) / len(cs), 2) if len(cs) * 2 >= 5 else None
        h3[t.tech_id] = {"split_criteria": split, "W1": w["W1"], "W2": w["W2"],
                         "criteria": {k: v for k, v in by.items()}}
    ev = {e.evidence_id: e for e in state.get("evidence", [])}
    cited = sorted({i for p in SCORED if results[p] for ta in results[p].by_tech.values()
                    for i in ta.pro_ids + ta.con_ids + ta.tech_mention_ids + ta.ecosystem_mention_ids})
    payload = {
        "techs": {t.tech_id: t.name for t in techs}, "scores": scores, "trl": trl_txt,
        "conflicts": {f"{c.tech_id}:{c.pair[0]}-{c.pair[1]}": {"gap": c.gap, "label": c.label} for c in conflicts
                      if c.label != "일치"},
        "h1_grid": h1_grid, "h2_stats": h2, "h3_stats": h3,
        "summaries": {p: {t: ta.summary for t, ta in results[p].by_tech.items()} for p in results if results[p]},
        "evidence": [{"id": i, "tech": ev[i].tech, "claim": ev[i].claim} for i in cited if i in ev][:80],
    }
    data = llm_json("generator", SYS, json.dumps(payload, ensure_ascii=False), tag="synthesizer")
    for c in conflicts:
        d = data.get("conflicts", {}).get(f"{c.tech_id}:{c.pair[0]}-{c.pair[1]}", {})
        c.explanation = d.get("explanation", "")
        c.hypothesis_tags = [x for x in d.get("tags", []) if x in ("H1", "H2", "H3", "H4")]
    hyps = {"H1": Hypothesis(verdict=h1_verdict(h1_grid), rationale="; ".join(
        f"{t.name}: {trl_txt[t.tech_id]}, 시장성 {market[t.tech_id] if market[t.tech_id] is not None else '판단 보류'} → "
        f"{h1_grid[t.tech_id]}" for t in techs) + " (C.5 격자, 코드 계산)",
        evidence_ids=sorted({i for t in techs for i in ((results['trl'].by_tech[t.tech_id].low_ids +
                             results['trl'].by_tech[t.tech_id].high_ids) if results['trl'] and t.tech_id in results['trl'].by_tech else [])}))}
    for h in ("H2", "H3", "H4"):
        d = data.get("hypotheses", {}).get(h, {})
        v = d.get("verdict") if d.get("verdict") in ("지지", "부분 지지", "기각", "판단 보류") else "판단 보류"
        hyps[h] = Hypothesis(verdict=v, rationale=d.get("rationale", "근거 부족"),
                             evidence_ids=[i for i in d.get("evidence_ids", []) if i in ev])
    matrix = {}
    for t in techs:
        ol = data.get("one_liners", {}).get(t.tech_id, {})
        matrix[t.tech_id] = {p: MatrixCell(score=scores[t.tech_id][p], one_liner=ol.get(p, "")) for p in SCORED}
        matrix[t.tech_id]["trl"] = MatrixCell(score=trl_mid[t.tech_id], one_liner=ol.get("trl", trl_txt[t.tech_id]))
    statements, k = {}, 1
    for c in conflicts:
        for s in split_sentences(c.explanation):
            statements[f"S{k}"] = s
            k += 1
    for h in ("H2", "H3", "H4"):
        for s in split_sentences(hyps[h].rationale):
            statements[f"S{k}"] = s
            k += 1
    syn = SynthesisResult(matrix=matrix, conflicts=conflicts, hypotheses=hyps, h1_grid=h1_grid,
                          sensitivity=sens | {"h2_stats": h2, "h3_stats": h3}, statements=statements)
    return {"synthesis": syn, "audit_log": audit("synthesizer", scores=scores, h1=h1_grid,
                                                  conflicts=[(c.tech_id, c.pair, c.label) for c in conflicts])}
