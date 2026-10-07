"""LangGraph State. RAG payload keys (DESIGN.md D.2) + Orchestrator-Workers control keys.

Reducers: evidence (merge_by_id), worker_results (merge_worker_results: by task_id, latest attempt wins),
review_results (operator.add, bounded by MAX_REVIEWERS), warnings (add_unique), audit_log (operator.add)."""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

Perspective = Literal["trl", "market", "stakeholder", "domain"]
PERSPECTIVES: tuple[str, ...] = ("trl", "market", "stakeholder", "domain")
PERSPECTIVE_NODE = {"trl": "trl_assessor", "market": "market_evaluator",
                    "stakeholder": "stakeholder_evaluator", "domain": "domain_evaluator"}


# ------------------------------------------------------------------ domain objects
class Technology(BaseModel):
    tech_id: str                 # turboquant / itme
    name: str                    # display name
    camp: Literal["SW", "HW"]
    developer: str               # organisation that built it (its own statements are excluded from stakeholder scores)
    developer_groups: list[str] = Field(default_factory=list)  # origin groups that count as the developer
    reason: str = ""             # team's selection reason (human, option 2)
    paper_arxiv: str = ""


class SelectionCheck(BaseModel):
    name: str                    # same_problem / same_stage / public_evidence
    passed: bool
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class SelectionValidation(BaseModel):
    by_tech: dict[str, list[SelectionCheck]] = Field(default_factory=dict)
    weaknesses: dict[str, list[str]] = Field(default_factory=dict)
    alternatives: dict[str, str] = Field(default_factory=dict)  # only filled when a check fails
    passed: bool = True


class DocumentMeta(BaseModel):
    doc_id: str
    tech: str
    camp: str
    role: str
    title: str
    pages: int
    chunks: int


class IndexStatus(BaseModel):
    n_chunks: int
    total_pages: int
    page_cap: int
    embedding_model: str
    max_seq_length: int
    reranker: Optional[str]
    reused: bool


class Query(BaseModel):
    tech: str
    text_ko: str
    text_en: str = ""
    aspect: str = ""             # principle / conditions / limits


class Evidence(BaseModel):
    evidence_id: str             # P:<chunk_id> | W:<hash>
    kind: Literal["paper", "web"]
    claim: str = ""
    summary: str = ""
    source_url: str = ""
    doc_id: str = ""
    title: str = ""
    publisher: str = ""
    published_at: str = ""
    page: Optional[int] = None
    section: str = ""
    tech: str = ""
    perspectives: list[str] = Field(default_factory=list)
    stances: list[Literal["pro", "con", "neutral"]] = Field(default_factory=list)
    scope: Literal["tech_specific", "category"] = "tech_specific"
    source_class: Literal["vendor", "third_party", "academic"] = "third_party"
    source_group: str = ""       # registered domain or vendor group
    origin_group: str = ""       # original-source family (re-reported press releases share the vendor's group)
    attempt: int = 0             # perspective run number that produced it (superseded runs are ignored)


class RetrievalGrade(BaseModel):
    sufficient: bool
    by_tech: dict[str, dict[str, bool]] = Field(default_factory=dict)  # tech -> {name, principle, number_or_limit}
    missing: dict[str, list[str]] = Field(default_factory=dict)


class TechBrief(BaseModel):
    tech_id: str
    principle: str
    scope: str
    conditions: str
    reported_results: str
    limitations: str
    evidence_ids: list[str] = Field(default_factory=list)


class Criterion(BaseModel):
    name: str
    weight: float = 0.0
    score_1to5: Optional[float] = None   # None = 판단 보류
    llm_score: Optional[float] = None     # score the LLM proposed under the Rubric (kept for RUBRIC_CHECK)
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    pro_ids: list[str] = Field(default_factory=list)
    con_ids: list[str] = Field(default_factory=list)
    workload: str = ""                   # domain only: W1 / W2


class TechAssessment(BaseModel):
    criteria: list[Criterion] = Field(default_factory=list)
    score: Optional[float] = None        # weighted mean; None = 판단 보류 (TRL never has a score)
    summary: str = ""
    pro_ids: list[str] = Field(default_factory=list)
    con_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: Literal["high", "mid", "low"] = "mid"
    # TRL only
    trl_low: Optional[int] = None
    trl_high: Optional[int] = None
    low_ids: list[str] = Field(default_factory=list)
    high_ids: list[str] = Field(default_factory=list)
    # stakeholder only: evidence ids tagged as about the technology itself vs its ecosystem/strategy (H2)
    tech_mention_ids: list[str] = Field(default_factory=list)
    ecosystem_mention_ids: list[str] = Field(default_factory=list)
    # H4: evidence about combined use with the other camp, and adopter type (existing_gpu / new_infra / both)
    claims: dict[str, str] = Field(default_factory=dict)  # this perspective's reading of each cited evidence id
    co_use_ids: list[str] = Field(default_factory=list)
    adopter_ids: dict[str, list[str]] = Field(default_factory=dict)


