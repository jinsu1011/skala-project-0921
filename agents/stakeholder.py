"""Stakeholder agent (stakeholder_evaluator): web only. Groups (a) cloud/datacenter (b) GPU/memory vendors
(c) developers (d) investors/analysts/press, 25 each; stance support 5 / mixed-neutral 3 / concern 1 (C.5).
The developer's own statements (Google for TurboQuant, SK hynix for ITME) are cited for reference but never scored."""
from __future__ import annotations

from agents.perspective import (Collected, CriterionSpec, PerspectiveSpec, apply_condition_mismatch, collect,
                                id_conflict_warnings, origin_sets, pro_con_sufficient, prompt, RUBRIC, weighted)
from graph.runtime import audit, llm_json
from graph.state import Criterion, PerspectiveResult, TechAssessment
from tools.evidence import developer_groups

GROUPS = [("a", "클라우드·데이터센터 사업자"), ("b", "GPU·메모리 벤더(개발사 제외)"), ("c", "개발자 커뮤니티"),
          ("d", "투자·분석·언론")]

SPEC = PerspectiveSpec(
    key="stakeholder", name_ko="이해관계자",
    criteria=[CriterionSpec(f"group_{g}", n, 25) for g, n in GROUPS],
    rounds=[
        [("neutral", "{sn} cloud provider data center reaction"),
         ("neutral", "{sn} GPU memory vendor competitor response"),
         ("neutral", "{sn} developers community discussion"),
         ("neutral", "{sn} analyst investor stock reaction")],
        [("con", "{sn} skepticism concerns criticism"),
         ("pro", "{sn} industry support endorsement")],
        [("con", "{sn} doubts limitations analyst view"),
         ("neutral", "{sn} opinion industry experts")],
    ],
    web_only=True, exclude_developer=True,
    extra_prompt=("이해관계자 기준은 집단이다: group_a(클라우드·데이터센터 사업자), group_b(GPU·메모리 벤더, 개발사 제외), "
                  "group_c(개발자 커뮤니티), group_d(투자·분석·언론). 근거를 낸 주체가 속한 집단을 group에 적고, "
                  "그 집단의 입장을 signals에 {\"criterion\": \"group_x\", \"stance\": pro(지지)|con(우려)|neutral(중립·혼재)}로 적는다. "
                  "개발사(평가 기술을 만든 회사) 본인의 발언·보도자료면 group을 \"developer\"로 적는다. "
                  "mention: 근거가 기술 자체(성능·방식)를 말하면 \"tech\", 생태계·사업 전략(파트너십, 표준, 투자, 시장 지위)을 말하면 \"ecosystem\"."),
    extra_fields=', "group": "a", "mention": "tech"',
)


def _group_score(ids_by_stance: dict[str, set[str]]) -> float | None:
    """C.6 stakeholder row: majority support 5, majority concern 1, similar or neutral 3, no evidence -> 판단 보류."""
    p, c, n = (len(ids_by_stance[k]) for k in ("pro", "con", "neutral"))
    if p + c + n == 0:
        return None
    if p > c + n:
        return 5.0
    if c > p + n:
        return 1.0
    return 3.0


