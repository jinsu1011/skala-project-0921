"""Tech research agent: selection_validator, tech_research, trl_assessor (D.1).

Selection is the team's decision (option 2): the validator only checks it against the source papers and records the
result; there is no re-selection branch. TRL is an estimate from public information, reported as a range + confidence,
with independent origin families on the lower and upper bound (C.4).
"""
from __future__ import annotations

import json

from agents.perspective import (Collected, CriterionSpec, PerspectiveSpec, collect, id_conflict_warnings, prompt)
from graph.runtime import audit, llm_json
from graph.state import (Criterion, PerspectiveResult, SelectionCheck, SelectionValidation, TechAssessment, TechBrief)
from tools.evidence import developer_groups
from tools.paper_retrieve import search_papers

CHECKS = [("same_problem", "KV cache 용량 문제를 직접 다루는가"),
          ("same_stage", "모델 학습이 끝난 뒤 추론·서빙 단계에 적용되는가(재학습 불필요)"),
          ("public_evidence", "실험 환경·기준선·성능 수치가 공개돼 근거로 확인할 수 있는가")]

VALIDATOR_SYS = """너는 기술 선정 검증자이다. 조가 직접 고른 기술(2안)을 원 논문 근거로 다시 확인만 한다. 선정을 바꾸거나 다른 기술을 추천하지 않는다.
세 항목을 판정한다: {checks}
각 항목에 passed(true/false), rationale(한국어 1~2문장, 문장마다 [근거 ID]), evidence_ids를 적는다.
weaknesses: 원문에서 확인되는 약점이나 검증 범위의 공백을 1~3개 적는다(각각 [근거 ID]).
충족하지 못한 항목이 있을 때만 alternative에 Doc Pool의 대체 후보 이름과 사유를 적고, 모두 충족하면 빈 문자열로 둔다.
JSON: {{"checks": [{{"name": "same_problem", "passed": true, "rationale": "...", "evidence_ids": ["P:..."]}}], "weaknesses": ["..."], "alternative": ""}}"""

BRIEF_SYS = """너는 기술 조사 에이전트이다. 주어진 원 논문 청크만 보고 기술 개요를 정리한다. 시장·이해관계자는 판단하지 않는다.
항목: principle(작동 원리), scope(적용 범위·적용 시점), conditions(실험 조건: 모델, 문맥 길이, HW, 기준선), reported_results(보고된 성능과 그 조건), limitations(논문에서 확인되는 한계·다루지 않은 부분).
각 항목은 한국어 2~3문장이고 문장마다 [근거 ID]를 붙인다. 근거에 없는 수치·주장은 쓰지 않는다. 우열·추천 표현을 쓰지 않는다.
JSON: {"principle": "...", "scope": "...", "conditions": "...", "reported_results": "...", "limitations": "...", "evidence_ids": ["P:..."]}"""


def _chunks_block(evs) -> str:
    return json.dumps([{"id": e.evidence_id, "section": e.section, "page": e.page, "text": e.summary[:1100]}
                       for e in sorted(evs, key=lambda x: x.evidence_id)], ensure_ascii=False)


def selection_validator(state: dict) -> dict:
    sv = SelectionValidation()
    evidence, warns = [], []
    for t in state["selected_techs"]:
        evs = {}
        for q in (f"{t.name}가 해결하는 문제와 KV cache 메모리 병목", f"{t.name}의 적용 시점, 재학습 필요 여부, 서빙 엔진 통합",
                  f"{t.name}의 실험 환경과 기준선, 성능 수치"):
            for e in search_papers(q, tech=t.tech_id, k=3):
                evs[e.evidence_id] = e
        data = llm_json("generator", VALIDATOR_SYS.format(checks="; ".join(f"{k}: {v}" for k, v in CHECKS)),
                        json.dumps({"technology": t.name, "developer": t.developer, "team_reason": t.reason,
                                    "chunks": json.loads(_chunks_block(evs.values()))}, ensure_ascii=False),
                        tag=f"selection_validator:{t.tech_id}")
        valid = set(evs)
        checks = []
        for name, _ in CHECKS:
            d = next((c for c in data.get("checks", []) if c.get("name") == name), {})
            ids = [i for i in d.get("evidence_ids", []) if i in valid]
            checks.append(SelectionCheck(name=name, passed=bool(d.get("passed")) and bool(ids),
                                         rationale=d.get("rationale", ""), evidence_ids=ids))
        sv.by_tech[t.tech_id] = checks
        sv.weaknesses[t.tech_id] = [w for w in data.get("weaknesses", []) if isinstance(w, str)]
        if not all(c.passed for c in checks):
            sv.passed = False
            sv.alternatives[t.tech_id] = data.get("alternative", "")
            warns.append(f"선정 검증: {t.name}의 미충족 항목 " + ", ".join(c.name for c in checks if not c.passed)
                         + " (선정은 유지하고 기록만 함)")
        evidence += [e.model_copy(update={"perspectives": ["selection"]}) for e in evs.values()]
    return {"selection_validation": sv, "evidence": evidence, "warnings": warns,
            "audit_log": audit("selection_validator", passed=sv.passed)}


