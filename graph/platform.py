"""Auxiliary (non-agent) nodes: initialize, index_builder, query_planner, hybrid_retriever, retrieval_grader,
query_rewriter, pdf_renderer and the retrieval router (D.1). Feedback routing and report quality evaluation live in
graph/orchestration.py."""
from __future__ import annotations

import re
import shutil
import uuid
from typing import Literal

from graph.runtime import ROOT, audit, config, rt
from graph.state import (PERSPECTIVES, DocumentMeta, IndexStatus, Query, RetrievalGrade,
                         Technology)

MAX_RETRIEVAL = config()["retrieval"]["max_retrieval_retries"]      # 2
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
    run_id = uuid.uuid4().hex[:12]
    return {"run_id": run_id, "trace_id": f"kvcache-{run_id}", "step_count": 0, "max_steps": 40, "status": "planning",
            "last_error": None, "selected_techs": techs, "retrieval_retry_count": 0,
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
            # call main() directly: running the script as __main__ would sys.exit() the whole app
            rc = runpy.run_path(str(ROOT / "scripts" / "download_papers.py"))["main"]()
            if rc:
                raise RuntimeError(f"페이지 수가 한도를 넘었습니다(download_papers.py 반환값 {rc})")
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


# 3./4. feedback routing and report quality: see graph/orchestration.py (Orchestrator-Workers)


def output_stem() -> str:
    team = config()["team"]
    return f"Agent-Output_{team['campus']}_{team['class']}_" + "+".join(m["name"] for m in team["members"])


def _heading_pages(pdf, headings: list[str]) -> dict[str, int]:
    import pymupdf

    found, start = {}, 0
    with pymupdf.open(pdf) as doc:
        # extracted text drops/splits spaces, so compare with all whitespace removed
        texts = [re.sub(r"\s+", "", p.get_text()) for p in doc]
    toc_page = next((i for i, t in enumerate(texts) if "목차" in t), 0)
    # the compact 목차 shares its page with SUMMARY: search that page only after the TOC's last row (REFERENCE)
    head, sep, tail = texts[toc_page].partition("REFERENCE")
    texts[toc_page] = tail if sep else ""
    start = toc_page
    for h in headings:
        key = re.sub(r"\s+", "", h)
        for i in range(start, len(texts)):
            if key in texts[i]:
                found[h] = i + 1
                start = i
                break
    return found


def _blank_pages(pdf) -> list[int]:
    """Pages whose only text is the running header and page number."""
    import pymupdf

    out = []
    with pymupdf.open(pdf) as doc:
        for i, p in enumerate(doc):
            body = re.sub(r"\s+", "", p.get_text())
            body = re.sub(r"SKALA4기과제제출보고서·평가보고서|-\d+-", "", body)
            if len(body) < 20:
                out.append(i + 1)
    return out


PAGE_LIMIT = 10   # Notion: 보고서 최대 10장


def _page_count(pdf) -> int:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        return doc.page_count


def pdf_renderer(state: dict) -> dict:
    """Render the SKALA-template PDF within PAGE_LIMIT pages. If the full report is longer, optional sub-sections are
    dropped in a fixed order (agents.report_writer.COMPACT_LEVELS) and citations / REFERENCE are renumbered, so the
    four perspective sections, SUMMARY and REFERENCE are always kept."""
    from agents.report_writer import (COMPACT_LEVELS, check_report, compact_report, quality_note, run_overview,
                                      toc_entries, with_toc)
    from report.docx_builder import CoverInfo, ReportBuilder, docx_to_pdf

    team = config()["team"]
    out = ROOT / "outputs"
    stem = output_stem()
    md_path = out / f"{stem}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    cover = CoverInfo(title="KV cache 최적화 기술 다관점 평가 보고서",
                      members=[(m["id"], m["name"]) for m in team["members"]], date=team["submit_date"],
                      report_kind="과제 제출 보고서 · 평가 보고서", doc_info=False)

    def build(md: str):
        rb = ReportBuilder(cover)
        rb.markdown(md, ROOT / "outputs")
        docx = rb.save(out / f"{stem}.docx")
        return docx_to_pdf(docx, out / f"{stem}.pdf")

    full = state["report_markdown"]
    extra = run_overview(state) + ([quality_note(state.get("report_quality"))] if state.get("report_quality") else [])
    if extra and "# REFERENCE" in full:   # quality evaluator runs after report_writer: add run facts to chapter 6
        n = len(re.findall(r"^## 6\.\d+ ", full, flags=re.M)) + 1
        block = "\n".join(extra)
        full = full.replace("\n# REFERENCE", f"\n## 6.{n} 실행 개요와 보고서 품질 평가\n{block}\n\n# REFERENCE", 1)
    for level in range(len(COMPACT_LEVELS)):
        body, refs, dropped = compact_report(full, state.get("references", []), level)
        # pass 1 renders with an empty page column, pass 2 fills the pages found in the PDF (same layout)
        pdf = build(with_toc(body))
        pages = _heading_pages(pdf, [t for _, t in toc_entries(body)])
        md = with_toc(body, pages)
        pdf = build(md)
        n_pages = _page_count(pdf)
        if n_pages <= PAGE_LIMIT:
            break
    blank = _blank_pages(pdf)
    md_path.write_text(md.rstrip() + "\n")
    dst = ROOT / "deliverables" / pdf.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(pdf, dst)
    warns = [f"PDF 빈 페이지: {blank}"] if blank else []
    if n_pages > PAGE_LIMIT:
        warns.append(f"PDF {n_pages}쪽: 압축 후에도 제출 한도 {PAGE_LIMIT}장 초과")
    left = check_report(body, refs)
    warns += [f"분량 압축 후 보고서 검사: {x}" for x in left]
    return {"report_markdown": body, "references": refs, "report_pdf_path": str(pdf), "warnings": warns,
            "audit_log": audit("pdf_renderer", blank_pages=blank, pages=n_pages, compact_level=level, dropped=dropped,
                               pdf=str(pdf.relative_to(ROOT)), copy=str(dst.relative_to(ROOT)))}
