"""Coordination layer of the Orchestrator-Workers graph (no research happens here).

dispatch_workers / dispatch_retry   -> dynamic fan-out with Send, one Send per SubTask in state["subtasks"]
result_aggregator (defer)           -> latest attempt per task_id -> perspective results + extra findings
perspective_judge                   -> existing Judge (per perspective) mapped to task_id feedback items
retry_router                        -> code rules: eligibility (attempt == 0), original agent, unused reviewers
dispatch_reviews                    -> bounded Send to cross_reviewer (or straight to Agent 0 when no reviewer)
report_quality_evaluator            -> deterministic checks + LLM Judge, then rewrite / evidence retry / finalize
"""
from __future__ import annotations

import json
import re

from langgraph.types import Send

from agents.cross_review import select_reviewers
from agents.registry import MINIMUM_KO, MINIMUM_PERSPECTIVES, REGISTRY, worker_type
from graph.observability import decision, span
from graph.runtime import OfflineCacheMiss, audit, config, lexicon_hits, llm_json
from graph.state import (JudgeFeedbackItem, JudgeResult, PerspectiveResult, ReportQuality, TechAssessment)

MAX_FEEDBACK_RETRY = 1                                  # attempt 0 = initial, attempt 1 = the only feedback retry
MAX_REPORT = config()["graph"]["max_report_retries"]    # 1 bounded rewrite
MAX_STEPS = 40                                          # control-node budget (separate from recursion_limit)
PAGE_LIMIT = 10
FINAL = ("passed", "FAILED_AFTER_RETRY", "PARTIAL", "excluded")


def _payload(state: dict, task, retry=None, previous=None) -> dict:
    return {"subtask": task, "retry_task": retry, "previous": previous, "selected_techs": state["selected_techs"],
            "evidence": state.get("evidence", []), "run_id": state.get("run_id"), "trace_id": state.get("trace_id")}


# ---------------------------------------------------------------- fan-out
def dispatch_workers(state: dict) -> list[Send]:
    """Initial fan-out: the number of workers equals the number of runtime SubTasks."""
    return [Send("worker", _payload(state, t)) for t in state["subtasks"]]


def dispatch_retry(state: dict) -> list[Send]:
    """After Agent 0: only the retried tasks, each to its original assigned_agent (copied from State)."""
    by_id = {t.task_id: t for t in state["subtasks"]}
    status = state.get("task_status", {})
    latest = {}
    for rt in state.get("retry_tasks", []):
        if status.get(rt.task_id) == "retrying":
            latest[rt.task_id] = rt
    sends = []
    for tid, rt in sorted(latest.items()):
        task = by_id[tid]
        assert rt.assigned_agent == task.assigned_agent, "retry must go to the original agent"
        sends.append(Send("worker", _payload(state, task.model_copy(update={"attempt": rt.attempt}), rt,
                                             state.get("worker_results", {}).get(tid))))
    return sends or "result_aggregator"


# ---------------------------------------------------------------- aggregation (raw results -> perspective payload)
def latest_results(state: dict) -> dict:
    return dict(state.get("worker_results", {}))   # the reducer already keeps the newest attempt per task_id


def result_aggregator(state: dict) -> dict:
    techs = state["selected_techs"]
    by_type: dict[str, dict[str, TechAssessment]] = {p: {} for p in MINIMUM_PERSPECTIVES}
    extra, retry_count, warns = {}, {p: 0 for p in MINIMUM_PERSPECTIVES}, []
    for tid, r in latest_results(state).items():
        wt = worker_type(r.assigned_agent)
        if wt in by_type:
            retry_count[wt] = max(retry_count[wt], r.attempt)
        if r.output is None:
            warns.append(f"작업 {tid}({r.perspective}, {r.assigned_agent}) 결과 없음: {r.error}")
            continue
        if wt in by_type:
            by_type[wt].update(r.output.by_tech)
        else:
            extra[tid] = r.output
    out = {"extra_findings": extra, "perspective_retry_count": retry_count, "warnings": warns,
           "step_count": state.get("step_count", 0) + 1}
    for p in MINIMUM_PERSPECTIVES:
        for t in techs:   # missing technology (worker excluded) -> explicit 판단 보류 placeholder, never a silent gap
            by_type[p].setdefault(t.tech_id, TechAssessment(summary="", confidence="low",
                                                            limitations=["작업 실패로 결과 없음(판단 보류)"]))
        out[f"{p}_result"] = PerspectiveResult(perspective=p, by_tech=by_type[p])
    decision(state, "result_aggregator", "aggregated", tasks={k: (v.attempt, v.status) for k, v in
                                                              latest_results(state).items()})
    out["audit_log"] = audit("result_aggregator", tasks={k: [v.attempt, v.status] for k, v in latest_results(state).items()})
    return out


