"""Auxiliary (non-agent) nodes: initialize, index_builder, query_planner, hybrid_retriever, retrieval_grader,
query_rewriter, retry_router, final_check, pdf_renderer, plus the conditional-edge routers (D.1, D.4)."""
from __future__ import annotations

import re
import shutil
import uuid
from typing import Literal

from langgraph.types import Command

from graph.runtime import ROOT, audit, config, rt
from graph.state import (PERSPECTIVE_NODE, PERSPECTIVES, DocumentMeta, IndexStatus, Query, RetrievalGrade,
                         Technology)

MAX_RETRIEVAL = config()["retrieval"]["max_retrieval_retries"]      # 2
MAX_PERSPECTIVE = config()["graph"]["max_perspective_retries"]      # 2
MAX_REPORT = config()["graph"]["max_report_retries"]                # 1
PERSPECTIVE_KO = {"trl": "TRL", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인 적합성"}

SELECTION_REASON = {  # team's selection (A.3/A.4), recorded as-is; the validator never changes it
    "turboquant": "후보 6개 가중합 SW 최고점(4.35). 재학습 없이 서빙 단계에서 KV를 양자화한다",
    "itme": "후보 6개 가중합 HW 최고점(4.50). vLLM 위의 서빙 계층 기술로 모델을 바꾸지 않고 KV 공간을 CXL-hybrid 메모리로 넓힌다",
}


# ---------------------------------------------------------------- 1. preparation
def initialize(state: dict) -> dict:
    meta = config()["tech_meta"]
    techs = []
    for camp_key, tid in rt().techs.items():
        m = meta[tid]
        techs.append(Technology(tech_id=tid, name=m["name"], camp=m["camp"], developer=m["developer"],
                                developer_groups=m.get("developer_groups", []), reason=SELECTION_REASON.get(tid, ""),
                                paper_arxiv=m.get("arxiv", "")))
    return {"run_id": uuid.uuid4().hex[:12], "selected_techs": techs, "retrieval_retry_count": 0,
            "perspective_retry_count": {p: 0 for p in PERSPECTIVES}, "report_retry_count": 0, "judge_scores": {},
            "judge_feedback": {}, "failed_perspectives": [], "rewritten_queries": {},
            "audit_log": audit("initialize", techs=[t.tech_id for t in techs], offline=rt().offline)}


def index_builder(state: dict) -> dict:
    import pymupdf as fitz

    from rag.loader import PAPER_DIR

    cfg = config()
    missing = [p["arxiv"] for p in cfg["corpus"]["papers"] if not (PAPER_DIR / f"{p['arxiv']}.pdf").exists()]
    if missing:
        try:
            import runpy
            runpy.run_path(str(ROOT / "scripts" / "download_papers.py"), run_name="__main__")
        except Exception as ex:  # noqa: BLE001
            raise RuntimeError(f"논문 PDF가 없습니다({', '.join(missing)}). 해결: `uv run python scripts/download_papers.py` "
                               f"를 실행해 data/papers/ 에 받아 주세요. 원인: {ex}") from ex
    r = rt().retriever()
    counts: dict[str, int] = {}
    for c in r.chunks:
        counts[c.doc_id] = counts.get(c.doc_id, 0) + 1
    manifest = []
    for p in cfg["corpus"]["papers"]:
        with fitz.open(PAPER_DIR / f"{p['arxiv']}.pdf") as doc:
            pages = doc.page_count
        manifest.append(DocumentMeta(doc_id=p["arxiv"], tech=p["tech"], camp=p["camp"], role=p["role"],
                                     title=p["title"], pages=pages, chunks=counts.get(p["arxiv"], 0)))
    total = sum(m.pages for m in manifest)
    status = IndexStatus(n_chunks=len(r.chunks), total_pages=total, page_cap=cfg["corpus"]["page_cap"],
                         embedding_model=cfg["retrieval"]["embedding_model"], max_seq_length=r.max_len,
                         reranker=cfg["retrieval"]["reranker"] if rt().rerank else None, reused=r.reused)
    warns = [f"문서 쪽수 {total}쪽이 한도 {status.page_cap}쪽을 넘음"] if total > status.page_cap else []
    return {"document_manifest": manifest, "index_status": status, "warnings": warns,
            "audit_log": audit("index_builder", chunks=len(r.chunks), pages=total, reused=r.reused)}


QUERY_TEMPLATES = [("principle", "{name}의 핵심 작동 원리와 방법"),
                   ("conditions", "{name}의 실험 환경(모델, 문맥 길이, 하드웨어, 기준선)과 보고된 성능 수치"),
                   ("limits", "{name}의 한계, 오버헤드, 적용 제약")]


def query_planner(state: dict) -> dict:
    from tools.paper_retrieve import to_english

    qs = {}
    for t in state["selected_techs"]:
        qs[t.tech_id] = [Query(tech=t.tech_id, aspect=a, text_ko=q.format(name=t.name),
                               text_en=to_english(q.format(name=t.name))) for a, q in QUERY_TEMPLATES]
    return {"queries": qs, "audit_log": audit("query_planner", n={k: len(v) for k, v in qs.items()})}


def hybrid_retriever(state: dict) -> dict:
    from tools.paper_retrieve import search_papers

    use = state.get("rewritten_queries") or state["queries"]
    out = {}
    for tech, qs in use.items():
        seen = {}
        for q in qs:
            for e in search_papers(q.text_ko, tech=tech, query_en=q.text_en):
                seen.setdefault(e.evidence_id, e)
        out[tech] = [seen[k] for k in sorted(seen)]
    return {"retrieved_chunks": out, "audit_log": audit("hybrid_retriever", attempt=state.get("retrieval_retry_count", 0),
                                                        hits={k: [e.evidence_id for e in v] for k, v in out.items()})}


_NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:%|x|×|bit|bits|GB|GiB|TB|ms|K\b|times)", re.I)
_LIMIT = re.compile(r"limitation|overhead|drawback|degrad|cost|latency|however|constraint", re.I)
_PRINCIPLE = re.compile(r"we propose|our (?:method|approach|design)|algorithm|architecture|we present|framework", re.I)


