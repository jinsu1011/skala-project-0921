"""Orchestrator (initial dynamic task planner).

The LLM plans SubTasks from the current State under prompts/orchestrator.md. The four minimum perspectives live in
the prompt as coverage requirements; there is no fixed worker list. Code then validates the plan (registry agents,
known technologies, no duplicated specialist work, task cap) and only *repairs* a missing minimum coverage, recording
each repair in Plan.repairs. used_agents / unused_agents are computed by code from the resulting SubTasks.

If the LLM plan is unusable (no API key and no cached response, or invalid JSON) a rule planner builds the plan from
the same State signals and Plan.plan_source = "fallback" is recorded in the plan and the report.
"""
from __future__ import annotations

import json

from agents.perspective import prompt
from agents.registry import MINIMUM_PERSPECTIVES, MINIMUM_KO, REGISTRY, registry_table, used_unused, worker_type
from graph.observability import decision, span
from graph.runtime import OfflineCacheMiss, audit, llm_json
from graph.state import Plan, SubTask

MAX_TASKS = 8
REVIEWER_RESERVE = 1   # keep >= 1 registry agent unassigned so a failed task can get an independent cross-review
SPECIALIST = {wt: a.agent_id for a in REGISTRY.values() if (wt := a.worker_type) != "research"}
OBJECTIVE = {"trl": "공개 근거로 TRL 하한·상한을 추정하고 각 경계를 독립 출처로 뒷받침한다",
             "market": "시장 규모·채택·생태계·비용 구조 기준으로 시장성을 근거 기반으로 평가한다",
             "stakeholder": "개발사 발언을 제외한 이해관계자 집단별 반응을 찬반 균형 있게 정리한다",
             "domain": "장문맥 배치(W1)·고동시성 다중 턴(W2) 서빙 워크로드 적용성을 평가한다"}


def _state_view(state: dict) -> dict:
    briefs = state.get("tech_brief", {})
    grade = state.get("retrieval_grade")
    return {
        "selected_techs": [{"tech_id": t.tech_id, "name": t.name, "camp": t.camp, "developer": t.developer}
                           for t in state["selected_techs"]],
        "tech_brief": {k: {"principle": b.principle[:300], "limitations": b.limitations[:300]} for k, b in briefs.items()},
        "evidence": {"total": len(state.get("evidence", [])),
                     "by_tech": {t.tech_id: sum(1 for e in state.get("evidence", []) if e.tech == t.tech_id)
                                 for t in state["selected_techs"]}},
        "research_gaps": grade.missing if grade else {},
        "selection_weaknesses": state["selection_validation"].weaknesses if state.get("selection_validation") else {},
    }


def _resolve_agent(raw: dict) -> str | None:
    a = raw.get("assigned_agent")
    if a in REGISTRY:
        return a
    text = f"{raw.get('perspective', '')} {raw.get('objective', '')}".lower()
    for key, agent in (("trl", "trl_specialist"), ("readiness", "trl_specialist"), ("market", "market_specialist"),
                       ("stakeholder", "stakeholder_specialist"), ("domain", "domain_specialist"),
                       ("regulat", "regulation_specialist"), ("compet", "ecosystem_specialist"),
                       ("ecosystem", "ecosystem_specialist")):
        if key in text:
            return agent
    return "research_generalist" if text.strip() else None


def validate_plan(raw_tasks: list, tech_ids: list[str]) -> tuple[list[SubTask], list[str]]:
    """Deterministic plan validation. Returns (subtasks, repairs)."""
    repairs, tasks, claimed = [], [], {}
    for i, raw in enumerate(x for x in raw_tasks if isinstance(x, dict)):
        agent = _resolve_agent(raw)
        if agent is None:
            repairs.append(f"작업 {raw.get('task_id', i)}: Agent를 정할 수 없어 제외")
            continue
        if agent != raw.get("assigned_agent"):
            repairs.append(f"작업 {raw.get('task_id', i)}: 등록되지 않은 Agent '{raw.get('assigned_agent')}' → {agent}")
        techs = [t for t in raw.get("tech_ids", []) if t in tech_ids] or list(tech_ids)
        wt = worker_type(agent)
        if wt != "research":  # one specialist run per (perspective, technology)
            dup = [t for t in techs if t in claimed.get(wt, set())]
            techs = [t for t in techs if t not in dup]
            if dup:
                repairs.append(f"작업 {raw.get('task_id', i)}: {wt} 관점 중복 기술 {dup} 제거")
            if not techs:
                continue
            claimed.setdefault(wt, set()).update(techs)
        pr = raw.get("priority", 3)
        tasks.append((int(pr) if isinstance(pr, (int, float)) else 3, i, SubTask(
            task_id="", perspective=str(raw.get("perspective") or wt), assigned_agent=agent, tech_ids=techs,
            objective=str(raw.get("objective", ""))[:400],
            required_evidence=[str(x) for x in raw.get("required_evidence", [])][:5],
            success_criteria=[str(x) for x in raw.get("success_criteria", [])][:5], priority=int(pr) if
            isinstance(pr, (int, float)) else 3)))
    # minimum coverage guard: repair only what is missing, never add a perspective that is covered
    for p in MINIMUM_PERSPECTIVES:
        missing = [t for t in tech_ids if t not in claimed.get(p, set())]
        if missing:
            repairs.append(f"최소 관점 '{MINIMUM_KO[p]}' 누락 기술 {missing} → {SPECIALIST[p]} 작업 추가")
            tasks.append((1, 10_000 + len(tasks), SubTask(task_id="", perspective=MINIMUM_KO[p],
                                                         assigned_agent=SPECIALIST[p], tech_ids=missing,
                                                         objective=OBJECTIVE[p], priority=1)))
    tasks.sort(key=lambda x: (x[0], x[1]))
    minimum = [t for t in tasks if worker_type(t[2].assigned_agent) != "research"]
    extra = [t for t in tasks if worker_type(t[2].assigned_agent) == "research"]
    if len(minimum) + len(extra) > MAX_TASKS:
        repairs.append(f"작업 수 상한 {MAX_TASKS}개 초과 → 추가 관점 {len(minimum) + len(extra) - MAX_TASKS}개 제외")
        extra = extra[:max(0, MAX_TASKS - len(minimum))]
    used = {t[2].assigned_agent for t in minimum + extra}
    while extra and len(REGISTRY) - len(used) < REVIEWER_RESERVE:
        drop = max(extra, key=lambda x: (x[0], x[1]))     # lowest priority, latest research task
        extra.remove(drop)
        used = {t[2].assigned_agent for t in minimum + extra}
        repairs.append(f"검토자 풀 확보: 모든 Agent가 배정돼 우선순위가 가장 낮은 추가 관점 '{drop[2].perspective}'"
                       f"({drop[2].assigned_agent}) 제외")
    ordered = sorted(minimum + extra, key=lambda x: (x[0], x[1]))
    out = [t.model_copy(update={"task_id": f"T{n:02d}"}) for n, (_, _, t) in enumerate(ordered, 1)]
    return out, repairs