class PerspectiveResult(BaseModel):
    perspective: str
    by_tech: dict[str, TechAssessment] = Field(default_factory=dict)


class MatrixCell(BaseModel):
    score: Optional[float] = None
    one_liner: str = ""


class Conflict(BaseModel):
    tech_id: str
    pair: tuple[str, str]
    gap: float
    label: str                          # 상충 / 부분 상충 / 일치
    explanation: str = ""
    hypothesis_tags: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    verdict: Literal["지지", "부분 지지", "기각", "판단 보류"]
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class SynthesisResult(BaseModel):
    matrix: dict[str, dict[str, MatrixCell]] = Field(default_factory=dict)
    conflicts: list[Conflict] = Field(default_factory=list)
    hypotheses: dict[str, Hypothesis] = Field(default_factory=dict)
    h1_grid: dict[str, str] = Field(default_factory=dict)          # tech -> 일치 / 부분 괴리 (기대 선행) / ...
    sensitivity: dict[str, object] = Field(default_factory=dict)
    statements: dict[str, str] = Field(default_factory=dict)        # S1..Sn explanation sentences (Judge checks these)


class JudgeChecks(BaseModel):
    max_origin_share: float = 0.0
    pro_origins: dict[str, int] = Field(default_factory=dict)
    con_origins: dict[str, int] = Field(default_factory=dict)
    bound_origins: dict[str, int] = Field(default_factory=dict)
    lexicon_hits: int = 0


class JudgeScore(BaseModel):
    grounding: int = 0
    neutrality: int = 0
    source_diversity: int = 0
    completeness: int = 0
    passed: bool = False
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    checks: JudgeChecks = Field(default_factory=JudgeChecks)


class Reference(BaseModel):
    num: int
    kind: Literal["paper", "web", "patent"]
    text: str                           # formatted per E.3
    evidence_ids: list[str] = Field(default_factory=list)


class AuditEvent(BaseModel):
    node: str
    at: str
    detail: dict = Field(default_factory=dict)


# ------------------------------------------------------------------ reducers
def merge_by_id(left: list[Evidence] | None, right: list[Evidence] | None) -> list[Evidence]:
    """Same id -> keep the first record, union perspectives/stances. Result is sorted by id (cache-stable prompts)."""
    out: dict[str, Evidence] = {e.evidence_id: e for e in (left or [])}
    for e in right or []:
        cur = out.get(e.evidence_id)
        if cur is None:
            out[e.evidence_id] = e
            continue
        # conservative origin: if any perspective saw the item as a re-reported vendor release, keep that family
        origin = e.origin_group if e.source_class != "vendor" and e.origin_group != e.source_group \
            and cur.origin_group == cur.source_group else cur.origin_group
        out[e.evidence_id] = cur.model_copy(update={
            "origin_group": origin,
            "perspectives": sorted(set(cur.perspectives) | set(e.perspectives)),
            "stances": sorted(set(cur.stances) | set(e.stances)),
            "attempt": max(cur.attempt, e.attempt),
        })
    return [out[k] for k in sorted(out)]


def last_error_reducer(left: str | None, right: str | None) -> str | None:
    """Parallel workers may fail in the same step: keep a deterministic combined message (sorted, bounded)."""
    if not right:
        return left
    if not left:
        return right
    return " | ".join(sorted({left, right}))[:1000]


def add_unique(left: list[str] | None, right: list[str] | None) -> list[str]:
    out = list(left or [])
    for w in right or []:
        if w not in out:
            out.append(w)
    return out


# ------------------------------------------------------------------ Orchestrator-Workers objects
class AgentProfile(BaseModel):
    agent_id: str
    name: str
    capabilities: list[str]
    worker_type: str             # trl / market / stakeholder / domain (existing specialists) | research (open perspective)


class SubTask(BaseModel):
    task_id: str
    perspective: str             # free text: not restricted to the four minimum perspectives
    assigned_agent: str          # mandatory; fixed for the task's whole life (retry goes to the same agent)
    tech_ids: list[str]
    objective: str
    required_evidence: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    priority: int = 3
    attempt: int = 0


class Plan(BaseModel):
    rationale: str = ""
    plan_source: Literal["llm", "fallback"] = "llm"   # fallback = rule planner used only when the LLM plan is unusable
    coverage: dict[str, list[str]] = Field(default_factory=dict)   # minimum perspective -> task ids
    repairs: list[str] = Field(default_factory=list)              # code-side fixes applied to the LLM plan


class WorkerResult(BaseModel):
    task_id: str
    perspective: str
    assigned_agent: str
    status: Literal["ok", "error", "partial"] = "ok"
    output: Any | None = None    # PerspectiveResult (specialists) | ExtraFinding (research agents)
    evidence_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    attempt: int = 0


