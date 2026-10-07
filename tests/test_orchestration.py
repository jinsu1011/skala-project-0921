"""Orchestrator-Workers behaviour with stub workers / judge (no model, no API).

The graph is the real one; only the leaf work (RAG prep, worker execution, synthesis, the Judge's LLM scoring, report
writing, LLM calls) is replaced, so routing, reducers, reviewer selection, Agent 0 and retry limits are the real code.
"""
from collections import Counter

import pytest

import graph.workflow as wf
from agents import orchestrator as orch
from agents.cross_review import MAX_REVIEWERS, cross_reviewer, select_reviewers
from agents.feedback_coordinator import build_retry_task
from agents.registry import REGISTRY, used_unused
from graph.orchestration import MAX_FEEDBACK_RETRY, dispatch_workers
from graph.runtime import OfflineCacheMiss, audit
from graph.state import (Evidence, ExtraFinding, JudgeFeedbackItem, JudgeScore, PerspectiveResult, ReviewResult,
                         SubTask, SynthesisResult, TechAssessment, WorkerResult, merge_worker_results)

TECHS = ["turboquant", "itme"]


def raw_task(agent, perspective, techs=TECHS, prio=1):
    return {"perspective": perspective, "assigned_agent": agent, "tech_ids": techs, "objective": f"{perspective} 평가",
            "required_evidence": ["independent evidence"], "success_criteria": ["2 origin families"], "priority": prio}


PLAN_6 = {"rationale": "시장성은 기술별로 나누고 규제 관점을 추가", "subtasks": [
    raw_task("trl_specialist", "technology readiness"),
    raw_task("market_specialist", "marketability (SW)", ["turboquant"]),
    raw_task("market_specialist", "marketability (HW)", ["itme"]),
    raw_task("stakeholder_specialist", "stakeholder"),
    raw_task("domain_specialist", "domain applicability"),
    raw_task("regulation_specialist", "export control regulation", prio=2)]}


def ev(i, group, tech="turboquant"):
    return Evidence(evidence_id=f"W:{i}", kind="web", claim=f"c{i}", tech=tech, source_group=group, origin_group=group,
                    source_url=f"https://{group}/{i}")