# ---------------------------------------------------------------- judge -> task-level feedback
def _failing_techs(score, techs: list[str], p: str) -> list[str]:
    c = score.checks
    if p == "trl":
        bad = [t for t, n in c.bound_origins.items() if n < 1]
    else:
        bad = [t for t in c.pro_origins if c.pro_origins[t] < 2 or c.con_origins.get(t, 0) < 2]
    return [t for t in techs if t in bad]


def _missing(score, p: str, techs: list[str]) -> list[str]:
    c, out = score.checks, []
    for t in techs:
        if p == "trl" and c.bound_origins.get(t, 1) < 1:
            out.append(f"{t}: TRL 상·하한을 뒷받침하는 독립 출처 근거")
        elif p != "trl":
            if c.pro_origins.get(t, 2) < 2:
                out.append(f"{t}: 개발사와 독립된 장점(pro) 근거 계열 {2 - c.pro_origins.get(t, 0)}개 이상")
            if c.con_origins.get(t, 2) < 2:
                out.append(f"{t}: 독립된 한계(con) 근거 계열 {2 - c.con_origins.get(t, 0)}개 이상")
    if c.max_origin_share > 0.5:
        out.append(f"단일 출처 계열 비중 {c.max_origin_share:.0%} → 50% 이하가 되도록 다른 계열 근거")
    return out


def _extra_check(state: dict, tid: str, r) -> tuple[bool, str, list[str]]:
    """Deterministic judge for research-agent findings: grounded sentences, >= 2 origin families, neutral wording."""
    ev = {e.evidence_id: e for e in state.get("evidence", [])}
    fams = {ev[i].origin_group for i in r.evidence_ids if i in ev}
    text = " ".join(r.output.by_tech.values()) if r.output else ""
    problems, missing = [], []
    if not text.strip():
        problems.append("근거가 붙은 요약 문장이 없음")
        missing.append(f"{r.perspective}: 기술명을 직접 언급하는 독립 출처 근거")
    if len(fams) < 2:
        problems.append(f"출처 계열 {len(fams)}개(2개 이상 필요)")
        missing.append(f"{r.perspective}: 서로 다른 출처 계열의 근거")
    if lexicon_hits(text):
        problems.append("우열·추천 어휘 포함")
    return not problems, "; ".join(problems), missing