class ExtraFinding(BaseModel):
    perspective: str
    by_tech: dict[str, str] = Field(default_factory=dict)    # tech -> summary sentences with [evidence ids]
    queries: list[str] = Field(default_factory=list)


class JudgeFeedbackItem(BaseModel):
    task_id: str
    passed: bool
    reason: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    feedback: str = ""
    retry_required: bool = False


class JudgeResult(BaseModel):
    passed: bool
    feedback_items: list[JudgeFeedbackItem] = Field(default_factory=list)
    source: Literal["perspective_judge", "report_quality"] = "perspective_judge"


class ReviewResult(BaseModel):
    reviewer_agent: str
    target_task_id: str
    agrees_with_judge: bool = True
    issues: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    overlooked_evidence_ids: list[str] = Field(default_factory=list)
    bias_risks: list[str] = Field(default_factory=list)
    suggested_search_direction: list[str] = Field(default_factory=list)
    review_summary: str = ""
    mode: Literal["llm", "fallback"] = "llm"


class RetryTask(BaseModel):
    task_id: str
    assigned_agent: str
    original_objective: str
    judge_feedback: str
    reviewer_feedback: list[str] = Field(default_factory=list)
    retry_instruction: str
    missing_evidence: list[str] = Field(default_factory=list)
    attempt: int = 1


class ReportQuality(BaseModel):
    passed: bool = False
    scores: dict[str, int] = Field(default_factory=dict)          # groundedness / neutrality / bias_control / coverage
    deterministic: dict[str, bool] = Field(default_factory=dict)
    writing_issues: list[str] = Field(default_factory=list)
    evidence_issues: dict[str, str] = Field(default_factory=dict)  # task_id -> problem
    action: Literal["pass", "rewrite", "evidence_retry", "finalize"] = "pass"
    judge_mode: Literal["llm", "deterministic_only"] = "llm"


def merge_worker_results(left: dict[str, WorkerResult] | None,
                         right: dict[str, WorkerResult] | None) -> dict[str, WorkerResult]:
    """Parallel-safe merge by task_id. A higher attempt replaces a lower one; for the same attempt the first write is
    kept, so the result never depends on the order in which parallel workers finish. Output sorted by task_id."""
    out = dict(left or {})
    for tid, r in (right or {}).items():
        cur = out.get(tid)
        if cur is None or r.attempt > cur.attempt:
            out[tid] = r
    return {k: out[k] for k in sorted(out)}


# ------------------------------------------------------------------ State
class State(TypedDict, total=False):
    # ── control metadata (routing / termination / resume; small and bounded) ──
    run_id: str
    trace_id: str                # correlation key to LangSmith / outputs/logs (run metadata carries the same value)
    step_count: int
    max_steps: int
    status: str                  # planning / executing / judge_feedback / quality_feedback / reporting / done
    last_error: Annotated[str | None, last_error_reducer]
    plan: Plan
    subtasks: list[SubTask]      # written by the orchestrator; attempts advanced only by the retry router
    used_agents: list[str]       # computed by code from initial subtasks
    unused_agents: list[str]     # registry - used_agents (cross-reviewer pool)
    task_status: dict[str, str]  # task_id -> ok / retrying / passed / FAILED_AFTER_RETRY / PARTIAL / excluded
    judge_result: JudgeResult
    review_assignments: dict[str, list[str]]   # task_id -> reviewer agent ids (from unused_agents only)
    retry_tasks: Annotated[list[RetryTask], operator.add]
    report_quality: ReportQuality
    # ── worker payload ──
    worker_results: Annotated[dict[str, WorkerResult], merge_worker_results]
    review_results: Annotated[list[ReviewResult], operator.add]
    extra_findings: dict[str, ExtraFinding]
    # ── RAG / evaluation payload (existing) ──
    selected_techs: list[Technology]
    selection_validation: SelectionValidation
    document_manifest: list[DocumentMeta]
    index_status: IndexStatus
    queries: dict[str, list[Query]]
    rewritten_queries: dict[str, list[Query]]
    retrieved_chunks: dict[str, list[Evidence]]
    retrieval_grade: RetrievalGrade
    retrieval_retry_count: int
    evidence: Annotated[list[Evidence], merge_by_id]
    tech_brief: dict[str, TechBrief]
    trl_result: PerspectiveResult
    market_result: PerspectiveResult
    stakeholder_result: PerspectiveResult
    domain_result: PerspectiveResult
    synthesis: SynthesisResult
    judge_scores: dict[str, JudgeScore]
    failed_perspectives: list[str]
    judge_feedback: dict[str, str]
    perspective_retry_count: dict[str, int]
    report_markdown: str
    references: list[Reference]
    report_retry_count: int
    report_pdf_path: str
    warnings: Annotated[list[str], add_unique]
    audit_log: Annotated[list[AuditEvent], operator.add]


STATE_KEYS: tuple[str, ...] = tuple(State.__annotations__)