class Harness:
    def __init__(self, monkeypatch, plan=PLAN_6, fail: dict | None = None, worker_error: dict | None = None,
                 quality_evidence: dict | None = None):
        self.calls = Counter()
        self.worker_calls: list[tuple[str, str, int]] = []
        self.fail = dict(fail or {})              # perspective -> number of judge rounds it fails
        self.worker_error = dict(worker_error or {})  # task_id -> attempts that raise
        self.quality_evidence = dict(quality_evidence or {})
        self.reviewer_calls: list[tuple[str, str]] = []
        self.llm_payloads: list[str] = []

        def simple(name):
            def f(state):
                self.calls[name] += 1
                return {"audit_log": audit(name)}
            return f

        from graph.platform import initialize as real_init
        monkeypatch.setattr(wf, "initialize", lambda s: real_init(s) | {"step_count": 0})
        for n in ("index_builder", "selection_validator", "query_planner", "hybrid_retriever", "tech_research",
                  "pdf_renderer"):
            monkeypatch.setattr(wf, n, simple(n))
        from graph.state import RetrievalGrade
        monkeypatch.setattr(wf, "retrieval_grader", lambda s: {"retrieval_grade": RetrievalGrade(sufficient=True)})
        monkeypatch.setattr("agents.orchestrator.llm_json", lambda *a, **k: plan)

        def fake_worker(payload):
            t: SubTask = payload["subtask"]
            self.worker_calls.append((t.task_id, t.assigned_agent, t.attempt))
            if t.attempt in self.worker_error.get(t.task_id, ()):
                prev = payload.get("previous")
                st = "partial" if prev and prev.output is not None and t.attempt else "error"
                return {"worker_results": {t.task_id: WorkerResult(
                    task_id=t.task_id, perspective=t.perspective, assigned_agent=t.assigned_agent, status=st,
                    output=prev.output if st == "partial" else None, error="boom", attempt=t.attempt)}}
            wt = REGISTRY[t.assigned_agent].worker_type
            evs = [ev(f"{t.task_id}a{t.attempt}x", "a.com"), ev(f"{t.task_id}a{t.attempt}y", "b.com")]
            out = (ExtraFinding(perspective=t.perspective, by_tech={x: f"요약 [{evs[0].evidence_id}]" for x in t.tech_ids})
                   if wt == "research" else
                   PerspectiveResult(perspective=wt, by_tech={x: TechAssessment(summary=f"{t.task_id} a{t.attempt}")
                                                              for x in t.tech_ids}))
            return {"worker_results": {t.task_id: WorkerResult(
                task_id=t.task_id, perspective=t.perspective, assigned_agent=t.assigned_agent, output=out,
                evidence_ids=[e.evidence_id for e in evs], attempt=t.attempt)}, "evidence": evs}
        monkeypatch.setattr(wf, "worker", fake_worker)

        def synth(state):
            self.calls["synthesizer"] += 1
            self.synth_inputs = {p: {k: v.summary for k, v in state[f"{p}_result"].by_tech.items()}
                                 for p in ("trl", "market", "stakeholder", "domain")}
            return {"synthesis": SynthesisResult()}
        monkeypatch.setattr(wf, "synthesizer", synth)

        def judge(state):
            self.calls["judge"] += 1
            prev = dict(state.get("judge_scores", {}))
            fb = {}
            for p in ("trl", "market", "stakeholder", "domain"):
                if p in prev and prev[p].passed:
                    continue
                if self.fail.get(p, 0) > 0:
                    self.fail[p] -= 1
                    prev[p] = JudgeScore(grounding=3, neutrality=4, source_diversity=4, completeness=4, passed=False)
                    fb[p] = "Independent third-party adoption evidence is insufficient."
                else:
                    prev[p] = JudgeScore(grounding=5, neutrality=5, source_diversity=5, completeness=5, passed=True)
            return {"judge_scores": prev, "judge_feedback": fb, "failed_perspectives": list(fb)}
        monkeypatch.setattr("agents.judge.judge", judge)

        def no_llm(*a, **k):
            self.llm_payloads.append(a[2] if len(a) > 2 else "")
            raise OfflineCacheMiss("stub")
        for mod in ("agents.cross_review", "agents.feedback_coordinator", "graph.orchestration"):
            monkeypatch.setattr(f"{mod}.llm_json", no_llm)

        real_review = cross_reviewer

        def spy_review(payload):
            self.reviewer_calls.append((payload["reviewer_agent"], payload["subtask"].task_id))
            return real_review(payload)
        monkeypatch.setattr(wf, "cross_reviewer", spy_review)

        def writer(state):
            self.calls["report_writer"] += 1
            return {"report_markdown": "# SUMMARY\n", "references": []}
        monkeypatch.setattr(wf, "report_writer", writer)

        def det(state):
            ev_issues = dict(self.quality_evidence)
            self.quality_evidence = {}
            return {"groundedness": True, "neutrality": True, "required_sections": True, "bias_control": True,
                    "coverage": not ev_issues}, [], ev_issues
        monkeypatch.setattr("graph.orchestration.deterministic_checks", det)
        monkeypatch.setenv("ORCH_DECISION_LOG", "0")

    def run(self):
        return wf.build_graph().invoke({}, config={"recursion_limit": wf.RECURSION_LIMIT})


# ------------------------------------------------------------------ planning
def test_dynamic_subtasks_and_extra_perspective(monkeypatch):
    h = Harness(monkeypatch)
    out = h.run()
    assert len(out["subtasks"]) == 6                               # runtime plan size, not 4
    assert len(h.worker_calls) == 6                                # one worker per SubTask
    assert any(t.perspective == "export control regulation" for t in out["subtasks"])
    assert out["plan"].plan_source == "llm" and not out["plan"].repairs


def test_worker_count_follows_plan_not_a_fixed_list(monkeypatch):
    plan = {"rationale": "r", "subtasks": PLAN_6["subtasks"][:1] + PLAN_6["subtasks"][3:5] +
            [raw_task("market_specialist", "market")]}
    h = Harness(monkeypatch, plan=plan)
    h.run()
    assert len(h.worker_calls) == 4
    assert not hasattr(wf, "FAN")


def test_dispatch_uses_state_subtasks():
    tasks = [SubTask(task_id=f"T{i}", perspective="p", assigned_agent="research_generalist", tech_ids=["x"],
                     objective="o") for i in range(3)]
    sends = dispatch_workers({"subtasks": tasks, "selected_techs": []})
    assert [s.arg["subtask"].task_id for s in sends] == ["T0", "T1", "T2"] and all(s.node == "worker" for s in sends)


