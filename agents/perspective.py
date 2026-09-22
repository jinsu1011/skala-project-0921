"""Shared engine for the perspective agents (market, stakeholder, domain; TRL reuses the search loop).

Per technology (same template, same search budget for both):
  round 0..2: symmetric pro/con queries -> search -> LLM annotation under the C.6 Rubric -> sufficiency check
  (tech-specific pro and con evidence from >= 2 independent origin families each; C.2). Missing evidence is left as
  '근거 부족' rather than forced into balance. Then scores are computed from the Rubric's evidence conditions (code) and
  the LLM writes the rationale; the LLM's own rubric score is kept next to the code score for RUBRIC_CHECK.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from graph.runtime import ROOT, audit, config, llm_json
from graph.state import Criterion, Evidence, TechAssessment, Technology
from tools.evidence import assign_origin_groups, cap_origin_share, developer_groups, rereport_family
from tools.paper_retrieve import search_papers
from tools.web_search import search_web

PROMPTS = ROOT / "prompts"
MAX_ROUNDS = 1 + config()["retrieval"]["max_retrieval_retries"]  # first search + up to 2 re-queries (D.1)


def prompt(name: str) -> str:
    return (PROMPTS / name).read_text()


RUBRIC = prompt("rubric_c6.md")


@dataclass
class CriterionSpec:
    key: str
    name_ko: str
    weight: float
    workload: str = ""


@dataclass
class PerspectiveSpec:
    key: str
    name_ko: str
    criteria: list[CriterionSpec]
    rounds: list[list[tuple[str, str]]]          # per round: [(stance, query template)], templates use {sn}
    paper_query: str = ""                        # Korean template with {name}; empty = web only
    web_only: bool = False
    exclude_developer: bool = False              # stakeholder: developer statements are not scored
    extra_prompt: str = ""
    extra_fields: str = ""
    score_extra: str = ""
    feedback_queries: bool = True


def search_name(t: Technology) -> str:
    return f"{t.developer} {t.name} KV cache"


def origin_sets(evs: list[Evidence], ann: dict, stance: str, exclude: list[str], criterion: Optional[str] = None,
                workload: Optional[str] = None) -> set[str]:
    out = set()
    for e in evs:
        a = ann.get(e.evidence_id, {})
        if not a.get("relevant") or a.get("scope") != "tech_specific" or e.origin_group in exclude:
            continue
        for s in a.get("signals", []):
            if s.get("stance") != stance or (criterion and s.get("criterion") != criterion):
                continue
            if workload and a.get("workload") not in (workload, "both"):
                continue
            out.add(e.origin_group)
    return out


def rubric_score(pro: set[str], con: set[str], neutral: set[str]) -> Optional[float]:
    """C.6 evidence conditions. None = 판단 보류 (<= 1 origin family for the criterion)."""
    if len(pro | con | neutral) <= 1:
        return None
    # neutral-only evidence from >= 2 families scores 3: C.6 row 3 "평가 없이 사실만 전한다 / 중립 서술 위주"
    p, c = len(pro), len(con)
    if p >= 2 and c == 0:
        return 5.0
    if c >= 2 and p == 0:
        return 1.0
    if p > c and c >= 1:
        return 4.0
    if c > p and p >= 1:
        return 2.0
    return 3.0


def weighted(criteria: list[Criterion]) -> Optional[float]:
    total = sum(c.weight for c in criteria)
    have = [c for c in criteria if c.score_1to5 is not None]
    if not total or sum(c.weight for c in have) < total * 0.5:  # missing weight > 50% -> 판단 보류 (C.5)
        return None
    return round(sum(c.weight * c.score_1to5 for c in have) / sum(c.weight for c in have), 2)


def annotate(spec: PerspectiveSpec, tech: Technology, evs: list[Evidence], tag: str) -> dict:
    if not evs:
        return {}
    sys = prompt("perspective_annotate.md").format(
        perspective_ko=spec.name_ko, tech_name=tech.name, developer=tech.developer, camp=tech.camp, rubric=RUBRIC,
        criteria=", ".join(f"{c.key.split(':')[-1]}({c.name_ko.split(' ', 1)[-1] if c.workload else c.name_ko})"
                           for c in spec.criteria if not c.workload or c.workload == "W1"),
        extra=spec.extra_prompt, extra_fields=spec.extra_fields)
    items = [{"id": e.evidence_id, "source": e.publisher or e.source_group, "title": e.title,
              "date": e.published_at, "text": e.summary[:900]} for e in sorted(evs, key=lambda x: x.evidence_id)]
    out = {}
    for i in range(0, len(items), 12):  # small batches keep the JSON reliable
        data = llm_json("generator", sys, json.dumps({"evidence": items[i:i + 12]}, ensure_ascii=False),
                        tag=f"{tag}:annotate")
        for it in data.get("items", []):
            if isinstance(it, dict) and it.get("id"):
                out[it["id"]] = it
    return out


def feedback_queries(spec: PerspectiveSpec, tech: Technology, feedback: str, tag: str) -> list[tuple[str, str]]:
    sys = ("너는 검색 질의 작성기이다. Judge의 보완 지시를 반영해 영어 웹 검색 질의 2개를 만든다. 하나는 장점 근거(pro), "
           "하나는 한계 근거(con)를 찾는 질의이다. 기술명을 반드시 넣는다. JSON: {\"queries\": [{\"stance\": \"pro\", \"q\": \"...\"}]}")
    data = llm_json("generator", sys, json.dumps({"technology": search_name(tech), "perspective": spec.name_ko,
                                                   "feedback": feedback}, ensure_ascii=False), tag=f"{tag}:feedback")
    return [(q.get("stance", "neutral"), q["q"]) for q in data.get("queries", []) if q.get("q")][:2]


@dataclass
class Collected:
    evidence: list[Evidence] = field(default_factory=list)
    ann: dict = field(default_factory=dict)
    rounds: int = 0
    queries: list[str] = field(default_factory=list)


def collect(spec: PerspectiveSpec, tech: Technology, attempt: int, feedback: str,
            sufficient: Callable[[list[Evidence], dict], bool], tag: str) -> Collected:
    col = Collected()
    seen: dict[str, Evidence] = {}
    rereport: dict[str, str] = {}
    exclude = developer_groups(tech.tech_id) if spec.exclude_developer else []
    sn = search_name(tech)
    for rnd in range(MAX_ROUNDS):
        plan = [(s, q.format(sn=sn, name=tech.name, dev=tech.developer)) for s, q in spec.rounds[rnd]]
        if rnd == 0 and feedback and spec.feedback_queries:
            plan += feedback_queries(spec, tech, feedback, tag)
        new: list[Evidence] = []
        for stance, q in plan:
            col.queries.append(q)
            for e in search_web(q, stance=stance, tech=tech.tech_id, perspective=spec.key, attempt=attempt):
                if e.evidence_id not in seen:
                    seen[e.evidence_id] = e
                    new.append(e)
        if spec.paper_query and rnd == 0:
            pq = spec.paper_query.format(name=tech.name)
            col.queries.append(pq)
            for e in search_papers(pq, tech=tech.tech_id, k=3):
                e = e.model_copy(update={"perspectives": [spec.key], "attempt": attempt})
                if e.evidence_id not in seen:
                    seen[e.evidence_id] = e
                    new.append(e)
        col.ann.update(annotate(spec, tech, new, f"{tag}:r{rnd}"))
        col.rounds = rnd + 1
        for e in new:  # document-level re-report decision shared by all perspectives (D50)
            if e.evidence_id not in rereport:
                rereport[e.evidence_id] = rereport_family(e)
        col.evidence = assign_origin_groups(list(seen.values()), rereport)
        if sufficient(col.evidence, col.ann):
            break
    # keep only relevant evidence, annotated claim/scope/stances copied onto the Evidence objects
    kept = []
    for e in col.evidence:
        a = col.ann.get(e.evidence_id)
        if not a or not a.get("relevant"):
            continue
        if a.get("scope") == "tech_specific" and not names_tech(e, tech):
            a["scope"] = "category"   # C.6 concretised: tech-specific evidence must name the technology (D52)
        stances = sorted({s.get("stance") for s in a.get("signals", []) if s.get("stance") in ("pro", "con", "neutral")})
        kept.append(e.model_copy(update={"claim": a.get("claim", ""), "scope": a.get("scope", "tech_specific")
                                         if a.get("scope") in ("tech_specific", "category") else "tech_specific",
                                         "stances": stances or ["neutral"]}))
    col.evidence = cap_origin_share(kept)
    return col


def names_tech(e: Evidence, tech: Technology) -> bool:
    """True when the evidence title or text names the technology (config aliases, case-insensitive). The primary
    paper's own chunks count as naming it."""
    if e.kind == "paper":
        return e.tech == tech.tech_id
    aliases = config()["tech_meta"].get(tech.tech_id, {}).get("aliases", [tech.name])
    text = f"{e.title} {e.summary}".lower()
    return any(a.lower() in text for a in aliases)