def perspective_judge(state: dict) -> dict:
    from agents.judge import judge

    with span("perspective_judge", run_id=state.get("run_id")):
        out = judge(state)
    scores = out["judge_scores"]
    techs = [t.tech_id for t in state["selected_techs"]]
    results = latest_results(state)
    status = dict(state.get("task_status", {}))
    items, warns = [], []
    for task in state["subtasks"]:
        tid, r = task.task_id, results.get(task.task_id)
        if status.get(tid) in FINAL:
            continue
        attempt = r.attempt if r else task.attempt
        wt = worker_type(task.assigned_agent)
        if r is None or r.status == "error":
            ok, reason = False, f"Worker 실행 실패: {r.error if r else '결과 없음'}"
            missing, fb = [f"{task.perspective}: 작업 결과 전체"], "같은 작업을 다시 실행해 결과를 만든다."
        elif wt == "research":
            ok, reason, missing = _extra_check(state, tid, r)
            fb = f"{task.perspective} 근거 보완: {'; '.join(missing)}" if missing else reason
        else:
            s = scores.get(wt)
            if s is None or s.passed:
                ok = True
            else:   # global failure (LLM score, origin share, wording) hits every task of the perspective;
                c = s.checks  # a per-technology evidence deficit hits only the tasks holding that technology
                global_fail = (min(s.grounding, s.neutrality, s.source_diversity, s.completeness) < 4
                               or c.max_origin_share > 0.5 or c.lexicon_hits > 0)
                ok = not global_fail and not set(task.tech_ids) & set(_failing_techs(s, techs, wt))
            missing = _missing(s, wt, task.tech_ids) if s and not ok else []
            reason = out["judge_feedback"].get(wt, "") if not ok else "통과"
            fb = (f"[{tid}] " + "; ".join(missing)) if missing else reason
        retry = (not ok) and attempt < MAX_FEEDBACK_RETRY
        items.append(JudgeFeedbackItem(task_id=tid, passed=ok, reason=reason[:400], missing_evidence=missing,
                                       feedback=fb[:500], retry_required=retry))
        if ok:
            status[tid] = "passed"
        elif not retry:   # attempt 1 already used: no more retries (deterministic)
            status[tid] = "PARTIAL" if r is not None and r.status == "partial" else (
                "excluded" if r is None or r.output is None else "FAILED_AFTER_RETRY")
            warns.append(f"판정 불확실: 작업 {tid}({task.perspective}, {task.assigned_agent})이 재시도 1회 후에도 "
                         f"기준 미달 → {status[tid]}. 사유: {reason[:120]}")
    jr = JudgeResult(passed=all(i.passed for i in items), feedback_items=items)
    decision(state, "perspective_judge", "PASS" if jr.passed else "FAIL",
             reason="; ".join(f"{i.task_id}:{i.reason[:60]}" for i in items if not i.passed),
             failed=[i.task_id for i in items if not i.passed], retry=[i.task_id for i in items if i.retry_required])
    return {**out, "judge_result": jr, "task_status": status, "warnings": out.get("warnings", []) + warns,
            "status": "judge_feedback" if any(i.retry_required for i in items) else "reporting",
            "step_count": state.get("step_count", 0) + 1}


def route_after_judge(state: dict) -> str:
    if state.get("step_count", 0) >= state.get("max_steps", MAX_STEPS):
        return "report_writer"
    return "retry_router" if any(i.retry_required for i in state["judge_result"].feedback_items) else "report_writer"


# ---------------------------------------------------------------- retry router (no research, no agent change)
def retry_router(state: dict) -> dict:
    by_id = {t.task_id: t for t in state["subtasks"]}
    status = dict(state.get("task_status", {}))
    unused = list(state.get("unused_agents", []))
    subtasks, assignments, events = [], {}, []
    retry_ids = []
    for item in state["judge_result"].feedback_items:
        task = by_id.get(item.task_id)
        if not item.retry_required or task is None:
            continue
        if task.attempt >= MAX_FEEDBACK_RETRY:          # eligibility rule enforced again in code
            status[item.task_id] = "FAILED_AFTER_RETRY"
            continue
        retry_ids.append(item.task_id)
        status[item.task_id] = "retrying"
        assignments[item.task_id] = select_reviewers(task, unused, item.feedback)
    for t in state["subtasks"]:   # advance the attempt counter only; assigned_agent is copied unchanged
        subtasks.append(t.model_copy(update={"attempt": t.attempt + 1}) if t.task_id in retry_ids else t)
    for tid in retry_ids:
        decision(state, "retry_router", "retry", task_id=tid, assigned_agent=by_id[tid].assigned_agent,
                 attempt=by_id[tid].attempt + 1, reviewers=assignments[tid], unused_agents=unused,
                 reason="unused 없음 → Cross-Review 생략" if not assignments[tid] else "")
        events += audit("retry_router", task_id=tid, assigned_agent=by_id[tid].assigned_agent,
                        attempt=by_id[tid].attempt + 1, reviewers=assignments[tid])
    return {"subtasks": subtasks, "task_status": status, "review_assignments": assignments,
            "step_count": state.get("step_count", 0) + 1, "audit_log": events}