def test_validate_plan_repairs_missing_minimum_and_unknown_agent():
    tasks, repairs = orch.validate_plan([raw_task("nobody", "competition landscape"),
                                         raw_task("trl_specialist", "trl")], TECHS)
    agents = [t.assigned_agent for t in tasks]
    assert "ecosystem_specialist" in agents                       # unknown agent mapped by capability keyword
    for a in ("market_specialist", "stakeholder_specialist", "domain_specialist"):
        assert a in agents                                         # minimum coverage repaired by code
    assert len(repairs) >= 4
    assert [t.task_id for t in tasks] == [f"T{i:02d}" for i in range(1, len(tasks) + 1)]


def test_validate_plan_drops_duplicate_specialist_work():
    tasks, _ = orch.validate_plan([raw_task("market_specialist", "m1"), raw_task("market_specialist", "m2")], TECHS)
    assert sum(t.assigned_agent == "market_specialist" for t in tasks) == 1


def test_fallback_plan_when_llm_unavailable(monkeypatch, techs):
    def miss(*a, **k):
        raise OfflineCacheMiss("x")
    monkeypatch.setattr("agents.orchestrator.llm_json", miss)
    monkeypatch.setenv("ORCH_DECISION_LOG", "0")
    out = orch.orchestrator({"selected_techs": techs, "evidence": []})
    assert out["plan"].plan_source == "fallback" and len(out["subtasks"]) == 4


# ------------------------------------------------------------------ registry / used vs unused
def test_registry_profiles():
    assert len(REGISTRY) >= 7
    assert {"trl", "market", "stakeholder", "domain", "research"} == {a.worker_type for a in REGISTRY.values()}
    assert all(a.capabilities for a in REGISTRY.values())


def test_used_unused_is_code_computed():
    tasks, _ = orch.validate_plan(PLAN_6["subtasks"], TECHS)
    used, unused = used_unused(tasks)
    assert used == sorted({"trl_specialist", "market_specialist", "stakeholder_specialist", "domain_specialist",
                           "regulation_specialist"})
    assert unused == ["ecosystem_specialist", "research_generalist"]
    assert set(used) | set(unused) == set(REGISTRY) and not set(used) & set(unused)


# ------------------------------------------------------------------ reviewer selection / review mode
def _task(agent="market_specialist"):
    return SubTask(task_id="T03", perspective="market", assigned_agent=agent, tech_ids=["itme"], objective="o")


def test_reviewer_only_from_unused_and_bounded():
    picks = select_reviewers(_task(), ["ecosystem_specialist", "research_generalist", "regulation_specialist"])
    assert 1 <= len(picks) <= MAX_REVIEWERS
    assert set(picks) <= {"ecosystem_specialist", "research_generalist", "regulation_specialist"}


def test_original_agent_never_reviews_own_task():
    assert "market_specialist" not in select_reviewers(_task(), ["market_specialist", "research_generalist"])


def test_reviewer_does_not_execute_task(monkeypatch):
    import agents.workers as workers

    monkeypatch.setattr(workers, "run_task", lambda *a: pytest.fail("reviewer executed the task"))
    monkeypatch.setattr("agents.cross_review.llm_json", lambda *a, **k: {
        "agrees_with_judge": True, "issues": ["developer sources dominate"], "missing_evidence": ["deployment case"],
        "overlooked_evidence_ids": ["W:1", "W:zzz"], "bias_risks": ["one family"],
        "suggested_search_direction": ["hyperscaler deployment"], "review_summary": "s"})
    monkeypatch.setenv("ORCH_DECISION_LOG", "0")
    res = WorkerResult(task_id="T03", perspective="market", assigned_agent="market_specialist", evidence_ids=["W:1"])
    out = cross_reviewer({"reviewer_agent": "ecosystem_specialist", "subtask": _task(), "worker_result": res,
                          "feedback": JudgeFeedbackItem(task_id="T03", passed=False), "evidence": [ev("1", "a.com")]})
    rr = out["review_results"][0]
    assert isinstance(rr, ReviewResult) and set(out) == {"review_results", "audit_log"}   # only a review result
    assert rr.reviewer_agent == "ecosystem_specialist" and rr.target_task_id == "T03"
    assert rr.overlooked_evidence_ids == ["W:1"]                    # ids outside the task's evidence are dropped


def test_review_result_schema():
    rr = ReviewResult(reviewer_agent="a", target_task_id="T01")
    assert set(ReviewResult.model_fields) >= {"reviewer_agent", "target_task_id", "agrees_with_judge", "issues",
                                              "missing_evidence", "overlooked_evidence_ids", "bias_risks",
                                              "suggested_search_direction", "review_summary"}
    assert rr.agrees_with_judge