def pro_con_sufficient(exclude: list[str]):
    def f(evs: list[Evidence], ann: dict) -> bool:
        return len(origin_sets(evs, ann, "pro", exclude)) >= 2 and len(origin_sets(evs, ann, "con", exclude)) >= 2
    return f


def criteria_block(spec: PerspectiveSpec, evs: list[Evidence], ann: dict, exclude: list[str]) -> tuple[list[Criterion], str]:
    crit, lines = [], []
    for c in spec.criteria:
        pro = origin_sets(evs, ann, "pro", exclude, c.key if not c.workload else c.key.split(":")[1], c.workload or None)
        con = origin_sets(evs, ann, "con", exclude, c.key if not c.workload else c.key.split(":")[1], c.workload or None)
        neu = origin_sets(evs, ann, "neutral", exclude, c.key if not c.workload else c.key.split(":")[1], c.workload or None)
        ck = c.key if not c.workload else c.key.split(":")[1]
        ids = {"pro": [], "con": [], "neutral": []}
        for e in evs:
            a = ann.get(e.evidence_id, {})
            if e.origin_group in exclude or a.get("scope") != "tech_specific":
                continue
            if c.workload and a.get("workload") not in (c.workload, "both"):
                continue
            for s in a.get("signals", []):
                if s.get("criterion") == ck and s.get("stance") in ids and e.evidence_id not in ids[s["stance"]]:
                    ids[s["stance"]].append(e.evidence_id)
        crit.append(Criterion(name=c.key, weight=c.weight, score_1to5=rubric_score(pro, con, neu),
                              evidence_ids=sorted(set(ids["pro"] + ids["con"] + ids["neutral"])),
                              pro_ids=sorted(ids["pro"]), con_ids=sorted(ids["con"]), workload=c.workload))
        by_id = {e.evidence_id: e for e in evs}
        lines.append(f"- {c.key} ({c.name_ko}, 가중치 {c.weight:g}): 긍정 계열 {len(pro)}, 부정 계열 {len(con)}, 중립 계열 {len(neu)}")
        for st in ("pro", "con", "neutral"):
            for i in sorted(ids[st]):
                lines.append(f"    · [{i}] {st} origin={by_id[i].origin_group}: {by_id[i].claim}")
    return crit, "\n".join(lines)


