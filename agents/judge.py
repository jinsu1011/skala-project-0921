"""Judge agent (gpt-4.1): LLM scores grounding / neutrality / source_diversity / completeness (1-5) and code runs the
rule checks. Verdict = D.5 exactly:

    LLM_OK = all(s >= 4 for s in [grounding, neutrality, source_diversity, completeness])
    COMMON = max_origin_share <= 0.50 and lexicon_hits == 0
    PER_PERSPECTIVE = {trl: all bound_origins >= 1, market/stakeholder/domain: all pro >= 2 and con >= 2}
    passed = LLM_OK and COMMON and PER_PERSPECTIVE[perspective]

Passed perspectives are frozen (never re-scored). After a re-run only the re-run perspectives and the synthesis
explanation are scored. Unsupported synthesis sentences go to unsupported_claim_ids and are dropped from the report.
The Judge does not edit content and does not manage re-run counts.
"""
from __future__ import annotations

import json

from graph.runtime import audit, lexicon_hits, llm_json
from graph.state import PERSPECTIVES, JudgeChecks, JudgeScore
from tools.evidence import developer_groups, max_origin_share

SYS = """너는 Judge이다. 다른 에이전트의 평가 결과를 채점만 하고 내용을 고치지 않는다.
네 항목을 1~5점으로 채점한다(4점 이상이 통과).
- grounding(근거성): 핵심 주장마다 근거 ID가 있고 그 근거(claim)가 주장을 직접 뒷받침하는가
- neutrality(중립성): 장점과 한계를 함께 쓰고 근거 없는 우열·추천이 없는가
- source_diversity(출처 다양성): 근거가 여러 독립 출처 계열(origin)에서 왔는가, 개발사 자료에 치우치지 않았는가
- completeness(완결성): 정해진 기준과 두 기술을 모두 다뤘는가(판단 보류는 이유가 적혀 있으면 다룬 것으로 본다)
unsupported: 근거가 없거나 근거가 뒷받침하지 않는 문장을 그대로 인용해 적는다.
feedback: 기준에 못 미치면 무엇을 보완해 다시 검색할지 한국어 1~2문장으로 적는다(통과면 빈 문자열).
JSON: {"grounding": 4, "neutrality": 4, "source_diversity": 4, "completeness": 4, "unsupported": ["..."], "feedback": "..."}"""

SYN_SYS = """너는 Judge이다. 평가 종합의 해설 문장(S1, S2, ...)이 주어진 근거로 뒷받침되는지 확인한다. 내용을 고치지 않는다.
문장에 근거 ID가 없거나, 근거의 claim이 문장을 뒷받침하지 않거나, 기술 간 우열·추천을 담은 문장의 ID를 unsupported_ids에 적는다.
grounding, neutrality, source_diversity, completeness를 1~5점으로 채점한다.
JSON: {"grounding": 4, "neutrality": 4, "source_diversity": 4, "completeness": 4, "unsupported_ids": ["S3"]}"""


def _texts(ta) -> str:
    parts = [ta.summary] + [c.rationale for c in ta.criteria] + ta.limitations
    return "\n".join(p for p in parts if p)


def code_checks(perspective: str, result, ev: dict, techs) -> JudgeChecks:
    chk = JudgeChecks()
    shares, hits = [], 0
    for t in techs:
        ta = result.by_tech.get(t.tech_id)
        if ta is None:
            chk.pro_origins[t.tech_id] = chk.con_origins[t.tech_id] = chk.bound_origins[t.tech_id] = 0
            continue
        used = sorted({i for c in ta.criteria for i in c.evidence_ids} | set(ta.pro_ids) | set(ta.con_ids)
                      | set(ta.low_ids) | set(ta.high_ids))
        evs = [ev[i] for i in used if i in ev]
        shares.append(max_origin_share(evs))
        excl = set(developer_groups(t.tech_id)) if perspective == "stakeholder" else set()
        # pro_ids / con_ids are already restricted to tech-specific evidence by the perspective's own annotation;
        # the shared Evidence.scope keeps the first perspective's label, so it is not re-checked here
        spec = lambda ids: {ev[i].origin_group for i in ids if i in ev and ev[i].origin_group not in excl}  # noqa: E731
        chk.pro_origins[t.tech_id] = len(spec(ta.pro_ids))
        chk.con_origins[t.tech_id] = len(spec(ta.con_ids))
        if perspective == "trl":
            low = {ev[i].origin_group for i in ta.low_ids if i in ev}
            high = {ev[i].origin_group for i in ta.high_ids if i in ev}
            chk.bound_origins[t.tech_id] = min(len(low), len(high))
        hits += len(lexicon_hits(_texts(ta)))
    chk.max_origin_share = round(max(shares) if shares else 0.0, 3)
    chk.lexicon_hits = hits
    return chk