def fallback_plan(state: dict) -> tuple[list[dict], str]:
    """Rule planner used only when no LLM plan is available (new input without API key / cache, or invalid JSON).
    It still reads the State: a perspective is split per technology when the evidence for the technologies is
    unbalanced, research tasks are added for retrieval gaps and recorded selection weaknesses."""
    tech_ids = [t.tech_id for t in state["selected_techs"]]
    counts = {t: sum(1 for e in state.get("evidence", []) if e.tech == t) for t in tech_ids}
    unbalanced = len(tech_ids) > 1 and min(counts.values()) * 2 < max(counts.values())
    raw, notes = [], []
    for p in MINIMUM_PERSPECTIVES:
        if unbalanced and p in ("market", "domain"):   # thin-evidence technology gets its own task (separate budget)
            raw += [{"perspective": f"{MINIMUM_KO[p]} ({t})", "assigned_agent": SPECIALIST[p], "tech_ids": [t],
                     "objective": OBJECTIVE[p], "priority": 1} for t in tech_ids]
        else:
            raw.append({"perspective": MINIMUM_KO[p], "assigned_agent": SPECIALIST[p], "tech_ids": tech_ids,
                        "objective": OBJECTIVE[p], "priority": 1})
    if unbalanced:
        notes.append(f"기술별 근거 수 불균형 {counts} → 시장성·도메인 기술별 분할")
    grade = state.get("retrieval_grade")
    if grade and grade.missing:
        raw.append({"perspective": "evidence gap research", "assigned_agent": "research_generalist",
                    "tech_ids": sorted(grade.missing), "objective": f"공통 검색에서 빠진 요소 보완: {grade.missing}",
                    "priority": 2})
        notes.append(f"검색 공백 {grade.missing} → 보완 조사 추가")
    sv = state.get("selection_validation")
    if sv and any(sv.weaknesses.values()):
        raw.append({"perspective": "competition/ecosystem", "assigned_agent": "ecosystem_specialist",
                    "tech_ids": tech_ids, "objective": "선정 검증에서 기록된 약점을 경쟁·생태계 관점에서 확인", "priority": 3})
        notes.append("선정 약점 기록 → 경쟁·생태계 조사 추가")
    return raw, "LLM 계획을 쓸 수 없어 State 신호로 규칙 기반 계획을 만들었다" + (f": {'; '.join(notes)}" if notes else ".")


def orchestrator(state: dict) -> dict:
    tech_ids = [t.tech_id for t in state["selected_techs"]]
    with span("orchestrator_planner", run_id=state.get("run_id")):
        source, err = "llm", None
        try:
            sys = prompt("orchestrator.md").format(max_tasks=MAX_TASKS,
                                                   registry=json.dumps(registry_table(), ensure_ascii=False, indent=1))
            data = llm_json("generator", sys, json.dumps(_state_view(state), ensure_ascii=False), tag="orchestrator")
            raw, rationale = data.get("subtasks", []), str(data.get("rationale", ""))
            if not raw:
                raise ValueError("빈 계획")
        except (OfflineCacheMiss, ValueError) as ex:
            err = f"{type(ex).__name__}: {str(ex)[:160]}"
            raw, rationale = fallback_plan(state)
            source = "fallback"
        subtasks, repairs = validate_plan(raw, tech_ids)
    used, unused = used_unused(subtasks)
    coverage = {p: [t.task_id for t in subtasks if worker_type(t.assigned_agent) == p] for p in MINIMUM_PERSPECTIVES}
    plan = Plan(rationale=rationale, plan_source=source, coverage=coverage, repairs=repairs)
    decision(state, "orchestrator", f"{len(subtasks)} subtasks", reason=rationale, plan_source=source, error=err,
             subtasks=[(t.task_id, t.perspective, t.assigned_agent, t.tech_ids) for t in subtasks],
             used_agents=used, unused_agents=unused, repairs=repairs)
    warns = [f"Orchestrator: LLM 계획 대신 규칙 기반 계획 사용({err})"] if source == "fallback" else []
    return {"plan": plan, "subtasks": subtasks, "used_agents": used, "unused_agents": unused,
            "task_status": {t.task_id: "planned" for t in subtasks}, "status": "executing",
            "step_count": state.get("step_count", 0) + 1, "warnings": warns,
            "audit_log": audit("orchestrator", plan_source=source, n_subtasks=len(subtasks), used=used, unused=unused,
                               subtasks=[t.model_dump() for t in subtasks], repairs=repairs)}