def assess(t, col: Collected, tag: str) -> TechAssessment:
    exclude = developer_groups(t.tech_id)
    crit, lines = [], []
    by_id = {e.evidence_id: e for e in col.evidence}
    for g, name in GROUPS:
        key = f"group_{g}"
        st = {s: set() for s in ("pro", "con", "neutral")}
        ids = {s: [] for s in st}
        for e in col.evidence:
            a = col.ann.get(e.evidence_id, {})
            if e.origin_group in exclude or a.get("group") == "developer" or a.get("group") != g:
                continue
            for s in a.get("signals", []):
                if s.get("criterion") == key and s.get("stance") in st:
                    st[s["stance"]].add(e.origin_group)
                    ids[s["stance"]].append(e.evidence_id)
        crit.append(Criterion(name=key, weight=25, score_1to5=_group_score(st), pro_ids=sorted(set(ids["pro"])),
                              con_ids=sorted(set(ids["con"])),
                              evidence_ids=sorted(set(ids["pro"] + ids["con"] + ids["neutral"]))))
        lines.append(f"- {key} ({name}, 가중치 25): 지지 계열 {len(st['pro'])}, 우려 계열 {len(st['con'])}, 중립 계열 {len(st['neutral'])}")
        for s in st:
            for i in sorted(set(ids[s])):
                lines.append(f"    · [{i}] {s} origin={by_id[i].origin_group}: {by_id[i].claim}")
    dev = [e for e in col.evidence if e.origin_group in exclude or col.ann.get(e.evidence_id, {}).get("group") == "developer"]
    lines.append("- 참고(점수 제외, 개발사 발언): " + ", ".join(f"[{e.evidence_id}]" for e in dev))
    sys = prompt("perspective_score.md").format(
        perspective_ko="이해관계자", tech_name=t.name, developer=t.developer, rubric=RUBRIC,
        extra="- 집단별 점수: 집단 안 근거의 다수가 지지하면 5, 다수가 우려하면 1, 비슷하거나 중립이면 3, 근거가 없으면 null. 개발사 발언은 세지 않는다.",
        criteria_block="\n".join(lines))
    data = llm_json("generator", sys, "위 기준별 근거로 채점하라.", tag=f"{tag}:score")
    by_name = {c.get("name"): c for c in data.get("criteria", []) if isinstance(c, dict)}
    for c in crit:
        d = by_name.get(c.name, {})
        c.llm_score = d.get("score") if isinstance(d.get("score"), (int, float)) else None
        c.rationale = d.get("rationale", "")
    pro_o = origin_sets(col.evidence, col.ann, "pro", exclude)
    con_o = origin_sets(col.evidence, col.ann, "con", exclude)
    lim = [x for x in data.get("limitations", []) if isinstance(x, str)]
    if len(pro_o) < 2 or len(con_o) < 2:
        lim.append(f"개발사 제외 기술 고유 근거: 지지 {len(pro_o)}계열, 우려 {len(con_o)}계열(2계열 미만은 근거 부족)")
    scored = [e for e in col.evidence if e.origin_group not in exclude]
    conf = "high" if len(pro_o) >= 2 and len(con_o) >= 2 else ("mid" if pro_o and con_o else "low")
    conf, lim = apply_condition_mismatch(crit, col, conf, lim)
    return TechAssessment(
        criteria=crit, score=weighted(crit), summary=data.get("summary", ""),
        pro_ids=sorted({i for c in crit for i in c.pro_ids}), con_ids=sorted({i for c in crit for i in c.con_ids}),
        limitations=lim, confidence=conf,
        tech_mention_ids=sorted(e.evidence_id for e in scored if col.ann.get(e.evidence_id, {}).get("mention") == "tech"),
        ecosystem_mention_ids=sorted(e.evidence_id for e in scored
                                     if col.ann.get(e.evidence_id, {}).get("mention") == "ecosystem"))


def stakeholder_evaluator(state: dict) -> dict:
    attempt = state.get("perspective_retry_count", {}).get("stakeholder", 0)
    feedback = state.get("judge_feedback", {}).get("stakeholder", "")
    by_tech, evidence, events, warns = {}, [], [], []
    for t in state["selected_techs"]:
        tag = f"stakeholder:{t.tech_id}:a{attempt}"
        col = collect(SPEC, t, attempt, feedback, pro_con_sufficient(developer_groups(t.tech_id)), tag)
        ta = assess(t, col, tag)
        by_tech[t.tech_id] = ta
        evidence += col.evidence
        warns += id_conflict_warnings(state, col.evidence)
        if ta.score is None:
            warns.append(f"이해관계자 관점 {t.name}: 빠진 가중치가 50%를 넘어 판단 보류")
        events += audit("stakeholder_evaluator", tech=t.tech_id, attempt=attempt, rounds=col.rounds,
                        queries=col.queries, evidence=[e.evidence_id for e in col.evidence], feedback=bool(feedback))
    return {"stakeholder_result": PerspectiveResult(perspective="stakeholder", by_tech=by_tech), "evidence": evidence,
            "audit_log": events, "warnings": warns}
