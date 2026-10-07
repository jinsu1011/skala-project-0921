"""Cross Reviewer: an Agent that was unused in the initial plan reviews a failed Task in REVIEW MODE.

The reviewer only reads the original SubTask, the original WorkerResult, the evidence that result cites and the Judge
feedback, and returns a ReviewResult. It never searches, never calls a worker runner, never changes assigned_agent
and never routes the graph. Selection is deterministic and bounded (MAX_REVIEWERS per failed task).
"""
from __future__ import annotations

import json
from collections import Counter

from agents.registry import REGISTRY
from graph.observability import decision, span
from graph.runtime import OfflineCacheMiss, audit, llm_json
from graph.state import ReviewResult

MAX_REVIEWERS = 2
KEYWORDS = {"trl": ["evidence validation", "release status", "general research"],
            "market": ["market knowledge", "competition", "ecosystem", "cost structure"],
            "stakeholder": ["source bias", "ecosystem", "partnerships", "evidence validation"],
            "domain": ["deployment constraints", "implementation risk", "operational considerations"],
            "research": ["evidence validation", "general research"]}


def select_reviewers(task, unused_agents: list[str], feedback: str = "", k: int = MAX_REVIEWERS) -> list[str]:
    """Candidates come only from the initial unused pool; the task's own agent is always excluded."""
    pool = [a for a in unused_agents if a in REGISTRY and a != task.assigned_agent]
    want = KEYWORDS.get(REGISTRY[task.assigned_agent].worker_type, []) + ["evidence validation"]
    text = f"{task.perspective} {task.objective} {feedback}".lower()

    def score(a: str) -> tuple:
        caps = REGISTRY[a].capabilities
        s = sum(2 for c in caps if c in want) + sum(1 for c in caps if c.split()[0] in text)
        return (-s, a)
    return sorted(pool, key=score)[:k]


SYS = """너는 Cross Reviewer이다. 이 작업을 처음 수행하지 않은 독립 검토자로서 REVIEW MODE로만 일한다.
너는 작업을 다시 수행하지 않고, 새 검색을 하지 않으며, 담당 Agent를 바꾸지 않는다. 검토 결과만 반환한다.
원래 SubTask, 원래 WorkerResult 요약, 그 결과가 인용한 근거 목록, Judge 피드백을 보고 다음을 점검한다.
- Judge 비판이 타당한가(agrees_with_judge)
- 구체적으로 어떤 근거가 빠졌는가(missing_evidence)
- 기존 근거 중 간과된 것이 있는가(overlooked_evidence_ids, 반드시 주어진 ID 중에서)
- 출처 편향, 한 출처 계열의 지배가 있는가(bias_risks)
- 다른 검색 방향이 필요한가(suggested_search_direction, 영어 검색 키워드 형태)
- 원래 Worker가 재시도에서 무엇에 집중해야 하는가(review_summary)
JSON: {"agrees_with_judge": true, "issues": ["..."], "missing_evidence": ["..."], "overlooked_evidence_ids": ["W:.."],
"bias_risks": ["..."], "suggested_search_direction": ["..."], "review_summary": "한국어 2문장"}"""


def evidence_facts(result, evidence: list, developer_groups: list[str]) -> dict:
    ev = [e for e in evidence if e.evidence_id in set(result.evidence_ids)] if result else []
    fam = Counter(e.origin_group for e in ev if e.kind == "web")
    total = sum(fam.values())
    top = fam.most_common(1)[0] if fam else ("", 0)
    dev = sum(n for g, n in fam.items() if g in developer_groups)
    return {"n_evidence": len(ev), "families": dict(fam), "dominant_family": top[0],
            "dominant_share": round(top[1] / total, 2) if total else 0.0,
            "developer_share": round(dev / total, 2) if total else 0.0,
            "items": [{"id": e.evidence_id, "origin": e.origin_group, "class": e.source_class,
                       "stances": e.stances, "claim": (e.claim or e.summary)[:200]} for e in ev[:40]]}


