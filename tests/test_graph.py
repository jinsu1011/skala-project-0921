"""Graph topology and loop behaviour with stub nodes (no model, no API)."""
from collections import Counter

import pytest

import graph.workflow as wf
from graph.platform import MAX_RETRIEVAL, query_rewriter, route_after_grade
from graph.runtime import audit
from graph.state import (JudgeScore, PerspectiveResult, Query, RetrievalGrade, SynthesisResult)

NODES = ["initialize", "index_builder", "selection_validator", "query_planner", "hybrid_retriever", "retrieval_grader",
         "query_rewriter", "tech_research", "trl_assessor", "market_evaluator", "stakeholder_evaluator",
         "domain_evaluator", "synthesizer", "judge", "retry_router", "report_writer", "final_check", "pdf_renderer"]


def test_graph_has_the_18_design_nodes_and_limit():
    g = wf.build_graph().get_graph()
    assert sorted(n for n in g.nodes if not n.startswith("__")) == sorted(NODES)
    assert wf.RECURSION_LIMIT == 50


def test_query_rewrite_loop_success_and_exhaustion(techs):
    st = {"retrieval_grade": RetrievalGrade(sufficient=True), "retrieval_retry_count": 0}
    assert route_after_grade(st) == "tech_research"
    st = {"retrieval_grade": RetrievalGrade(sufficient=False), "retrieval_retry_count": 1}
    assert route_after_grade(st) == "query_rewriter"
    st["retrieval_retry_count"] = MAX_RETRIEVAL
    assert route_after_grade(st) == "tech_research"        # limit reached -> move on (warning set by grader)


def test_query_rewriter_increments(monkeypatch, techs):
    monkeypatch.setattr("tools.paper_retrieve.to_english", lambda q: "en")
    st = {"retrieval_retry_count": 0, "selected_techs": techs, "rewritten_queries": {},
          "queries": {"turboquant": [Query(tech="turboquant", text_ko="q")]},
          "retrieval_grade": RetrievalGrade(sufficient=False, missing={"turboquant": ["principle"]})}
    out = query_rewriter(st)
    assert out["retrieval_retry_count"] == 1
    assert len(out["rewritten_queries"]["turboquant"]) == 2


class Stub:
    """Replaces every node function; records calls; the judge fails the given perspectives `fail_times` times."""

    def __init__(self, monkeypatch, fail: dict[str, int], grader_insufficient: int = 0):
        self.calls = Counter()
        self.fail = dict(fail)
        self.grader_left = grader_insufficient

        def simple(name, extra=None):
            def f(state):
                self.calls[name] += 1
                return {"audit_log": audit(name), **(extra(state) if extra else {})}
            return f

        from graph.platform import initialize as real_init
        monkeypatch.setattr(wf, "initialize", lambda s: (self.calls.update(["initialize"]), real_init(s))[1])
        for n in ("index_builder", "selection_validator", "query_planner", "hybrid_retriever", "tech_research",
                  "pdf_renderer"):
            monkeypatch.setattr(wf, n, simple(n))

        def grader(state):
            self.calls["retrieval_grader"] += 1
            ok = self.grader_left <= 0
            self.grader_left -= 1
            return {"retrieval_grade": RetrievalGrade(sufficient=ok, missing={} if ok else {"turboquant": ["name"]})}
        monkeypatch.setattr(wf, "retrieval_grader", grader)

        def rewriter(state):
            self.calls["query_rewriter"] += 1
            return {"retrieval_retry_count": state.get("retrieval_retry_count", 0) + 1}
        monkeypatch.setattr(wf, "query_rewriter", rewriter)

        for p, node in (("trl", "trl_assessor"), ("market", "market_evaluator"), ("stakeholder", "stakeholder_evaluator"),
                        ("domain", "domain_evaluator")):
            monkeypatch.setattr(wf, node, simple(node, lambda s, p=p: {f"{p}_result": PerspectiveResult(perspective=p)}))

        def synth(state):
            self.calls["synthesizer"] += 1
            return {"synthesis": SynthesisResult()}
        monkeypatch.setattr(wf, "synthesizer", synth)

        def judge(state):
            self.calls["judge"] += 1
            prev = dict(state.get("judge_scores", {}))
            failed = []
            for p in ("trl", "market", "stakeholder", "domain"):
                if p in prev and prev[p].passed:
                    continue                                   # frozen
                if self.fail.get(p, 0) > 0:
                    self.fail[p] -= 1
                    failed.append(p)
                    prev[p] = JudgeScore(passed=False)
                else:
                    prev[p] = JudgeScore(passed=True)
            return {"judge_scores": prev, "failed_perspectives": failed}
        monkeypatch.setattr(wf, "judge", judge)

        def writer(state):
            self.calls["report_writer"] += 1
            return {"report_markdown": "# SUMMARY\n", "references": []}
        monkeypatch.setattr(wf, "report_writer", writer)

        def check(state):
            self.calls["final_check"] += 1
            return {"audit_log": audit("final_check", passed=True)}
        monkeypatch.setattr(wf, "final_check", check)

    def run(self):
        return wf.build_graph().invoke({}, config={"recursion_limit": wf.RECURSION_LIMIT})


def test_fan_out_fan_in_once(monkeypatch):
    s = Stub(monkeypatch, fail={})
    s.run()
    for n in ("trl_assessor", "market_evaluator", "stakeholder_evaluator", "domain_evaluator"):
        assert s.calls[n] == 1
    assert s.calls["synthesizer"] == 1          # deferred: runs once after all four
    assert s.calls["judge"] == 1 and s.calls["report_writer"] == 1


def test_only_failed_perspective_is_rerun(monkeypatch):
    s = Stub(monkeypatch, fail={"market": 1})
    out = s.run()
    assert s.calls["market_evaluator"] == 2
    assert s.calls["trl_assessor"] == s.calls["stakeholder_evaluator"] == s.calls["domain_evaluator"] == 1
    assert s.calls["synthesizer"] == 2 and s.calls["judge"] == 2
    assert out["perspective_retry_count"]["market"] == 1
    assert not any(w.startswith("판정 불확실") for w in out.get("warnings", []))


def test_rerun_limit_exhausted_records_uncertain(monkeypatch):
    s = Stub(monkeypatch, fail={"domain": 99})
    out = s.run()
    assert s.calls["domain_evaluator"] == 3            # first run + 2 re-runs
    assert out["perspective_retry_count"]["domain"] == 2
    assert any(w.startswith("판정 불확실") and "도메인" in w for w in out["warnings"])
    assert s.calls["report_writer"] == 1


def test_common_retrieval_loop_limit(monkeypatch):
    s = Stub(monkeypatch, fail={}, grader_insufficient=99)
    out = s.run()
    assert s.calls["query_rewriter"] == 2 and s.calls["hybrid_retriever"] == 3
    assert out["retrieval_retry_count"] == 2