def verdict(perspective: str, s: JudgeScore) -> bool:
    c = s.checks
    llm_ok = all(x >= 4 for x in [s.grounding, s.neutrality, s.source_diversity, s.completeness])
    common = c.max_origin_share <= 0.50 and c.lexicon_hits == 0
    per = {
        "trl": all(n >= 1 for n in c.bound_origins.values()),
        "market": all(c.pro_origins[t] >= 2 and c.con_origins[t] >= 2 for t in c.pro_origins),
        "stakeholder": all(c.pro_origins[t] >= 2 and c.con_origins[t] >= 2 for t in c.pro_origins),
        "domain": all(c.pro_origins[t] >= 2 and c.con_origins[t] >= 2 for t in c.pro_origins),
    }
    return llm_ok and common and per[perspective]


def _payload(perspective: str, result, ev: dict, techs) -> str:
    body = {}
    for t in techs:
        ta = result.by_tech.get(t.tech_id)
        if not ta:
            continue
        ids = sorted({i for c in ta.criteria for i in c.evidence_ids} | set(ta.pro_ids) | set(ta.con_ids)
                     | set(ta.low_ids) | set(ta.high_ids))
        body[t.name] = {
            "score": ta.score, "trl": [ta.trl_low, ta.trl_high] if perspective == "trl" else None,
            "confidence": ta.confidence, "summary": ta.summary, "limitations": ta.limitations,
            "criteria": [{"name": c.name, "score": c.score_1to5, "rationale": c.rationale} for c in ta.criteria],
            "evidence": [{"id": i, "origin": ev[i].origin_group, "class": ev[i].source_class, "scope": ev[i].scope,
                          "claim": ev[i].claim or ev[i].summary[:200]} for i in ids if i in ev],
        }
    return json.dumps({"perspective": perspective, "by_tech": body}, ensure_ascii=False)


def _failure_reasons(p: str, s: JudgeScore) -> list[str]:
    c, out = s.checks, []
    if c.max_origin_share > 0.5:
        out.append(f"한 출처 계열 비중 {c.max_origin_share:.0%}(50% 초과)")
    if c.lexicon_hits:
        out.append(f"우열·추천 어휘 {c.lexicon_hits}건")
    if p == "trl":
        out += [f"{t}: 상·하한 근거 계열 부족" for t, n in c.bound_origins.items() if n < 1]
    else:
        out += [f"{t}: 찬성 {c.pro_origins[t]}계열·반대 {c.con_origins[t]}계열(각 2 이상 필요)"
                for t in c.pro_origins if c.pro_origins[t] < 2 or c.con_origins[t] < 2]
    low = [k for k in ("grounding", "neutrality", "source_diversity", "completeness") if getattr(s, k) < 4]
    if low:
        out.append("LLM 채점 4점 미만: " + ", ".join(low))
    return out


def judge(state: dict) -> dict:
    techs = state["selected_techs"]
    ev = {e.evidence_id: e for e in state.get("evidence", [])}
    prev = dict(state.get("judge_scores", {}))
    scores, failed, feedback, events = dict(prev), [], {}, []
    for p in PERSPECTIVES:
        if p in prev and prev[p].passed:  # frozen
            continue
        result = state.get(f"{p}_result")
        chk = code_checks(p, result, ev, techs)
        data = llm_json("judge", SYS, _payload(p, result, ev, techs), tag=f"judge:{p}")
        s = JudgeScore(**{k: int(data.get(k, 0) or 0) for k in ("grounding", "neutrality", "source_diversity",
                                                                  "completeness")},
                       unsupported_claim_ids=[x for x in data.get("unsupported", []) if isinstance(x, str)], checks=chk)
        s.passed = verdict(p, s)
        scores[p] = s
        if not s.passed:
            failed.append(p)
            feedback[p] = " / ".join(_failure_reasons(p, s) + ([data["feedback"]] if data.get("feedback") else []))
        events += audit("judge", perspective=p, passed=s.passed, checks=chk.model_dump(),
                        llm=[s.grounding, s.neutrality, s.source_diversity, s.completeness])
    # synthesis explanation: sentence-level grounding only (no re-run branch, D.4-6)
    syn = state["synthesis"]
    stmts = syn.statements
    if stmts:
        data = llm_json("judge", SYN_SYS, json.dumps({"statements": stmts, "evidence": [
            {"id": i, "claim": ev[i].claim or ev[i].summary[:200]} for i in sorted(
                {i for h in syn.hypotheses.values() for i in h.evidence_ids} |
                {i for s_ in stmts.values() for i in ev if i in s_})]}, ensure_ascii=False), tag="judge:synthesis")
        ss = JudgeScore(**{k: int(data.get(k, 0) or 0) for k in ("grounding", "neutrality", "source_diversity",
                                                                   "completeness")},
                        unsupported_claim_ids=[x for x in data.get("unsupported_ids", []) if x in stmts])
        ss.checks.lexicon_hits = len(lexicon_hits(" ".join(stmts.values())))
        ss.passed = not ss.unsupported_claim_ids and ss.checks.lexicon_hits == 0
        scores["synthesis"] = ss
        events += audit("judge", perspective="synthesis", unsupported=ss.unsupported_claim_ids)
    return {"judge_scores": scores, "failed_perspectives": failed,
            "judge_feedback": {**state.get("judge_feedback", {}), **feedback}, "audit_log": events}