def _output_summary(result) -> object:
    out = getattr(result, "output", None)
    if out is None:
        return None
    if hasattr(out, "by_tech"):
        return {t: (getattr(v, "summary", v) if not isinstance(v, str) else v) for t, v in out.by_tech.items()}
    return str(out)[:1500]


def fallback_review(reviewer: str, task, feedback, facts: dict) -> ReviewResult:
    bias = []
    if facts["dominant_share"] > 0.5:
        bias.append(f"출처 계열 '{facts['dominant_family']}' 비중 {facts['dominant_share']:.0%}로 한 계열이 지배")
    if facts["developer_share"] > 0.3:
        bias.append(f"개발사 계열 근거 비중 {facts['developer_share']:.0%}")
    return ReviewResult(reviewer_agent=reviewer, target_task_id=task.task_id, agrees_with_judge=True,
                        issues=[feedback.reason] if feedback.reason else [], missing_evidence=list(feedback.missing_evidence),
                        bias_risks=bias, suggested_search_direction=[f"independent third-party {m}" for m in
                                                                     feedback.missing_evidence[:2]],
                        review_summary=f"근거 {facts['n_evidence']}건, 출처 계열 {len(facts['families'])}개를 점검했다. "
                                       "Judge가 지적한 빠진 근거를 독립 출처에서 보완하는 데 집중해야 한다.",
                        mode="fallback")


def cross_reviewer(payload: dict) -> dict:
    """Send target. payload: {reviewer_agent, subtask, worker_result, feedback, evidence, developer_groups, run_id}."""
    reviewer, task, fb = payload["reviewer_agent"], payload["subtask"], payload["feedback"]
    assert reviewer != task.assigned_agent, "original agent cannot review its own task"
    facts = evidence_facts(payload.get("worker_result"), payload.get("evidence", []), payload.get("developer_groups", []))
    meta = dict(run_id=payload.get("run_id"), task_id=task.task_id, perspective=task.perspective,
                assigned_agent=task.assigned_agent, reviewer_agent=reviewer, attempt=task.attempt, mode="review")
    with span(f"cross_review:{task.task_id}:{reviewer}", **meta):
        body = {"reviewer_profile": REGISTRY[reviewer].model_dump(), "subtask": task.model_dump(),
                "worker_result": {"status": getattr(payload.get("worker_result"), "status", None),
                                  "output_summary": _output_summary(payload.get("worker_result"))},
                "evidence_facts": facts, "judge_feedback": fb.model_dump()}
        try:
            d = llm_json("judge", SYS, json.dumps(body, ensure_ascii=False, default=str), tag=f"cross_review:{task.task_id}")
            if not d:
                raise ValueError("빈 응답")
            ids = {x["id"] for x in facts["items"]}
            rr = ReviewResult(reviewer_agent=reviewer, target_task_id=task.task_id,
                              agrees_with_judge=bool(d.get("agrees_with_judge", True)),
                              issues=[str(x) for x in d.get("issues", [])][:5],
                              missing_evidence=[str(x) for x in d.get("missing_evidence", [])][:5],
                              overlooked_evidence_ids=[x for x in d.get("overlooked_evidence_ids", []) if x in ids],
                              bias_risks=[str(x) for x in d.get("bias_risks", [])][:5],
                              suggested_search_direction=[str(x) for x in d.get("suggested_search_direction", [])][:3],
                              review_summary=str(d.get("review_summary", ""))[:600])
        except (OfflineCacheMiss, ValueError) as ex:
            rr = fallback_review(reviewer, task, fb, facts)
            decision(payload, "cross_reviewer", "fallback_review", reason=str(ex)[:160], **meta)
    decision(payload, "cross_reviewer", "reviewed", reason=rr.review_summary, **meta,
             agrees=rr.agrees_with_judge)
    return {"review_results": [rr], "audit_log": audit("cross_reviewer", **meta, agrees=rr.agrees_with_judge,
                                                       mode_=rr.mode)}