def tech_research(state: dict) -> dict:
    briefs, evidence = {}, []
    for t in state["selected_techs"]:
        evs = state.get("retrieved_chunks", {}).get(t.tech_id, [])
        data = llm_json("generator", BRIEF_SYS, json.dumps({"technology": t.name, "chunks": json.loads(_chunks_block(evs))},
                                                           ensure_ascii=False), tag=f"tech_research:{t.tech_id}")
        valid = {e.evidence_id for e in evs}
        briefs[t.tech_id] = TechBrief(tech_id=t.tech_id, **{k: data.get(k, "") for k in
                                      ("principle", "scope", "conditions", "reported_results", "limitations")},
                                      evidence_ids=sorted(i for i in data.get("evidence_ids", []) if i in valid) or sorted(valid))
        evidence += [e.model_copy(update={"perspectives": ["tech_brief"]}) for e in evs]
    return {"tech_brief": briefs, "evidence": evidence, "audit_log": audit("tech_research", techs=list(briefs))}


# ---------------------------------------------------------------- TRL
TRL_SPEC = PerspectiveSpec(
    key="trl", name_ko="기술 성숙도(TRL)",
    criteria=[CriterionSpec("validation", "검증 수준", 0), CriterionSpec("release", "코드·데이터 공개", 0),
              CriterionSpec("framework", "서빙 프레임워크 반영", 0), CriterionSpec("product", "샘플 공급·제품 출시", 0)],
    rounds=[
        [("neutral", "{sn} open source implementation release"),
         ("neutral", "{sn} product launch availability customers"),
         ("neutral", "{sn} vLLM integration production deployment")],
        [("neutral", "{sn} demo prototype evaluation partners"),
         ("neutral", "{sn} roadmap commercialization plan")],
        [("neutral", "{sn} github implementation"),
         ("neutral", "{sn} sample shipment mass production")],
    ],
    paper_query="{name}의 검증 방식(시뮬레이션, 실제 하드웨어 측정, 시제품)과 실험 환경",
    extra_prompt="TRL 기준 키: validation(검증 수준: 시뮬레이션/실제 HW 측정/시제품), release(코드·데이터 공개), "
                 "framework(vLLM 등 서빙 프레임워크 반영), product(샘플 공급·제품 출시). 공개된 사실이면 stance를 pro, "
                 "아직 없다거나 계획뿐이라는 근거면 con, 단순 언급이면 neutral로 적는다.",
    feedback_queries=True,
)

TRL_SYS = """너는 기술 조사 에이전트의 TRL 추정 담당이다. 아래 TRL 추정 규칙을 그대로 따른다.

{ladder}

규칙: TRL은 공개 정보로 추정한 값이다. trl_low는 공개 근거로 확인된 가장 높은 단계, trl_high는 발표·계획 같은 부분 신호로 보이는 단계이다(trl_low <= trl_high).
low_ids에는 하한을 뒷받침하는 근거 ID, high_ids에는 상한을 뒷받침하는 근거 ID를 적는다. 두 기술에 같은 규칙을 쓴다. 우열·추천 표현을 쓰지 않는다.
ladder_rows: 판단에 쓴 근거마다 {{"stage": 단계 숫자, "evidence_id": "...", "note": "한국어 한 문장"}}.
summary: 추정 결과와 이유를 한국어 2~3문장으로(문장마다 [근거 ID]).
JSON: {{"trl_low": 4, "trl_high": 5, "low_ids": ["..."], "high_ids": ["..."], "ladder_rows": [...], "summary": "...", "limitations": ["..."]}}"""


