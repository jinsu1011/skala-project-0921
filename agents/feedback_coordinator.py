"""Agent 0 (feedback_retry_coordinator). Runs only after Judge / Report-Quality feedback.

Combines the original SubTask, the original WorkerResult, the Judge feedback and zero or more ReviewResults into a
precise retry instruction ("what exactly should the original agent improve?"). It never chooses who runs next: the
RetryTask's assigned_agent is copied by code from SubTask.assigned_agent and any agent named by the LLM is ignored.
"""
from __future__ import annotations

import json

from agents.registry import FEEDBACK_COORDINATOR
from graph.observability import decision, span
from graph.runtime import OfflineCacheMiss, audit, llm_json
from graph.state import RetryTask

SYS = """너는 Agent 0(feedback_retry_coordinator)이다. Supervisor가 아니다. 다음에 어떤 Agent를 실행할지 정하지 않는다.
원래 SubTask, 원래 결과, Judge 피드백, 독립 검토자(Cross Reviewer)의 검토 결과를 합쳐
원래 담당 Agent가 재시도에서 정확히 무엇을 개선해야 하는지 지시문을 만든다.
지시문은 구체적이어야 한다: 어떤 종류의 출처를 피하고 어떤 독립 근거를 찾을지, 어떤 검색 방향을 쓸지 적는다.
우열 판정이나 추천을 요구하지 않는다(중립 평가).
JSON: {"retry_instruction": "한국어 2~3문장", "missing_evidence": ["영어 검색 키워드 형태 1~3개"]}"""


def build_retry_task(task, feedback, reviews: list) -> tuple[RetryTask, str]:
    rev_lines = [f"{r.reviewer_agent}: {r.review_summary}" + (f" (편향: {'; '.join(r.bias_risks)})" if r.bias_risks else "")
                 for r in reviews]
    body = {"subtask": task.model_dump(), "judge_feedback": feedback.model_dump(),
            "reviews": [r.model_dump() for r in reviews]}
    mode = "llm"
    try:
        d = llm_json("generator", SYS, json.dumps(body, ensure_ascii=False), tag=f"agent0:{task.task_id}")
        instruction = str(d.get("retry_instruction", "")).strip()
        missing = [str(x) for x in d.get("missing_evidence", [])][:3]
        if not instruction:
            raise ValueError("빈 지시문")
    except (OfflineCacheMiss, ValueError):
        mode = "fallback"
        dirs = [x for r in reviews for x in r.suggested_search_direction]
        missing = list(dict.fromkeys(list(feedback.missing_evidence) + [x for r in reviews for x in r.missing_evidence]))[:3]
        instruction = (f"Judge 지적: {feedback.feedback or feedback.reason}. "
                       + (f"검토자 의견: {' / '.join(r.review_summary for r in reviews)[:300]}. " if reviews else "")
                       + "같은 출처 계열을 늘리지 말고 독립 제3자 근거를 찾는다"
                       + (f"(검색 방향: {', '.join(dirs[:3])})." if dirs else "."))
    # assigned_agent and task_id come from State, never from the LLM output
    rt = RetryTask(task_id=task.task_id, assigned_agent=task.assigned_agent, original_objective=task.objective,
                   judge_feedback=feedback.feedback or feedback.reason, reviewer_feedback=rev_lines,
                   retry_instruction=instruction, missing_evidence=missing, attempt=task.attempt)  # router already advanced it
    return rt, mode


def feedback_retry_coordinator(state: dict) -> dict:
    by_id = {t.task_id: t for t in state["subtasks"]}
    fb = {f.task_id: f for f in state["judge_result"].feedback_items}
    retry_ids = [tid for tid, s in sorted(state.get("task_status", {}).items()) if s == "retrying"]
    out, events = [], []
    for tid in retry_ids:
        task = by_id[tid]
        reviews = [r for r in state.get("review_results", []) if r.target_task_id == tid
                   and r.reviewer_agent != task.assigned_agent]
        with span(f"agent0:{tid}", run_id=state.get("run_id"), task_id=tid, perspective=task.perspective,
                  assigned_agent=task.assigned_agent, attempt=task.attempt):
            rt, mode = build_retry_task(task, fb[tid], reviews)
        out.append(rt)
        decision(state, FEEDBACK_COORDINATOR, "retry_instruction", reason=rt.retry_instruction, task_id=tid,
                 assigned_agent=rt.assigned_agent, attempt=rt.attempt, reviewers=[r.reviewer_agent for r in reviews],
                 mode=mode)
        events += audit(FEEDBACK_COORDINATOR, task_id=tid, assigned_agent=rt.assigned_agent, attempt=rt.attempt,
                        reviewers=[r.reviewer_agent for r in reviews], mode=mode)
    return {"retry_tasks": out, "step_count": state.get("step_count", 0) + 1, "audit_log": events}