def match_scored(crit: list[Criterion], items: list, names_ko: dict[str, str]) -> list[dict]:
    """The LLM sometimes answers with the Korean display name instead of the key: match by key, then by Korean
    name, then by position when the counts agree."""
    items = [x for x in items if isinstance(x, dict)]
    out = []
    for i, c in enumerate(crit):
        ko = names_ko.get(c.name, "")
        tail = ko.split(" ", 1)[-1] if c.workload else ko
        d = next((x for x in items if str(x.get("name", "")) == c.name), None)
        d = d or next((x for x in items if c.workload and c.workload in str(x.get("name", ""))
                       and (c.name.split(":")[1] in str(x.get("name", "")) or tail in str(x.get("name", "")))), None)
        d = d or next((x for x in items if not c.workload and ko and ko in str(x.get("name", ""))), None)
        d = d or (items[i] if len(items) == len(crit) else {})
        out.append(d)
    return out


def assess(spec: PerspectiveSpec, tech: Technology, col: Collected, tag: str) -> TechAssessment:
    exclude = developer_groups(tech.tech_id) if spec.exclude_developer else []
    crit, block = criteria_block(spec, col.evidence, col.ann, exclude)
    sys = prompt("perspective_score.md").format(perspective_ko=spec.name_ko, tech_name=tech.name,
                                                developer=tech.developer, rubric=RUBRIC, extra=spec.score_extra,
                                                criteria_block=block)
    data = llm_json("generator", sys, "위 기준별 근거로 채점하라.", tag=f"{tag}:score")
    names_ko = {c.key: c.name_ko for c in spec.criteria}
    for c, d in zip(crit, match_scored(crit, data.get("criteria", []), names_ko)):
        c.llm_score = d.get("score") if isinstance(d.get("score"), (int, float)) else None
        c.rationale = d.get("rationale", "")
    pro_o = origin_sets(col.evidence, col.ann, "pro", exclude)
    con_o = origin_sets(col.evidence, col.ann, "con", exclude)
    pro_ids = sorted({i for c in crit for i in c.pro_ids})
    con_ids = sorted({i for c in crit for i in c.con_ids})
    lim = [x for x in data.get("limitations", []) if isinstance(x, str)]
    if len(pro_o) < 2:
        lim.append(f"기술 고유 장점 근거가 독립 계열 {len(pro_o)}개로 2계열 미만(근거 부족)")
    if len(con_o) < 2:
        lim.append(f"기술 고유 한계 근거가 독립 계열 {len(con_o)}개로 2계열 미만(근거 부족)")
    conf = "high" if len(pro_o) >= 2 and len(con_o) >= 2 else ("mid" if pro_o and con_o else "low")
    conf, lim = apply_condition_mismatch(crit, col, conf, lim)
    co, ad = h4_tags(col)
    return TechAssessment(criteria=crit, score=weighted(crit), summary=tool_summary(spec, tech, col, data, tag),
                          pro_ids=pro_ids, con_ids=con_ids, limitations=lim, confidence=conf, co_use_ids=co,
                          adopter_ids=ad, claims=local_claims(col))