def retrieval_grader(state: dict) -> dict:
    grade = RetrievalGrade(sufficient=True)
    aliases = {k: v.get("aliases", [v["name"]]) for k, v in config()["tech_meta"].items()}
    for tech, evs in state["retrieved_chunks"].items():
        text = " ".join(e.summary for e in evs)
        g = {"name": any(a.lower() in text.lower() for a in aliases.get(tech, [tech])),
             "principle": bool(_PRINCIPLE.search(text)),
             "number_or_limit": bool(_NUM.search(text) or _LIMIT.search(text))}
        grade.by_tech[tech] = g
        miss = [k for k, v in g.items() if not v]
        if miss:
            grade.missing[tech] = miss
            grade.sufficient = False
    warns = []
    if not grade.sufficient and state.get("retrieval_retry_count", 0) >= MAX_RETRIEVAL:
        warns.append("공통 검색: 재시도 한도(2회)에 이르렀으나 빠진 요소가 남음 " + str(grade.missing))
    return {"retrieval_grade": grade, "warnings": warns,
            "audit_log": audit("retrieval_grader", sufficient=grade.sufficient, missing=grade.missing)}


def route_after_grade(state: dict) -> Literal["query_rewriter", "tech_research"]:
    if state["retrieval_grade"].sufficient or state.get("retrieval_retry_count", 0) >= MAX_RETRIEVAL:
        return "tech_research"
    return "query_rewriter"


MISSING_HINT = {"name": "{name}", "principle": "{name}의 방법과 알고리즘 설계",
                "number_or_limit": "{name}의 실험 수치 결과와 한계"}


def query_rewriter(state: dict) -> dict:
    from tools.paper_retrieve import to_english

    n = state.get("retrieval_retry_count", 0) + 1
    base = state.get("rewritten_queries") or state["queries"]
    names = {t.tech_id: t.name for t in state["selected_techs"]}
    out = {}
    for tech, qs in base.items():
        extra = [MISSING_HINT[m].format(name=names[tech]) for m in state["retrieval_grade"].missing.get(tech, [])]
        new = list(qs)
        for x in extra:
            q = f"{x} (재검색 {n})"
            new.append(Query(tech=tech, aspect="rewrite", text_ko=q, text_en=to_english(q)))
        out[tech] = new
    return {"rewritten_queries": out, "retrieval_retry_count": n, "audit_log": audit("query_rewriter", attempt=n)}


