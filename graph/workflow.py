"""LangGraph workflow (DESIGN.md D.3, figure 2): 18 nodes, 4 bounded loops, fan-out/fan-in with a deferred synthesizer,
retry_router re-running only failed perspectives via Command(goto=[...])."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.domain import domain_evaluator
from agents.judge import judge
from agents.market import market_evaluator
from agents.report_writer import report_writer
from agents.stakeholder import stakeholder_evaluator
from agents.synthesis import synthesizer
from agents.tech_research import selection_validator, tech_research, trl_assessor
from graph.platform import (final_check, hybrid_retriever, index_builder, initialize, pdf_renderer, query_planner,
                            query_rewriter, retrieval_grader, retry_router, route_after_check, route_after_grade)
from graph.state import State

RECURSION_LIMIT = 50
FAN = ["trl_assessor", "market_evaluator", "stakeholder_evaluator", "domain_evaluator"]


def build_graph(checkpointer=None):
    g = StateGraph(State)
    # 1. selection check and RAG preparation
    g.add_node("initialize", initialize)
    g.add_node("index_builder", index_builder)
    g.add_node("selection_validator", selection_validator)
    g.add_node("query_planner", query_planner)
    g.add_node("hybrid_retriever", hybrid_retriever)
    g.add_node("retrieval_grader", retrieval_grader)
    g.add_node("query_rewriter", query_rewriter)
    # 2. tech research and parallel perspectives
    g.add_node("tech_research", tech_research)
    g.add_node("trl_assessor", trl_assessor)
    g.add_node("market_evaluator", market_evaluator)
    g.add_node("stakeholder_evaluator", stakeholder_evaluator)
    g.add_node("domain_evaluator", domain_evaluator)
    g.add_node("synthesizer", synthesizer, defer=True)
    # 3. judge and selective re-run
    g.add_node("judge", judge)
    g.add_node("retry_router", retry_router)
    # 4. report
    g.add_node("report_writer", report_writer)
    g.add_node("final_check", final_check)
    g.add_node("pdf_renderer", pdf_renderer)

    g.add_edge(START, "initialize")
    g.add_edge("initialize", "index_builder")
    g.add_edge("index_builder", "selection_validator")
    g.add_edge("selection_validator", "query_planner")
    g.add_edge("query_planner", "hybrid_retriever")
    g.add_edge("hybrid_retriever", "retrieval_grader")
    g.add_conditional_edges("retrieval_grader", route_after_grade, ["query_rewriter", "tech_research"])
    g.add_edge("query_rewriter", "hybrid_retriever")
    for n in FAN:
        g.add_edge("tech_research", n)
        g.add_edge(n, "synthesizer")
    g.add_edge("synthesizer", "judge")
    g.add_edge("judge", "retry_router")
    # retry_router -> Command(goto=[failed perspective nodes]) | "report_writer" (declared by its return annotation)
    g.add_edge("report_writer", "final_check")
    g.add_conditional_edges("final_check", route_after_check, ["report_writer", "pdf_renderer"])
    g.add_edge("pdf_renderer", END)
    return g.compile(checkpointer=checkpointer)