def dispatch_reviews(state: dict):
    """Bounded cross-review fan-out; no unused agent -> skip review and go straight to Agent 0 (fallback)."""
    by_id = {t.task_id: t for t in state["subtasks"]}
    fb = {f.task_id: f for f in state["judge_result"].feedback_items}
    results = state.get("worker_results", {})
    sends = []
    for tid, reviewers in sorted(state.get("review_assignments", {}).items()):
        if state.get("task_status", {}).get(tid) != "retrying":
            continue
        task = by_id[tid]
        devs = [g for t in state["selected_techs"] if t.tech_id in task.tech_ids for g in t.developer_groups]
        for a in reviewers:
            sends.append(Send("cross_reviewer", {
                "reviewer_agent": a, "subtask": task.model_copy(update={"attempt": task.attempt - 1}),
                "worker_result": results.get(tid), "feedback": fb[tid], "evidence": state.get("evidence", []),
                "developer_groups": devs, "run_id": state.get("run_id"), "trace_id": state.get("trace_id")}))
    return sends or "feedback_retry_coordinator"


# ---------------------------------------------------------------- report quality evaluator (hybrid)
QSYS = """너는 보고서 품질 평가자(LLM Judge)이다. 보고서를 고치지 않고 채점만 한다. 각 항목 1~5점(4점 이상 통과).
- groundedness: 주장이 검색된 출처([n] 인용)로 추적되는가, 근거 없는 수치·주장이 없는가
- neutrality: 특정 기술 추천이나 우열 판정이 없는가
- bias_control: 단일 출처나 유리한 근거에 치우치지 않았는가(개발사 자료 편중 포함)
- coverage: 기술 성숙도(TRL), 시장성, 이해관계자, 도메인 적용 4개 관점을 모두 다뤘는가
issues: 문제 문장·문단을 짧게 인용하고 종류를 적는다. kind는 "writing"(서술·형식 문제) 또는 "evidence"(근거 자체가 부족).
evidence 문제면 perspective에 trl/market/stakeholder/domain 중 하나와 tech_id를 적는다.
JSON: {"groundedness": 4, "neutrality": 4, "bias_control": 4, "coverage": 4,
"issues": [{"kind": "writing", "text": "...", "perspective": "", "tech_id": ""}]}"""


def deterministic_checks(state: dict) -> tuple[dict[str, bool], list[str], dict[str, str]]:
    from agents.report_writer import check_report

    md, refs = state["report_markdown"], state["references"]
    issues = check_report(md, refs)
    body = md.split("# REFERENCE")[0]
    writing = list(issues)
    det = {"groundedness": not any(i.startswith(("근거 없는 문장", "REFERENCE 불일치")) for i in issues),
           "neutrality": not lexicon_hits(body),
           "required_sections": "# SUMMARY" in md and "# REFERENCE" in md}
    if not det["required_sections"]:
        writing.append("필수 목차(SUMMARY, REFERENCE) 누락")
    # bias control: web references from one origin family must stay <= 50%
    ev = {e.evidence_id: e for e in state.get("evidence", [])}
    fam = [ev[i].origin_group for r in refs if r.kind == "web" for i in r.evidence_ids[:1] if i in ev]
    share = max((fam.count(f) / len(fam) for f in set(fam)), default=0.0)
    det["bias_control"] = share <= 0.5
    if not det["bias_control"]:
        writing.append(f"REFERENCE 웹 출처 한 계열 비중 {share:.0%}")
    # coverage: every minimum perspective has an evaluated (non-placeholder) result per technology
    evidence_issues = {}
    for task in state["subtasks"]:
        wt = worker_type(task.assigned_agent)
        if wt not in MINIMUM_PERSPECTIVES:
            continue
        res = state.get(f"{wt}_result")
        for t in task.tech_ids:
            ta = res.by_tech.get(t) if res else None
            empty = ta is None or (not ta.summary and not ta.criteria)
            if empty:
                evidence_issues[task.task_id] = f"{MINIMUM_KO[wt]} 관점 {t} 평가 결과 없음"
    det["coverage"] = not evidence_issues and all(f"{p}_result" in state for p in MINIMUM_PERSPECTIVES)
    return det, writing, evidence_issues