# ---------------------------------------------------------------- 3. selective re-run
PerspectiveNode = Literal["trl_assessor", "market_evaluator", "stakeholder_evaluator", "domain_evaluator", "report_writer"]


def retry_router(state: dict) -> Command[PerspectiveNode]:
    """Increments the re-run counter of failed perspectives (< 2 re-runs) and re-runs only those nodes with
    Command(goto=[...]). Perspectives at the limit are recorded as 판정 불확실. Nothing to re-run -> report_writer."""
    counts = dict(state.get("perspective_retry_count", {}))
    goto, warns = [], []
    for p in state.get("failed_perspectives", []):
        if counts.get(p, 0) < MAX_PERSPECTIVE:
            counts[p] = counts.get(p, 0) + 1
            goto.append(PERSPECTIVE_NODE[p])
        else:
            warns.append(f"판정 불확실: {PERSPECTIVE_KO[p]} 관점이 재실행 한도({MAX_PERSPECTIVE}회) 후에도 Judge 기준 미달")
    if not goto:
        return Command(update={"warnings": warns, "audit_log": audit("retry_router", goto=["report_writer"])},
                       goto="report_writer")
    return Command(update={"perspective_retry_count": counts, "warnings": warns,
                           "audit_log": audit("retry_router", goto=goto, counts=counts)}, goto=goto)


# ---------------------------------------------------------------- 4. report check and PDF
def final_check(state: dict) -> dict:
    from agents.report_writer import check_report, postprocess

    issues = check_report(state["report_markdown"], state["references"])
    n = state.get("report_retry_count", 0)
    if not issues:
        return {"audit_log": audit("final_check", passed=True, attempt=n)}
    if n < MAX_REPORT:
        return {"report_retry_count": n + 1, "audit_log": audit("final_check", passed=False, attempt=n, issues=issues)}
    md, refs, left = postprocess(state["report_markdown"], state["references"])
    return {"report_markdown": md, "references": refs,
            "warnings": [f"보고서 검수: 수정 한도 소진 후 결정적 후처리, 남은 문제 {x}" for x in left],
            "audit_log": audit("final_check", passed=False, attempt=n, issues=issues, postprocessed=True, left=left)}


def route_after_check(state: dict) -> Literal["report_writer", "pdf_renderer"]:
    last = next((e for e in reversed(state.get("audit_log", [])) if e.node == "final_check"), None)
    if last and not last.detail.get("passed") and not last.detail.get("postprocessed"):
        return "report_writer"
    return "pdf_renderer"


def output_stem() -> str:
    team = config()["team"]
    return f"RAG-Output_{team['campus']}_{team['class']}_" + "+".join(m["name"] for m in team["members"])


def pdf_renderer(state: dict) -> dict:
    from report.docx_builder import CoverInfo, ReportBuilder, docx_to_pdf

    team = config()["team"]
    out = ROOT / "outputs"
    stem = output_stem()
    md_path = out / f"{stem}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(state["report_markdown"])
    cover = CoverInfo(title="KV cache 최적화 기술 다관점 평가 보고서",
                      members=[(m["id"], m["name"]) for m in team["members"]], date=team["submit_date"],
                      report_kind="과제 제출 보고서 · 평가 보고서", doc_info=False)
    rb = ReportBuilder(cover)
    rb.markdown(state["report_markdown"], ROOT / "outputs")
    docx = rb.save(out / f"{stem}.docx")
    pdf = docx_to_pdf(docx, out / f"{stem}.pdf")
    dst = ROOT / "deliverables" / pdf.name
    shutil.copy(pdf, dst)
    return {"report_pdf_path": str(pdf), "audit_log": audit("pdf_renderer", pdf=str(pdf.relative_to(ROOT)),
                                                            copy=str(dst.relative_to(ROOT)))}
