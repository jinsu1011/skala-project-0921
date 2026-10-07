"""Graph topology and loop behaviour with stub nodes (no model, no API)."""
from collections import Counter

import graph.workflow as wf
from graph.platform import MAX_RETRIEVAL, query_rewriter, route_after_grade
from graph.state import Query, RetrievalGrade

NODES = ["initialize", "index_builder", "selection_validator", "query_planner", "hybrid_retriever", "retrieval_grader",
         "query_rewriter", "tech_research", "orchestrator", "worker", "result_aggregator", "synthesizer",
         "perspective_judge", "retry_router", "cross_reviewer", "feedback_retry_coordinator", "report_writer",
         "report_quality_evaluator", "pdf_renderer"]


def test_graph_nodes_and_limit():
    g = wf.build_graph().get_graph()
    assert sorted(n for n in g.nodes if not n.startswith("__")) == sorted(NODES)
    assert wf.RECURSION_LIMIT == 80
    # no static per-perspective worker nodes: fan-out goes through a single dynamic `worker` node
    assert not {"trl_assessor", "market_evaluator", "stakeholder_evaluator", "domain_evaluator"} & set(g.nodes)


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


def test_common_retrieval_loop_limit(monkeypatch):
    from tests.test_orchestration import Harness

    h = Harness(monkeypatch)
    calls = Counter()

    def grader(state):
        calls["retrieval_grader"] += 1
        return {"retrieval_grade": RetrievalGrade(sufficient=False, missing={"turboquant": ["name"]})}

    def rewriter(state):
        calls["query_rewriter"] += 1
        return {"retrieval_retry_count": state.get("retrieval_retry_count", 0) + 1}

    def retriever(state):
        calls["hybrid_retriever"] += 1
        return {}
    monkeypatch.setattr(wf, "retrieval_grader", grader)
    monkeypatch.setattr(wf, "query_rewriter", rewriter)
    monkeypatch.setattr(wf, "hybrid_retriever", retriever)
    out = h.run()
    assert calls["query_rewriter"] == 2 and calls["hybrid_retriever"] == 3
    assert out["retrieval_retry_count"] == 2