def report_quality_evaluator(state: dict) -> dict:
    det, writing, evidence = deterministic_checks(state)
    n = state.get("report_retry_count", 0)
    scores, mode = {}, "llm"
    with span("report_quality_evaluator", run_id=state.get("run_id"), attempt=n):
        try:
            d = llm_json("judge", QSYS, state["report_markdown"][:24000], tag="report_quality")
            if not d:
                raise ValueError("빈 응답")
            scores = {k: int(d.get(k, 0) or 0) for k in ("groundedness", "neutrality", "bias_control", "coverage")}
            for it in d.get("issues", []):
                if not isinstance(it, dict):
                    continue
                if it.get("kind") == "evidence" and it.get("perspective") in MINIMUM_PERSPECTIVES:
                    for task in state["subtasks"]:
                        if worker_type(task.assigned_agent) == it["perspective"] and \
                                (not it.get("tech_id") or it["tech_id"] in task.tech_ids):
                            evidence.setdefault(task.task_id, f"LLM Judge: {str(it.get('text', ''))[:120]}")
                elif scores.get("groundedness", 5) < 4 or scores.get("neutrality", 5) < 4:
                    writing.append(f"LLM Judge: {str(it.get('text', ''))[:120]}")
        except (OfflineCacheMiss, ValueError):
            mode = "deterministic_only"
    llm_ok = all(v >= 4 for v in scores.values()) if scores else True
    passed = all(det.values()) and llm_ok and not evidence
    status = state.get("task_status", {})
    by_id = {t.task_id: t for t in state["subtasks"]}
    retryable = {tid: why for tid, why in evidence.items()
                 if tid in by_id and by_id[tid].attempt < MAX_FEEDBACK_RETRY and status.get(tid) != "retrying"}
    if passed:
        action = "pass"
    elif retryable:
        action = "evidence_retry"
    elif writing and n < MAX_REPORT:
        action = "rewrite"
    else:
        action = "finalize"
    q = ReportQuality(passed=passed, scores=scores, deterministic=det, writing_issues=writing,
                      evidence_issues=evidence, action=action, judge_mode=mode)
    out = {"report_quality": q, "step_count": state.get("step_count", 0) + 1,
           "audit_log": audit("report_quality_evaluator", action=action, det=det, scores=scores, mode=mode,
                              writing=writing[:10], evidence=evidence)}
    warns = [f"판정 불확실: 보고서 품질 평가에서 작업 {tid} 근거 문제({why}) - 재시도 1회 이미 사용"
             for tid, why in evidence.items() if tid not in retryable]
    if action == "rewrite":
        out["report_retry_count"] = n + 1
    elif action == "evidence_retry":
        out["judge_result"] = JudgeResult(passed=False, source="report_quality", feedback_items=[
            JudgeFeedbackItem(task_id=tid, passed=False, reason=f"보고서 품질 평가: {why}", missing_evidence=[why],
                              feedback=f"[{tid}] {why}", retry_required=True) for tid, why in sorted(retryable.items())])
        out["task_status"] = {**status, **{tid: "quality_feedback" for tid in retryable}}
        out["status"] = "quality_feedback"
    elif action == "finalize" and not passed:
        from agents.report_writer import postprocess

        md, refs, left = postprocess(state["report_markdown"], state["references"])
        out.update({"report_markdown": md, "references": refs})
        warns += [f"보고서 검수: 수정 한도 소진 후 결정적 후처리, 남은 문제 {x}" for x in left]
    out["warnings"] = warns
    decision(state, "report_quality_evaluator", action, reason="; ".join(writing[:3] + list(evidence.values())[:3]),
             scores=scores, deterministic=det, mode=mode)
    return out


def route_after_quality(state: dict) -> str:
    action = state["report_quality"].action
    if state.get("step_count", 0) >= state.get("max_steps", MAX_STEPS):
        return "pdf_renderer"
    return {"rewrite": "report_writer", "evidence_retry": "retry_router"}.get(action, "pdf_renderer")