# ------------------------------------------------------------------ Agent 0
def test_agent0_combines_judge_and_reviews_and_keeps_agent(monkeypatch):
    seen = {}

    def fake(role, sys, user, tag=""):
        seen["user"] = user
        return {"retry_instruction": "독립 제3자 배포 사례를 찾는다", "missing_evidence": ["hyperscaler deployment"],
                "assigned_agent": "ecosystem_specialist"}          # an LLM attempt to reassign must be ignored
    monkeypatch.setattr("agents.feedback_coordinator.llm_json", fake)
    fb = JudgeFeedbackItem(task_id="T03", passed=False, feedback="Independent adoption evidence is insufficient.")
    rr = ReviewResult(reviewer_agent="ecosystem_specialist", target_task_id="T03", review_summary="developer only")
    rt, mode = build_retry_task(_task().model_copy(update={"attempt": 1}), fb, [rr])
    assert "Independent adoption" in seen["user"] and "developer only" in seen["user"]
    assert rt.assigned_agent == "market_specialist" and rt.task_id == "T03" and rt.attempt == 1 and mode == "llm"
    assert rt.reviewer_feedback and rt.judge_feedback


# ------------------------------------------------------------------ reducer
def test_reducer_keeps_latest_attempt_and_parallel_writes():
    a0 = WorkerResult(task_id="T1", perspective="p", assigned_agent="x", attempt=0, output="old")
    a1 = a0.model_copy(update={"attempt": 1, "output": "new"})
    b0 = WorkerResult(task_id="T2", perspective="p", assigned_agent="y", attempt=0)
    m = merge_worker_results({"T1": a0}, {"T2": b0})
    assert set(m) == {"T1", "T2"}                                  # parallel writes preserved
    assert merge_worker_results(m, {"T1": a1})["T1"].output == "new"
    assert merge_worker_results({"T1": a1}, {"T1": a0})["T1"].output == "new"   # stale attempt never wins
    assert list(merge_worker_results({"T2": b0}, {"T1": a0})) == ["T1", "T2"]  # deterministic order


# ------------------------------------------------------------------ full feedback loop
def test_pass_goes_straight_to_report(monkeypatch):
    h = Harness(monkeypatch)
    out = h.run()
    assert h.calls["judge"] == 1 and not h.reviewer_calls and not out.get("retry_tasks")
    assert out["report_quality"].action == "pass"


def test_failed_task_reviewed_by_unused_then_same_agent_retries_once(monkeypatch):
    h = Harness(monkeypatch, fail={"stakeholder": 1})
    out = h.run()
    st_task = next(t for t in out["subtasks"] if t.assigned_agent == "stakeholder_specialist")
    retried = [c for c in h.worker_calls if c[2] == 1]
    assert retried == [(st_task.task_id, "stakeholder_specialist", 1)]          # same agent, attempt 1, only it
    assert Counter(c[0] for c in h.worker_calls)[st_task.task_id] == 2
    assert all(n == 1 for tid, n in Counter(c[0] for c in h.worker_calls).items() if tid != st_task.task_id)
    assert h.reviewer_calls and all(r in out["unused_agents"] and r != "stakeholder_specialist"
                                    for r, _ in h.reviewer_calls)
    assert all(r not in out["used_agents"] for r, _ in h.reviewer_calls)
    rt = out["retry_tasks"][0]
    assert rt.assigned_agent == "stakeholder_specialist" and rt.reviewer_feedback
    assert out["worker_results"][st_task.task_id].attempt == 1
    assert h.synth_inputs["stakeholder"]["itme"].endswith("a1")             # synthesizer uses the newest attempt
    assert out["task_status"][st_task.task_id] == "passed"


def test_split_perspective_retries_only_failing_part(monkeypatch):
    h = Harness(monkeypatch, fail={"market": 1})
    out = h.run()
    market_tasks = [t.task_id for t in out["subtasks"] if t.assigned_agent == "market_specialist"]
    assert sorted(c[0] for c in h.worker_calls if c[2] == 1) == sorted(market_tasks)   # global LLM fail -> both
    assert all(c[1] == "market_specialist" for c in h.worker_calls if c[2] == 1)


