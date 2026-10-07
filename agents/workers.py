"""Worker execution for one SubTask (dynamic fan-out target).

`worker` receives a Send payload {subtask, retry_task, previous, selected_techs, evidence, run_id, trace_id} and runs
the runner of subtask.assigned_agent only. Specialists reuse the existing perspective agents unchanged; research
agents run an open-perspective web research loop. Every run returns one WorkerResult (task_id-keyed reducer).

Fallback (Notion B, 일부 worker 실패): an exception becomes status="error". At attempt 0 the Judge marks the task
retry_required (one retry by the same agent). At attempt 1 the previous attempt's output is kept as status="partial"
(or the task is excluded when there is none) and the run continues.
"""
from __future__ import annotations

from graph.observability import decision, span
from graph.runtime import audit
from graph.state import ExtraFinding, SubTask, WorkerResult

from agents.registry import REGISTRY


def _specialist(fn, key: str):
    def run(sub_state: dict) -> tuple[object, list, list, list]:
        out = fn(sub_state)
        return out[f"{key}_result"], out.get("evidence", []), out.get("audit_log", []), out.get("warnings", [])
    return run


def _research(sub_state: dict, task: SubTask, instruction: str) -> tuple[object, list, list, list]:
    """Open-perspective research: symmetric pro/con web queries per technology -> origin-family control ->
    summarize_sources (every sentence keeps evidence ids). Same budget for every technology."""
    from agents.perspective import search_name
    from tools.evidence import assign_origin_groups, cap_origin_share
    from tools.summarize import summarize
    from tools.web_search import search_web

    finding = ExtraFinding(perspective=task.perspective)
    evidence, events = [], []
    for t in sub_state["selected_techs"]:
        sn = search_name(t)
        plan = [("pro", f"{sn} {task.perspective}"), ("con", f"{sn} {task.perspective} risks limitations")]
        plan += [("neutral", f"{sn} {x}") for x in task.required_evidence[:1]]
        if instruction:
            plan += [("neutral", f"{sn} {x}") for x in sub_state.get("retry_missing", [])[:2]]
        seen = {}
        for stance, q in plan:
            finding.queries.append(q)
            for e in search_web(q, stance=stance, tech=t.tech_id, perspective=task.perspective, attempt=task.attempt):
                seen.setdefault(e.evidence_id, e)
        evs = cap_origin_share(assign_origin_groups(list(seen.values())))
        focus = (f"{t.name}: {task.objective} (한국어로 2문장 이내, 기술 소개 말고 이 관점의 사실만)"
                 + (f" / 재시도 지시: {instruction}" if instruction else ""))
        sents = summarize(evs[:20], focus, tag=f"{task.task_id}:{t.tech_id}:a{task.attempt}:summary")
        used = sorted({i for s in sents for i in s["evidence_ids"]})
        finding.by_tech[t.tech_id] = " ".join(f"{s['text'].rstrip('.')} [{', '.join(s['evidence_ids'])}]."
                                              if "[" not in s["text"] else s["text"] for s in sents)
        evidence += [e for e in evs if e.evidence_id in used]
        events += audit(task.assigned_agent, task_id=task.task_id, tech=t.tech_id, attempt=task.attempt,
                        queries=finding.queries, evidence=used)
    return finding, evidence, events, []


def runners() -> dict:
    from agents.domain import domain_evaluator
    from agents.market import market_evaluator
    from agents.stakeholder import stakeholder_evaluator
    from agents.tech_research import trl_assessor

    return {"trl": _specialist(trl_assessor, "trl"), "market": _specialist(market_evaluator, "market"),
            "stakeholder": _specialist(stakeholder_evaluator, "stakeholder"),
            "domain": _specialist(domain_evaluator, "domain")}


def run_task(payload: dict) -> dict:
    task: SubTask = payload["subtask"]
    retry = payload.get("retry_task")
    prof = REGISTRY[task.assigned_agent]
    instruction = retry.retry_instruction if retry else ""
    techs = [t for t in payload["selected_techs"] if t.tech_id in task.tech_ids] or payload["selected_techs"]
    # sub-state in the shape the existing agents read: attempt number and feedback per perspective key
    sub = {"selected_techs": techs, "evidence": payload.get("evidence", []),
           "perspective_retry_count": {prof.worker_type: task.attempt},
           "judge_feedback": {prof.worker_type: instruction} if instruction else {},
           "retry_missing": retry.missing_evidence if retry else []}
    if prof.worker_type == "research":
        output, evidence, events, warns = _research(sub, task, instruction)
    else:
        output, evidence, events, warns = runners()[prof.worker_type](sub)
    return {"output": output, "evidence": evidence, "audit_log": events, "warnings": warns}


def worker(payload: dict) -> dict:
    task: SubTask = payload["subtask"]
    meta = dict(run_id=payload.get("run_id"), task_id=task.task_id, perspective=task.perspective,
                assigned_agent=task.assigned_agent, attempt=task.attempt, mode="execute")
    with span(f"worker:{task.task_id}:{task.assigned_agent}:a{task.attempt}", **meta):
        try:
            out = run_task(payload)
            res = WorkerResult(task_id=task.task_id, perspective=task.perspective, assigned_agent=task.assigned_agent,
                               status="ok", output=out["output"], attempt=task.attempt,
                               evidence_ids=sorted({e.evidence_id for e in out["evidence"]}))
            decision(payload, "worker", "ok", task_id=task.task_id, assigned_agent=task.assigned_agent,
                     attempt=task.attempt, evidence=len(res.evidence_ids))
            return {"worker_results": {task.task_id: res}, "evidence": out["evidence"],
                    "audit_log": out["audit_log"] + audit("worker", **meta, status="ok"), "warnings": out["warnings"]}
        except Exception as ex:  # noqa: BLE001 - worker failure is a recorded state, not a crash (fallback policy)
            prev = payload.get("previous")
            keep = prev is not None and prev.output is not None
            res = WorkerResult(task_id=task.task_id, perspective=task.perspective, assigned_agent=task.assigned_agent,
                               status="partial" if keep and task.attempt > 0 else "error",
                               output=prev.output if keep and task.attempt > 0 else None,
                               evidence_ids=prev.evidence_ids if keep and task.attempt > 0 else [],
                               error=f"{type(ex).__name__}: {str(ex)[:300]}", attempt=task.attempt)
            decision(payload, "worker", res.status, reason=res.error, task_id=task.task_id,
                     assigned_agent=task.assigned_agent, attempt=task.attempt)
            return {"worker_results": {task.task_id: res}, "last_error": res.error,
                    "audit_log": audit("worker", **meta, status=res.status, error=res.error)}