def _trl_sufficient(exclude):
    def f(evs, ann) -> bool:
        rel = {e.origin_group for e in evs if ann.get(e.evidence_id, {}).get("relevant")}
        return len(rel) >= 3 and bool(rel - set(exclude))
    return f


def trl_assess_tech(t, col: Collected, tag: str) -> TechAssessment:
    data = llm_json("generator", TRL_SYS.format(ladder=prompt("trl_ladder_c4.md")),
                    json.dumps({"technology": t.name, "developer": t.developer, "evidence": [
                        {"id": e.evidence_id, "origin": e.origin_group, "kind": e.kind, "date": e.published_at,
                         "claim": e.claim or e.summary[:300]} for e in sorted(col.evidence, key=lambda x: x.evidence_id)]},
                        ensure_ascii=False), tag=f"{tag}:trl")
    by_id = {e.evidence_id: e for e in col.evidence}
    low_ids = sorted(i for i in data.get("low_ids", []) if i in by_id)
    high_ids = sorted(i for i in data.get("high_ids", []) if i in by_id)
    lo, hi = data.get("trl_low"), data.get("trl_high")
    lo = int(lo) if isinstance(lo, (int, float)) else None
    hi = int(hi) if isinstance(hi, (int, float)) else None
    if lo is not None and hi is not None and hi < lo:
        lo, hi = hi, lo
    devs = set(developer_groups(t.tech_id))
    low_o = {by_id[i].origin_group for i in low_ids}
    high_o = {by_id[i].origin_group for i in high_ids}
    indep_low, indep_high = bool(low_o - devs), bool(high_o - devs)
    # C.4: both bounds independently supported -> high, one side -> mid, developer material only -> low
    conf = "high" if indep_low and indep_high else ("mid" if indep_low or indep_high else "low")
    rows = [Criterion(name=f"TRL {r.get('stage')}", rationale=r.get("note", ""),
                      evidence_ids=[r["evidence_id"]] if r.get("evidence_id") in by_id else [])
            for r in data.get("ladder_rows", []) if isinstance(r, dict)]
    lim = [x for x in data.get("limitations", []) if isinstance(x, str)]
    lim.append("공개 정보 기반 추정이며, TRL 4~6 구간은 공개 정보가 가장 적다")
    if not low_ids or not high_ids:
        lim.append("범위의 하한 또는 상한 근거가 없음(근거 부족)")
    return TechAssessment(criteria=rows, score=None, summary=data.get("summary", ""), limitations=lim, confidence=conf,
                          claims={e.evidence_id: e.claim for e in col.evidence if e.claim},
                          trl_low=lo, trl_high=hi, low_ids=low_ids, high_ids=high_ids,
                          pro_ids=low_ids, con_ids=high_ids)


def trl_assessor(state: dict) -> dict:
    attempt = state.get("perspective_retry_count", {}).get("trl", 0)
    feedback = state.get("judge_feedback", {}).get("trl", "")
    by_tech, evidence, events, warns = {}, [], [], []
    for t in state["selected_techs"]:
        tag = f"trl:{t.tech_id}:a{attempt}"
        col = collect(TRL_SPEC, t, attempt, feedback, _trl_sufficient(developer_groups(t.tech_id)), tag)
        ta = trl_assess_tech(t, col, tag)
        by_tech[t.tech_id] = ta
        evidence += col.evidence
        warns += id_conflict_warnings(state, col.evidence)
        if ta.trl_low is None:
            warns.append(f"TRL {t.name}: 범위를 추정할 근거 부족")
        events += audit("trl_assessor", tech=t.tech_id, attempt=attempt, rounds=col.rounds, queries=col.queries,
                        trl=[ta.trl_low, ta.trl_high], confidence=ta.confidence)
    return {"trl_result": PerspectiveResult(perspective="trl", by_tech=by_tech), "evidence": evidence,
            "audit_log": events, "warnings": warns}
