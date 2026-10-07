"""LangGraph workflow: Orchestrator-Workers with a bounded quality-feedback loop.

RAG preparation (unchanged) -> orchestrator (dynamic SubTasks) -> Send fan-out to `worker` (one per SubTask)
-> result_aggregator (deferred join; reducer keeps the newest attempt per task_id) -> synthesizer -> perspective_judge
  PASS -> report_writer -> report_quality_evaluator -> pdf_renderer -> END
  FAIL -> retry_router -> cross_reviewer (unused agents only, bounded Send) -> feedback_retry_coordinator (Agent 0)
       -> worker (same original agent, attempt 1, failed tasks only) -> result_aggregator -> synthesizer -> judge
Loops: retrieval rewrite <= 2, feedback retry per task = 1, report rewrite <= 1, max_steps, recursion_limit.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.cross_review import cross_reviewer
from agents.feedback_coordinator import feedback_retry_coordinator
from agents.orchestrator import orchestrator
from agents.report_writer import report_writer
from agents.synthesis import synthesizer
from agents.tech_research import selection_validator, tech_research
from agents.workers import worker
from graph.orchestration import (dispatch_retry, dispatch_reviews, dispatch_workers, perspective_judge,
                                 report_quality_evaluator, result_aggregator, retry_router, route_after_judge,
                                 route_after_quality)
from graph.platform import (hybrid_retriever, index_builder, initialize, pdf_renderer, query_planner, query_rewriter,
                            retrieval_grader, route_after_grade)
from graph.state import State

RECURSION_LIMIT = 80


def build_graph(checkpointer=None):
    g = StateGraph(State)
    # 1. selection check and RAG preparation (existing)
    g.add_node("initialize", initialize)
    g.add_node("index_builder", index_builder)
    g.add_node("selection_validator", selection_validator)
    g.add_node("query_planner", query_planner)
    g.add_node("hybrid_retriever", hybrid_retriever)
    g.add_node("retrieval_grader", retrieval_grader)
    g.add_node("query_rewriter", query_rewriter)
    g.add_node("tech_research", tech_research)
    # 2. orchestrator-workers
    g.add_node("orchestrator", orchestrator)
    g.add_node("worker", worker)
    g.add_node("result_aggregator", result_aggregator, defer=True)
    g.add_node("synthesizer", synthesizer)
    g.add_node("perspective_judge", perspective_judge)
    # 3. feedback path (only when the Judge or the quality evaluator fails a task)
    g.add_node("retry_router", retry_router)
    g.add_node("cross_reviewer", cross_reviewer)
    g.add_node("feedback_retry_coordinator", feedback_retry_coordinator)
    # 4. report
    g.add_node("report_writer", report_writer)
    g.add_node("report_quality_evaluator", report_quality_evaluator)
    g.add_node("pdf_renderer", pdf_renderer)

    g.add_edge(START, "initialize")
    g.add_edge("initialize", "index_builder")
    g.add_edge("index_builder", "selection_validator")
    g.add_edge("selection_validator", "query_planner")
    g.add_edge("query_planner", "hybrid_retriever")
    g.add_edge("hybrid_retriever", "retrieval_grader")
    g.add_conditional_edges("retrieval_grader", route_after_grade, ["query_rewriter", "tech_research"])
    g.add_edge("query_rewriter", "hybrid_retriever")
    g.add_edge("tech_research", "orchestrator")
    g.add_conditional_edges("orchestrator", dispatch_workers, ["worker"])
    g.add_edge("worker", "result_aggregator")
    g.add_edge("result_aggregator", "synthesizer")
    g.add_edge("synthesizer", "perspective_judge")
    g.add_conditional_edges("perspective_judge", route_after_judge, ["retry_router", "report_writer"])
    g.add_conditional_edges("retry_router", dispatch_reviews, ["cross_reviewer", "feedback_retry_coordinator"])
    g.add_edge("cross_reviewer", "feedback_retry_coordinator")
    g.add_conditional_edges("feedback_retry_coordinator", dispatch_retry, ["worker", "result_aggregator"])
    g.add_edge("report_writer", "report_quality_evaluator")
    g.add_conditional_edges("report_quality_evaluator", route_after_quality,
                            ["report_writer", "retry_router", "pdf_renderer"])
    g.add_edge("pdf_renderer", END)
    return g.compile(checkpointer=checkpointer)