def local_claims(col: Collected) -> dict[str, str]:
    """Keep this perspective's own claim per evidence id: the shared, merged Evidence keeps only the first one."""
    return {e.evidence_id: e.claim for e in col.evidence if e.claim}


def tool_summary(spec: PerspectiveSpec, tech: Technology, col: Collected, data: dict, tag: str) -> str:
    """Perspective summary via the summarize_sources tool (B.7): every sentence keeps its evidence ids."""
    from tools.summarize import summarize

    evs = [e for e in col.evidence if col.ann.get(e.evidence_id, {}).get("scope") == "tech_specific"] or col.evidence
    sents = summarize(evs[:30], f"{tech.name}의 {spec.name_ko} 평가: 장점과 한계를 함께", tag=f"{tag}:summary")
    text = " ".join(f"{x['text'].rstrip('.')} [{', '.join(x['evidence_ids'])}]." if "[" not in x["text"] else x["text"]
                    for x in sents)
    return text or data.get("summary", "")


def h4_tags(col: Collected) -> tuple[list[str], dict[str, list[str]]]:
    kept = {e.evidence_id for e in col.evidence}
    co = sorted(i for i, a in col.ann.items() if i in kept and a.get("relevant") and a.get("co_use") is True)
    ad: dict[str, list[str]] = {}
    for i, a in col.ann.items():
        k = a.get("adopter")
        if i in kept and a.get("relevant") and k in ("existing_gpu", "new_infra", "both"):
            ad.setdefault(k, []).append(i)
    return co, {k: sorted(v) for k, v in ad.items()}


def apply_condition_mismatch(crit: list[Criterion], col: Collected, conf: str, lim: list[str]) -> tuple[str, list[str]]:
    """C.6: evidence whose experimental conditions do not match W1/W2 or the public claim is marked '조건 불일치'
    and lowers the confidence one step."""
    mism = sorted(i for i, a in col.ann.items() if a.get("condition_mismatch") and a.get("relevant"))
    used = {i for c in crit for i in c.evidence_ids}
    hit = [i for i in mism if i in used]
    if not hit:
        return conf, lim
    for c in crit:
        if set(c.evidence_ids) & set(hit):
            c.rationale = (c.rationale + " (조건 불일치 근거 포함: " + ", ".join(sorted(set(c.evidence_ids) & set(hit))) + ")").strip()
    lim = lim + [f"조건 불일치 근거 {len(hit)}건이 있어 신뢰도를 한 단계 낮춤"]
    return {"high": "mid", "mid": "low", "low": "low"}[conf], lim


def id_conflict_warnings(state: dict, new: list[Evidence]) -> list[str]:
    """D.2: the same evidence id arriving with different source content is kept once and reported as a warning."""
    old = {e.evidence_id: e for e in state.get("evidence", [])}
    return [f"근거 ID 충돌: {e.evidence_id}의 원문 내용이 이전 기록과 다름(기존 항목 유지)" for e in new
            if e.evidence_id in old and _content_key(old[e.evidence_id]) != _content_key(e)]


def _content_key(e: Evidence) -> str:
    # web snippets are query-dependent, so a web document is identified by its title; paper chunks by their text
    return (e.title or "").strip().lower() if e.kind == "web" else e.summary[:200]


def run_perspective(spec: PerspectiveSpec, state: dict, sufficient_factory=None):
    """Returns (PerspectiveResult-like dict by tech, evidence list, audit events, warnings)."""
    attempt = state.get("perspective_retry_count", {}).get(spec.key, 0)
    feedback = state.get("judge_feedback", {}).get(spec.key, "")
    by_tech, evidence, events, warns = {}, [], [], []
    for t in state["selected_techs"]:
        exclude = developer_groups(t.tech_id) if spec.exclude_developer else []
        suff = (sufficient_factory or pro_con_sufficient)(exclude)
        tag = f"{spec.key}:{t.tech_id}:a{attempt}"
        col = collect(spec, t, attempt, feedback, suff, tag)
        ta = assess(spec, t, col, tag)
        by_tech[t.tech_id] = ta
        evidence += col.evidence
        warns += id_conflict_warnings(state, col.evidence)
        if ta.score is None and spec.key != "trl":
            warns.append(f"{spec.name_ko} 관점 {t.name}: 빠진 가중치가 50%를 넘어 판단 보류")
        events += audit(spec.key, tech=t.tech_id, attempt=attempt, rounds=col.rounds, queries=col.queries,
                        evidence=[e.evidence_id for e in col.evidence], feedback=bool(feedback))
    return by_tech, evidence, events, warns