def test_retry_exactly_once_then_failed_after_retry(monkeypatch):
    h = Harness(monkeypatch, fail={"domain": 99})
    out = h.run()
    d = next(t.task_id for t in out["subtasks"] if t.assigned_agent == "domain_specialist")
    assert Counter(c[0] for c in h.worker_calls)[d] == 1 + MAX_FEEDBACK_RETRY
    assert out["task_status"][d] == "FAILED_AFTER_RETRY"
    assert any("재시도 1회 후에도" in w for w in out["warnings"])
    assert h.calls["report_writer"] == 1 and h.calls["pdf_renderer"] == 1     # terminates and reports


def test_validate_plan_keeps_one_reviewer_in_reserve():
    raw = PLAN_6["subtasks"] + [raw_task("ecosystem_specialist", "competition", prio=2),
                                raw_task("research_generalist", "cost", prio=3)]
    tasks, repairs = orch.validate_plan(raw, TECHS)
    used, unused = used_unused(tasks)
    assert unused == ["research_generalist"] and any("검토자 풀" in r for r in repairs)


def test_no_unused_agent_skips_review(monkeypatch):
    plan = {"rationale": "all agents", "subtasks": PLAN_6["subtasks"] + [
        raw_task("ecosystem_specialist", "competition", prio=2), raw_task("research_generalist", "cost", prio=2)]}
    monkeypatch.setattr(orch, "REVIEWER_RESERVE", 0)   # fallback path: the plan really uses every agent
    h = Harness(monkeypatch, plan=plan, fail={"trl": 1})
    out = h.run()
    assert out["unused_agents"] == [] and not h.reviewer_calls and not out.get("review_results")
    assert out["retry_tasks"] and out["retry_tasks"][0].reviewer_feedback == []
    assert [c for c in h.worker_calls if c[2] == 1][0][1] == "trl_specialist"


def test_worker_error_fallback_keeps_previous_output(monkeypatch):
    h = Harness(monkeypatch, worker_error={"T01": (0,)})
    out = h.run()
    assert [c for c in h.worker_calls if c[0] == "T01"] == [("T01", "trl_specialist", 0), ("T01", "trl_specialist", 1)]
    assert out["task_status"]["T01"] == "passed"
    h2 = Harness(monkeypatch, fail={"trl": 99}, worker_error={"T01": (1,)})
    out2 = h2.run()
    assert out2["task_status"]["T01"] == "PARTIAL" and out2["worker_results"]["T01"].output is not None


def test_judge_feedback_items_carry_task_id(monkeypatch):
    h = Harness(monkeypatch, fail={"trl": 1})
    out = h.run()
    assert out["retry_tasks"][0].task_id == "T01"
    assert "T01" in out["retry_tasks"][0].judge_feedback or out["retry_tasks"][0].judge_feedback


def test_report_quality_evidence_issue_routes_to_retry(monkeypatch):
    h = Harness(monkeypatch, quality_evidence={"T04": "이해관계자 관점 itme 평가 결과 없음"})
    out = h.run()
    assert ("T04", out["subtasks"][3].assigned_agent, 1) in h.worker_calls
    assert h.calls["report_writer"] == 2 and out["judge_result"].source in ("perspective_judge", "report_quality")


def test_graph_terminates_within_limits(monkeypatch):
    h = Harness(monkeypatch, fail={"trl": 99, "market": 99, "stakeholder": 99, "domain": 99})
    out = h.run()
    assert h.calls["judge"] == 2 and out["step_count"] <= out["max_steps"]
    assert all(c[2] <= MAX_FEEDBACK_RETRY for c in h.worker_calls)


def test_parallel_worker_errors_do_not_conflict(monkeypatch):
    """Real `worker` node: several workers failing in the same superstep write last_error through a reducer."""
    from agents import workers

    h = Harness(monkeypatch, fail={"trl": 99, "market": 99})
    monkeypatch.setattr(wf, "worker", workers.worker)

    def boom(payload):
        if payload["subtask"].attempt == 1:
            raise RuntimeError(f"down {payload['subtask'].task_id}")
        t = payload["subtask"]
        research = REGISTRY[t.assigned_agent].worker_type == "research"
        out = (ExtraFinding(perspective=t.perspective, by_tech={x: "s" for x in t.tech_ids}) if research else
               PerspectiveResult(perspective="x", by_tech={x: TechAssessment(summary="s") for x in t.tech_ids}))
        return {"output": out,
                "evidence": [], "audit_log": [], "warnings": []}
    monkeypatch.setattr(workers, "run_task", boom)
    out = h.run()
    assert "down T01" in out["last_error"] and "down T02" in out["last_error"]
    assert out["task_status"]["T01"] == "PARTIAL"
