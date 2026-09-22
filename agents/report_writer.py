"""Report agent (report_writer) + the deterministic report checks used by final_check.

Structure = DESIGN.md E.1. Tables and computed values come from State (code); the LLM writes only the narrative
paragraphs, each sentence carrying evidence ids. Evidence ids are turned into numbered citations; REFERENCE lists only
what the body cites (E.3 formats). Unsupported synthesis sentences flagged by the Judge are left out.
"""
from __future__ import annotations

import json
import re

from graph.runtime import ROOT, audit, config, lexicon_hits, llm_json, neutralize
from graph.state import Reference

PAPER_REFS = {
    "2504.19874": "Zandieh, A., Daliri, M., Hadian, M., & Mirrokni, V.(2025). TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate. arXiv, 2504.19874.",
    "2606.12556": "Jang, H., Min, Y., Kim, S., Ahn, T., Kim, H., Joo, Y., Kim, H., & Kim, J.(2026). ITME: Inference Tiered Memory Expansion with Disaggregated CXL-Hybrid Memories. arXiv, 2606.12556.",
    "2405.04434": "DeepSeek-AI(2024). DeepSeek-V2: A Strong, Economical, and Efficient Mixture-of-Experts Language Model. arXiv, 2405.04434.",
    "2402.02750": "Liu, Z., Yuan, J., Jin, H., Zhong, S. et al.(2024). KIVI: A Tuning-Free Asymmetric 2bit Quantization for KV Cache. ICML 2024 (arXiv, 2402.02750).",
    "2406.19707": "Lee, W., Lee, J., Seo, J., & Sim, J.(2024). InfiniGen: Efficient Generative Inference of Large Language Models with Dynamic KV Cache Management. OSDI 2024 (arXiv, 2406.19707).",
    "2511.00321": "Kim, D., Lee, M., Kim, J. et al.(2025). Scalable Processing-Near-Memory for 1M-Token LLM Inference: CXL-Enabled KV-Cache Management Beyond GPU Limits. arXiv, 2511.00321.",
}
PUBLISHER = {"google": "Google", "skhynix": "SK hynix", "nvidia": "NVIDIA", "samsung": "Samsung", "micron": "Micron",
             "intel": "Intel", "amd": "AMD", "microsoft": "Microsoft", "amazon": "Amazon", "meta": "Meta"}
ACCESS_DATE = "2026-09-22"
ID_RE = re.compile(r"\[((?:[PW]:[^\]\s,]+)(?:\s*,\s*[PW]:[^\]\s,]+)*)\]")
CITE_RE = re.compile(r"\[\d+(?:, p\.[\d·]+)?(?:; \d+(?:, p\.[\d·]+)?)*\]")
KO = {"trl": "TRL", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인 적합성"}
SUMMARY_MAX_CHARS = 760   # about half an A4 page of the SKALA template at 10 pt (checked on the rendered PDF)
CHECKED_SECTIONS = ("SUMMARY", "3.", "4.", "5.")


def _fmt(x) -> str:
    return "판단 보류" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


def url_date(url: str) -> str:
    """Publication date from dated URL paths (/2026/03/24/, /2026-03-24, /20260324...), else ''."""
    m = re.search(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|-|$)", url) or re.search(r"/(20\d{2})(\d{2})(\d{2})\d{0,4}(?:/|$)", url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"/(20\d{2})/(\d{1,2})/", url)
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" if m and 1 <= int(m.group(2)) <= 12 else ""


def split_site(title: str, fallback: str) -> tuple[str, str]:
    """'Headline - Site Name' / 'Headline | Site' -> (headline, site name); keeps the domain when no suffix."""
    m = re.match(r"^(.{15,}?)\s+[-|·–]\s+([^-|·–]{2,40})$", title.strip())
    if m and not re.search(r"\.\.\.|…", m.group(2)):
        return m.group(1).strip(), m.group(2).strip()
    return title.strip(), fallback


class Citer:
    """Maps evidence ids to reference numbers in order of first citation (papers grouped per arXiv id)."""

    def __init__(self, ev: dict):
        self.ev, self.num, self.refs = ev, {}, []

    def _ref_key(self, e) -> str:
        return f"paper:{e.doc_id}" if e.kind == "paper" else e.evidence_id

    def cite(self, ids: list[str]) -> str:
        order, pages = [], {}
        for i in ids:
            e = self.ev.get(i)
            if e is None:
                continue
            k = self._ref_key(e)
            if k not in self.num:
                self.num[k] = len(self.refs) + 1
                self.refs.append((k, e, []))
            self.refs[self.num[k] - 1][2].append(i)
            n = self.num[k]
            if n not in pages:
                order.append(n)
                pages[n] = []
            if e.kind == "paper" and e.page and e.page not in pages[n]:
                pages[n].append(e.page)
        parts = [f"{n}, p.{'·'.join(str(p) for p in sorted(pages[n]))}" if pages[n] else str(n) for n in order]
        return f"[{'; '.join(parts)}]" if parts else ""

    def sub(self, text: str) -> str:
        return ID_RE.sub(lambda m: self.cite([x.strip() for x in m.group(1).split(",")]), text or "").replace(" []", "")

    def references(self) -> list[Reference]:
        out = []
        for k, e, ids in self.refs:
            n = self.num[k]
            if e.kind == "paper":
                out.append(Reference(num=n, kind="paper", text=PAPER_REFS.get(e.doc_id, f"{e.title}. arXiv, {e.doc_id}."),
                                     evidence_ids=sorted(set(ids))))
            else:
                site = re.sub(r"^https?://(www\.)?", "", e.source_url).split("/")[0]
                title, org = split_site(e.title or site, PUBLISHER.get(e.source_group, e.source_group))
                date = e.published_at or url_date(e.source_url) or f"{ACCESS_DATE} 접속"
                title = title.replace("|", "·")
                out.append(Reference(num=n, kind="web", text=f"{org}({date}). {title}. {site}, {e.source_url}",
                                     evidence_ids=sorted(set(ids))))
        return out


def _table(header: list[str], rows: list[list[str]], widths: str) -> str:
    esc = lambda s: str(s).replace("|", "·").replace("\n", " ")  # noqa: E731
    return "\n".join([f"<!--w:{widths}-->", "| " + " | ".join(header) + " |", "|" + "---|" * len(header)] +
                     ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows])


def _ids(ids, n=4) -> str:
    return "[" + ", ".join(ids[:n]) + "]" if ids else ""


def _narrative(state: dict, revision: str) -> dict:
    from agents.perspective import prompt

    techs = state["selected_techs"]
    ev = {e.evidence_id: e for e in state.get("evidence", [])}
    syn = state["synthesis"]
    drop = set(state.get("judge_scores", {}).get("synthesis").unsupported_claim_ids
               if state.get("judge_scores", {}).get("synthesis") else [])
    persp = {}
    used = set()
    claims: dict[str, str] = {}
    for p in ("trl", "market", "stakeholder", "domain"):
        r = state.get(f"{p}_result")
        persp[p] = {}
        for t in techs:
            ta = r.by_tech.get(t.tech_id)
            if not ta:
                continue
            ids = sorted({i for c in ta.criteria for i in c.evidence_ids} | set(ta.pro_ids) | set(ta.con_ids)
                         | set(ta.low_ids) | set(ta.high_ids))
            used |= set(ids)
            claims.update({i: c for i, c in ta.claims.items() if i not in claims})
            persp[p][t.name] = {"score": ta.score, "trl": [ta.trl_low, ta.trl_high], "confidence": ta.confidence,
                                "summary": ta.summary, "limitations": ta.limitations,
                                "criteria": [{"name": c.name, "score": c.score_1to5, "rationale": c.rationale,
                                              "pro": c.pro_ids, "con": c.con_ids} for c in ta.criteria]}
    payload = {
        "perspectives": persp,
        "synthesis": {"conflicts": [{"tech": c.tech_id, "pair": c.pair, "gap": c.gap, "label": c.label,
                                     "explanation": c.explanation} for c in syn.conflicts],
                      "hypotheses": {h: v.model_dump() for h, v in syn.hypotheses.items()},
                      "unsupported_sentences_to_avoid": [syn.statements[s] for s in drop if s in syn.statements] + [
                          x for p in ("trl", "market", "stakeholder", "domain")
                          for x in (state.get("judge_scores", {}).get(p).unsupported_claim_ids
                                    if state.get("judge_scores", {}).get(p) else [])]},
        "evidence": [{"id": i, "tech": ev[i].tech, "origin": ev[i].origin_group,
                      "claim": claims.get(i) or ev[i].claim} for i in sorted(used) if i in ev],
    }
    sys = prompt("report_writer.md").format(revision=revision)
    return llm_json("generator", sys, json.dumps(payload, ensure_ascii=False), tag=f"report_writer:{bool(revision)}")


_TRL_RANGE = re.compile(r"TRL\s*(\d)\s*(?:~|–|-|에서)\s*(\d)")


def guard_numbers(text: str, ranges: list[tuple[int, int]]) -> str:
    """Drop narrative sentences whose TRL range differs from the code-computed ranges (LLM number slips)."""
    ok = set(ranges)
    kept = [x for x in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if x
            and all((int(a), int(b)) in ok for a, b in _TRL_RANGE.findall(x))]
    return " ".join(kept)


def build_markdown(state: dict, nar: dict) -> tuple[str, list[Reference]]:
    techs = state["selected_techs"]
    names = {t.tech_id: t.name for t in techs}
    ev = {e.evidence_id: e for e in state.get("evidence", [])}
    ct = Citer(ev)
    syn = state["synthesis"]
    js = state.get("judge_scores", {})
    drop = set(js["synthesis"].unsupported_claim_ids) if js.get("synthesis") else set()
    briefs = state.get("tech_brief", {})
    idx = state.get("index_status")
    manifest = state.get("document_manifest", [])
    sv = state.get("selection_validation")
    L: list[str] = []
    add = L.append

    def brief_ids(tid, n=2):
        b = briefs.get(tid)
        return b.evidence_ids[:n] if b else []

    trl_r = state["trl_result"].by_tech
    ranges = [(v.trl_low, v.trl_high) for v in trl_r.values() if v.trl_low is not None]
    for k in ("trl", "market", "stakeholder", "domain"):
        nar[k] = guard_numbers(nar.get(k, ""), ranges)
    # ---------------- SUMMARY: result lines are generated from State; the LLM adds at most two findings
    add("# SUMMARY")
    add("")
    conf_ko = {"high": "높음", "mid": "보통", "low": "낮음"}
    for t in techs:
        tr = trl_r.get(t.tech_id)
        m = syn.matrix.get(t.tech_id, {})
        ids = (tr.low_ids[:1] + tr.high_ids[:1]) if tr else []
        add(f"- {t.name}: TRL {tr.trl_low}–{tr.trl_high}(추정, 신뢰도 {conf_ko[tr.confidence]}), " if tr and tr.trl_low is not None
            else f"- {t.name}: TRL 판단 보류, ")
        L[-1] += (f"시장성 {_fmt(m['market'].score)}, 이해관계자 {_fmt(m['stakeholder'].score)}, "
                  f"도메인 적합성 {_fmt(m['domain'].score)} {ct.cite(ids)}")
    hy = syn.hypotheses
    add("- 가설 판정: " + ", ".join(f"{h} {hy[h].verdict}" for h in ("H1", "H2", "H3", "H4") if h in hy) + " "
        + ct.cite(sorted({i for h in hy.values() for i in h.evidence_ids})[:3]))
    extra = [x for x in nar.get("summary", []) if "우열이나 추천이 아니라" not in x]
    for x in extra[:2]:
        g = guard_numbers(x, ranges)
        if g and ID_RE.search(g):
            add(f"- {ct.sub(neutralize(g))}")
    add("- 우열이나 추천이 아니라 관점별 평가 차이와 그 근거를 정리한 결과이다(판단 보류는 근거 부족을 뜻함).")
    add("")
    add("---pagebreak---")
    add("")
    # ---------------- 1
    tq, it = (techs[0].tech_id, techs[1].tech_id) if len(techs) > 1 else (techs[0].tech_id, techs[0].tech_id)
    add("# 1. 분석 배경")
    add("## 1.1 KV cache 병목")
    add("LLM은 토큰을 생성하면서 앞에서 계산한 Key·Value를 KV cache에 저장해 다시 쓰고, 그 크기는 문맥 길이와 동시 요청 수에 비례해 커진다. "
        f"두 기술의 원 논문도 이 메모리 병목을 출발점으로 삼는다 {ct.cite(brief_ids(tq, 1) + brief_ids(it, 1))}. "
        "Llama-3.1-8B(레이어 32, KV 헤드 8, 헤드 차원 128, FP16) 기준으로 토큰 하나에 128 KiB, 128K 토큰 요청 한 건에 16 GiB가 필요하다(조 계산, 설계서 A.1).")
    add("## 1.2 두 진영의 접근과 함께 쓰는 경우")
    add(f"SW 진영은 KV를 작게 만들고 HW 진영은 KV를 둘 공간을 넓힌다. {names[tq]}는 데이터 표현(비트 수)을 바꾸고 "
        f"{ct.cite(brief_ids(tq))}, {names[it]}는 저장 위치(메모리 계층)를 바꾼다 {ct.cite(brief_ids(it))}. "
        "두 방식은 계층이 달라 함께 쓰일 수 있으며, 이 가능성은 가설 H4로 검증한다.")
    add("## 1.3 분석 도메인과 문제 정의")
    add("도메인은 데이터센터·클라우드 장문맥 LLM 서빙 하나이다. 도메인 적합성은 W1(요청 하나가 32K~128K 토큰 이상인 장문맥 배치 추론)과 "
        "W2(앞 대화의 KV를 다시 쓰는 고동시성 다중 턴 서빙)로 나눠 채점한다(설계서 C.1). 결과물은 기술 추천이나 우열 판정이 아니라 "
        "관점별 평가 차이와 그 근거·조건을 따라갈 수 있게 정리한 보고서이다.")
    add("## 1.4 분석 질문과 가설")
    add(_table(["구분", "내용"], [
        ["Q1", "두 기술은 관점마다 어떤 근거로 어떻게 평가되는가"],
        ["Q2", "관점끼리 어디서 일치하고 어디서 엇갈리며, 그 이유는 무엇인가"],
        ["Q3", "워크로드(W1, W2)와 함께 쓰는 경우에 따라 평가 조건이 어떻게 달라지는가"],
        ["H1", "기술 성숙도와 시장의 평가는 같은 방향이 아닐 수 있다(TRL×시장 격자로 판정)"],
        ["H2", "이해관계자 반응은 기술 자체보다 속한 생태계의 영향을 더 받을 수 있다"],
        ["H3", "같은 도메인 안에서도 W1과 W2에 따라 적용 조건과 제약에 대한 평가가 달라질 수 있다"],
        ["H4", "두 방식은 경쟁보다 보완 관계일 수 있다"]], "2.0,14.0"))
    add("")
    # ---------------- 2
    add("# 2. 기술 선정")
    add("## 2.1 선정 방식과 기준")
    add("가이드의 두 방식 중 조가 직접 고르는 2안을 택했다. Doc Pool 후보 6개를 비교 공정성 35%, 근거 확보성 25%, 산업 연관성 25%, "
        "최신성 15%로 채점하고, KV cache를 직접 다루지 않거나 적용 시점이 크게 다른 후보는 제외했다(설계서 A.2). 에이전트는 선정을 바꾸지 않고 원문 근거로 검증만 한다.")
    add("## 2.2 후보 평가표")
    add(_table(["후보", "진영", "공정성", "근거", "산업", "최신", "가중합"], [
        ["TurboQuant", "SW", "5", "3", "5", "4", "4.35"], ["KIVI", "SW", "5", "4", "3", "2", "3.80"],
        ["DeepSeek MLA", "SW", "1", "5", "4", "2", "2.90 (사전학습 필요로 제외)"], ["ITME", "HW", "5", "3", "5", "5", "4.50"],
        ["InfiniGen", "HW", "4", "3", "2", "2", "2.95"], ["CXL-PNM", "HW", "3", "2", "3", "4", "2.90"]],
        "3.2,1.4,1.6,1.6,1.6,1.6,5.0"))
    add("")
    add("출처: 조 채점(설계서 A.3). 진영별 최고점인 TurboQuant(4.35)와 ITME(4.50)를 선정했다.")
    add("## 2.3 선정 결과와 사유")
    for t in techs:
        add(f"- **{t.name}** ({t.developer}, {t.camp}): {t.reason} {ct.cite(brief_ids(t.tech_id, 1))}")
    add("## 2.4 에이전트의 선정 검증 결과")
    rows = []
    label = {"same_problem": "같은 문제(KV cache 용량)", "same_stage": "같은 적용 시점(서빙 단계)",
             "public_evidence": "공개 근거 충분성"}
    for t in techs:
        for c in (sv.by_tech.get(t.tech_id, []) if sv else []):
            rows.append([t.name, label.get(c.name, c.name), "충족" if c.passed else "미충족",
                         ct.sub(neutralize(c.rationale)) or ct.cite(c.evidence_ids)])
    add(_table(["기술", "검증 항목", "결과", "원문 근거"], rows, "2.3,3.4,1.6,8.7"))
    add("")
    for t in techs:
        for w in (sv.weaknesses.get(t.tech_id, []) if sv else [])[:2]:
            s = ct.sub(neutralize(w))
            if CITE_RE.search(s):
                add(f"- {t.name} 약점: {s}")
    add(f"- 검증 결과 선정은 {'모든 항목을 충족했다' if sv and sv.passed else '일부 항목이 미충족으로 기록됐으며 선정은 유지했다'}. "
        "평가 주체가 SK 교육과정 소속이고 ITME가 SK hynix 기술이라는 점은 6장 한계에 적었다.")
    add("")
    # ---------------- 3
    add("# 3. 기술 개요")
    for i, t in enumerate(techs, 1):
        b = briefs.get(t.tech_id)
        add(f"## 3.{i} {t.name}")
        if not b:
            add("근거 부족으로 개요를 만들지 못했다.")
            continue
        for lab, val in (("작동 원리", b.principle), ("적용 범위", b.scope), ("실험 조건", b.conditions),
                         ("보고된 성능", b.reported_results), ("한계", b.limitations)):
            add(f"- **{lab}**: {ct.sub(neutralize(val))}")
    add(f"## 3.{len(techs) + 1} 비교표")
    add(_table(["항목"] + [t.name for t in techs], [
        ["바꾸는 것", "데이터 표현(비트 수)", "저장 위치(메모리 계층)"][:1 + len(techs)],
        ["진영·개발사"] + [f"{t.camp} · {t.developer}" for t in techs],
        ["원 논문"] + [f"arXiv {t.paper_arxiv}" for t in techs],
        ["보고된 성능 근거"] + [ct.cite(brief_ids(t.tech_id, 2)) for t in techs]], "3.4,6.3,6.3"))
    add("")
    # ---------------- 4
    add("# 4. 관점별 평가")
    add("## 4.0 평가 기준")
    add("기준마다 1점(부정적 평가가 많음)~5점(긍정적 평가가 많음)의 인식 점수를 설계서 C.6 Rubric의 근거 조건으로 매긴다. 점수는 기술의 품질이 아니라 "
        "공개 자료에 나타난 평가의 방향이다. 해당 기술 고유 근거만 세고 같은 원 출처 계열은 하나로 센다. 근거가 1계열 이하인 기준은 판단 보류로 두고 계산에서 빼며, "
        "빠진 가중치가 50%를 넘으면 관점 전체를 판단 보류로 한다. 가중치는 시장성 25/30/30/15, 이해관계자 집단별 25, 도메인 W1·W2 각 50(항목별 20)이다.")
    # 4.1 TRL
    add("## 4.1 기술 성숙도(TRL)")
    trl = state["trl_result"]
    rows = []
    for t in techs:
        ta = trl.by_tech.get(t.tech_id)
        if not ta:
            continue
        rng = f"{ta.trl_low}–{ta.trl_high}" if ta.trl_low is not None else "판단 보류"
        rows.append([t.name, rng, {"high": "높음", "mid": "보통", "low": "낮음"}[ta.confidence],
                     ct.cite(ta.low_ids) or "없음", ct.cite(ta.high_ids) or "없음"])
    add(_table(["기술", "TRL 범위(추정)", "신뢰도", "하한 근거", "상한 근거"], rows, "2.6,2.6,1.8,4.5,4.5"))
    add("")
    add("TRL은 공개 정보로 추정한 범위이다. 하한은 공개 근거로 확인된 가장 높은 단계, 상한은 발표·계획 같은 부분 신호로 보이는 단계이며, "
        "양쪽 모두 개발사 외 독립 근거가 있으면 신뢰도 높음, 한쪽만 있으면 보통, 개발사 자료뿐이면 낮음이다(설계서 C.4).")
    if nar.get("trl"):
        add(ct.sub(neutralize(nar["trl"])))
    # 4.2 market
    add("## 4.2 시장성")
    mk = state["market_result"]
    crit_ko = {"size_growth": "시장 규모·성장 (25)", "adoption": "상용화·채택 (30)", "ecosystem": "생태계 지원 (30)",
               "cost": "도입 비용 구조 (15)"}
    rows = []
    for key, ko in crit_ko.items():
        row = [ko]
        for t in techs:
            c = next((c for c in mk.by_tech[t.tech_id].criteria if c.name == key), None) if t.tech_id in mk.by_tech else None
            row.append(f"{_fmt(c.score_1to5)} {ct.cite(c.pro_ids[:2] + c.con_ids[:2])}" if c else "판단 보류")
        rows.append(row)
    rows.append(["**가중 평균**"] + [f"**{_fmt(mk.by_tech[t.tech_id].score)}**" if t.tech_id in mk.by_tech else "판단 보류"
                                   for t in techs])
    add(_table(["기준 (가중치)"] + [t.name for t in techs], rows, "4.0,6.0,6.0"))
    add("")
    if nar.get("market"):
        add(ct.sub(neutralize(nar["market"])))
    # 4.3 stakeholder
    add("## 4.3 이해관계자")
    sh = state["stakeholder_result"]
    grp = {"group_a": "(a) 클라우드·데이터센터", "group_b": "(b) GPU·메모리 벤더", "group_c": "(c) 개발자",
           "group_d": "(d) 투자·분석·언론"}
    stance = {5.0: "지지(5)", 3.0: "중립·혼재(3)", 1.0: "우려(1)", None: "판단 보류"}
    rows = []
    for key, ko in grp.items():
        row = [ko]
        for t in techs:
            c = next((c for c in sh.by_tech[t.tech_id].criteria if c.name == key), None) if t.tech_id in sh.by_tech else None
            row.append(f"{stance.get(c.score_1to5, _fmt(c.score_1to5))} {ct.cite(c.evidence_ids[:3])}" if c else "판단 보류")
        rows.append(row)
    rows.append(["**가중 평균**"] + [f"**{_fmt(sh.by_tech[t.tech_id].score)}**" if t.tech_id in sh.by_tech else "판단 보류"
                                   for t in techs])
    add(_table(["집단 (각 25)"] + [t.name for t in techs], rows, "4.0,6.0,6.0"))
    add("")
    add("개발사(TurboQuant는 Google, ITME는 SK hynix)의 발언과 보도자료는 점수에서 제외하고 참고로만 인용했다.")
    if nar.get("stakeholder"):
        add(ct.sub(neutralize(nar["stakeholder"])))
    # 4.4 domain
    add("## 4.4 도메인 적합성(W1·W2)")
    dm = state["domain_result"]
    items = [("cost", "비용"), ("latency", "지연"), ("throughput", "처리량·동시성"), ("accuracy", "정확도 영향"),
             ("integration", "통합 난이도")]
    rows = []
    for k, ko in items:
        row = [ko]
        for t in techs:
            for w in ("W1", "W2"):
                c = next((c for c in dm.by_tech[t.tech_id].criteria if c.name == f"{w}:{k}"), None) \
                    if t.tech_id in dm.by_tech else None
                row.append(f"{_fmt(c.score_1to5)} {ct.cite(c.evidence_ids[:2])}" if c else "판단 보류")
        rows.append(row)
    total = ["**가중 평균**"]
    for t in techs:
        total += [f"**{_fmt(dm.by_tech[t.tech_id].score)}**" if t.tech_id in dm.by_tech else "판단 보류", ""]
    rows.append(total)
    hdr = ["항목 (각 20)"] + [f"{t.name} {w}" for t in techs for w in ("W1", "W2")]
    add(_table(hdr, rows, "3.2,3.2,3.2,3.2,3.2"))
    add("")
    h3 = syn.sensitivity.get("h3_stats", {})
    add("워크로드별 평균: " + "; ".join(f"{names[t]} W1 {_fmt(v.get('W1'))}, W2 {_fmt(v.get('W2'))}" for t, v in h3.items())
        + ". 가중 평균 행은 기술별 W1·W2 합산값이다(W1 열에 표기).")
    if nar.get("domain"):
        add(ct.sub(neutralize(nar["domain"])))
    add("")
    # ---------------- 5
    add("# 5. 시사점")
    add("## 5.1 관점 간 일치·상충 표")
    rows = []
    for t in techs:
        m = syn.matrix.get(t.tech_id, {})
        trl_ta = trl.by_tech.get(t.tech_id)
        rows.append([t.name, f"{trl_ta.trl_low}–{trl_ta.trl_high}" if trl_ta and trl_ta.trl_low is not None else "판단 보류"]
                    + [_fmt(m[p].score) if p in m else "판단 보류" for p in ("market", "stakeholder", "domain")])
    add(_table(["기술", "TRL(범위)", "시장성", "이해관계자", "도메인"], rows, "3.2,3.2,3.2,3.2,3.2"))
    add("")
    rows = [[names[c.tech_id], f"{KO[c.pair[0]]} – {KO[c.pair[1]]}", f"{c.gap:.2f}", c.label] for c in syn.conflicts]
    add(_table(["기술", "관점 쌍", "점수 차", "판정"], rows or [["-", "비교 가능한 관점 쌍 없음(판단 보류)", "-", "-"]],
               "3.0,6.0,2.5,4.5"))
    add("")
    add("판정 기준: 점수 차 2.0 이상 상충, 1.0 이상 2.0 미만 부분 상충, 1.0 미만 일치. 같은 기술 안에서만 비교하고 두 기술을 합치거나 순위를 매기지 않는다.")
    add("## 5.2 주요 상충 지점")
    stmt_by_text = {v: k for k, v in syn.statements.items()}
    wrote = False
    for c in syn.conflicts:
        if c.label == "일치":
            continue
        from agents.synthesis import split_sentences
        kept = [s for s in split_sentences(c.explanation) if stmt_by_text.get(s) not in drop]
        text = ct.sub(neutralize(" ".join(kept)))
        if text and CITE_RE.search(text):
            add(f"- **{names[c.tech_id]} {KO[c.pair[0]]}–{KO[c.pair[1]]} ({c.label}, 차이 {c.gap:.2f})**: {text}")
            wrote = True
    if not wrote:
        add("- 상충 또는 부분 상충으로 판정된 관점 쌍이 없거나, 해설 문장이 Judge 근거 검사를 통과하지 못해 표의 판정만 싣는다.")
    add("## 5.3 가설 판정")
    rows = []
    from agents.synthesis import split_sentences
    for h in ("H1", "H2", "H3", "H4"):
        v = syn.hypotheses.get(h)
        if not v:
            continue
        if h in ("H1", "H3"):
            reason = (v.rationale.replace("신뢰도 high", "신뢰도 높음").replace("신뢰도 mid", "신뢰도 보통")
                      .replace("신뢰도 low", "신뢰도 낮음") + " " + ct.cite(v.evidence_ids[:4]))
        else:
            kept = [s for s in split_sentences(v.rationale) if stmt_by_text.get(s) not in drop]
            reason = ct.sub(neutralize(" ".join(kept))) or "근거 문장이 Judge 검사를 통과하지 못함"
            if not CITE_RE.search(reason) and v.evidence_ids:
                reason += " " + ct.cite(v.evidence_ids[:3])
        rows.append([h, v.verdict, reason])
    add(_table(["가설", "판정", "근거"], rows, "1.4,2.2,12.4"))
    add("")
    add("H1은 TRL 범위의 가운데 값과 시장성 점수로 설계서 C.5의 3×3 격자에서 코드로 판정했다: " +
        "; ".join(f"{names[t]} {g}" for t, g in syn.h1_grid.items()) + ".")
    add("## 5.4 조건별 시사점(추천 아님)")
    for s in nar.get("conditions", [])[:4]:
        s2 = ct.sub(neutralize(s))
        if CITE_RE.search(s2):
            add(f"- {s2}")
    add("## 5.5 기준값 민감도 점검")
    sens = syn.sensitivity
    rows = [[r["variant"], f"{r['changed']} / {r['cells']}칸"] for r in sens.get("conflict", [])]
    rows += [[r["variant"], f"{r['changed']} / {r['cells']}칸 (H1 판정: {r['verdict']})"] for r in sens.get("h1", [])]
    add(_table(["바꾼 기준(±0.5)", "판정이 바뀐 칸"], rows, "7.0,9.0"))
    add("")
    add("2.0·1.0과 격자 경계는 절대 기준이 아니라 분류를 일관되게 하려고 미리 정한 값이므로, 각 값을 0.5씩 바꿨을 때 판정이 달라지는 칸 수를 함께 보고한다.")
    add("")
    # ---------------- 6
    add("# 6. 한계점")
    add("## 6.1 공개 정보 기반 추정의 한계")
    add("- TRL과 모든 인식 점수는 공개 자료로 추정한 값이다. TRL 4~6 구간은 수율·실측치가 영업 비밀이라 공개 정보가 가장 적다.")
    add("- ITME는 2026년 6월에 공개돼 공개 후 기간이 짧고, 제3자 평가·채택 신호가 쌓이기 전이다. 논문 저자와 측정 제품이 모두 SK hynix이다.")
    add("- TurboQuant는 논문 실험이 품질·왜곡률 위주이고 서빙 엔진 통합과 처리량 수치가 논문에 없다.")
    add("## 6.2 확증편향 방지 조치와 실행 결과")
    rows = []
    for p in ("trl", "market", "stakeholder", "domain"):
        s = js.get(p)
        if not s:
            continue
        c = s.checks
        bal = (", ".join(f"{names.get(t, t)} 하·상한 {n}" for t, n in c.bound_origins.items()) if p == "trl" else
               ", ".join(f"{names.get(t, t)} 찬{c.pro_origins.get(t, 0)}/반{c.con_origins.get(t, 0)}" for t in c.pro_origins))
        rows.append([KO[p], f"{s.grounding}/{s.neutrality}/{s.source_diversity}/{s.completeness}",
                     f"{c.max_origin_share:.0%}", bal, str(c.lexicon_hits),
                     str(state.get("perspective_retry_count", {}).get(p, 0)), "통과" if s.passed else "판정 불확실"])
    add(_table(["관점", "Judge 근거·중립·다양성·완결", "최대 계열 비중", "찬반·범위 근거 계열", "우열 어휘", "재실행", "결과"],
               rows, "2.2,2.9,1.9,4.2,1.4,1.4,2.0"))
    add("")
    cls = {"vendor": 0, "third_party": 0, "academic": 0}
    used_ids = {i for k, e, ids in ct.refs for i in ids}
    for e in state.get("evidence", []):
        if e.evidence_id in used_ids:
            cls[e.source_class] += 1
    add(f"- 본문 인용 근거의 출처 구분: 벤더 {cls['vendor']}건, 제3자 {cls['third_party']}건, 학술 {cls['academic']}건. "
        "관점마다 지지·반대 질의를 짝지어 검색했고, 한 원 출처 계열이 웹 근거의 50%를 넘지 않게 했으며, 개발사 발언은 이해관계자 점수에서 뺐다.")
    add("- Judge 판정식은 설계서 D.5를 그대로 썼다. 기준에 못 미친 관점만 최대 2회 다시 실행했고, 한도 후에도 미달이면 판정 불확실로 남겼다.")
    unc = [w for w in state.get("warnings", []) if w.startswith("판정 불확실")]
    for w in unc:
        add(f"- {w}")
    add("## 6.3 분석 방법의 한계")
    add(f"- 검색 구성(bge-m3, 3중 RRF + reranker)은 한국어→영어 42문항 개발 지표(구현 조건 Hit@1 0.786, Hit@5 0.976, MRR@10 0.863)로 골랐다. "
        "설정 선택과 성능 보고를 같은 42문항으로 해 개발셋과 테스트셋이 분리되지 않았고 과적합 가능성이 있다.")
    add(f"- Judge(gpt-4.1)는 생성 모델(gpt-4.1-mini)보다 상위 모델이지만 같은 계열이라 자기 평가 편향을 줄이는 효과가 제한적이다.")
    add("- 평가 주체인 우리 조가 SK 교육과정 소속이고 ITME는 SK hynix 기술이다. 두 기술에 같은 질의 틀·검색 한도·Rubric을 썼지만 소속에 따른 편향 가능성을 배제할 수 없다.")
    add("- 웹 근거는 Tavily 검색 결과에 의존하며 검색 시점(2026-09-22)의 자료만 반영한다. 점수는 근거 개수를 가중치로 쓰지 않지만 검색되는 자료의 양에 영향을 받는다.")
    ws = state.get("warnings", [])
    other = [w for w in ws if not w.startswith(("판정 불확실", "보고서 검수", "근거 ID 충돌"))]
    n_conf = sum(1 for w in ws if w.startswith("근거 ID 충돌"))
    if n_conf:
        other.append(f"같은 근거 ID에 다른 제목이 들어온 경우 {n_conf}건(기존 항목 유지, 실행 로그에 기록)")
    if other:
        add("- 실행 중 기록된 경고: " + "; ".join(other[:8]))
    if idx:
        add(f"- RAG 문서는 Doc Pool 논문 {len(manifest)}편 {idx.total_pages}쪽(한도 {idx.page_cap}쪽), 청크 {idx.n_chunks}개이다.")
    add("")
    # ---------------- REFERENCE (papers first, then web; numbers follow first citation within each group)
    old = ct.references()
    ordered = [r for r in old if r.kind == "paper"] + [r for r in old if r.kind != "paper"]
    remap = {r.num: i for i, r in enumerate(ordered, 1)}
    refs = [r.model_copy(update={"num": remap[r.num]}) for r in ordered]
    body = "\n".join(L)
    body = CITE_RE.sub(lambda m: re.sub(r"(?<=[\[;] )(\d+)|(?<=\[)(\d+)",
                                        lambda n: str(remap[int(n.group(0))]), m.group(0)), body)
    L[:] = body.split("\n")
    add("# REFERENCE")
    for kind, ko in (("paper", "논문"), ("web", "웹페이지"), ("patent", "특허")):
        items = [r for r in refs if r.kind == kind]
        if not items:
            continue
        add(f"**{ko}**")
        add("")
        for r in items:
            add(f"- [{r.num}] {r.text}")
        add("")
    return "\n".join(L) + "\n", refs


def report_writer(state: dict) -> dict:
    last = next((e for e in reversed(state.get("audit_log", [])) if e.node == "final_check"), None)
    revision = ""
    if last and not last.detail.get("passed"):
        revision = "이전 원고의 검수 지적을 반영해 다시 쓴다: " + "; ".join(last.detail.get("issues", []))
    nar = _narrative(state, revision)
    md, refs = build_markdown(state, nar)
    return {"report_markdown": md, "references": refs,
            "audit_log": audit("report_writer", revision=bool(revision), refs=len(refs), chars=len(md))}


# ---------------------------------------------------------------- table of contents (E.1)
def toc_entries(md: str) -> list[tuple[int, str]]:
    out = []
    for ln in md.split("\n"):
        m = re.match(r"^(#{1,2}) (.+)", ln)
        if m and m.group(2) != "목차":
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def with_toc(md: str, pages: dict[str, int] | None = None) -> str:
    """Insert a 목차 page before SUMMARY. `pages` maps heading text to the printed page (second render pass)."""
    body = re.sub(r"^# 목차\n.*?---pagebreak---\n\n", "", md, flags=re.S)
    rows = []
    pg = lambda t: str(pages.get(t, "")) if pages else ""  # noqa: E731
    for lvl, text in toc_entries(body):
        if lvl == 1:
            rows.append([f"**{text}**", [], pg(text)])
        elif rows:
            rows[-1][1].append(f"{text} ({pg(text)})" if pages else text)
    rows = [[a, " · ".join(b), c] for a, b, c in rows]
    toc = "# 목차\n\n" + _table(["장", "절 (쪽)", "쪽"], rows, "3.6,11.2,1.2") + "\n\n---pagebreak---\n\n"
    return toc + body


# ---------------------------------------------------------------- checks used by final_check
def sections(md: str) -> list[tuple[str, list[str]]]:
    out, cur, buf = [], None, []
    for ln in md.split("\n"):
        if ln.startswith("# "):
            if cur is not None:
                out.append((cur, buf))
            cur, buf = ln[2:].strip(), []
        else:
            buf.append(ln)
    if cur is not None:
        out.append((cur, buf))
    return out


def _prose(ln: str) -> bool:
    s = ln.strip()
    return bool(s) and not s.startswith(("|", "#", "<!--", "---", "**논문", "**웹", "![", "출처:", "판정 기준", "H1은",
                                          "워크로드별", "개발사(", "TRL은 공개", "기준마다", "2.0·1.0",
                                          "- 상충 또는 부분 상충으로"))


def summary_text(md: str) -> str:
    for name, body in sections(md):
        if name == "SUMMARY":
            return "\n".join(l for l in body if l.strip() and not l.startswith("---"))
    return ""


def check_report(md: str, refs: list[Reference]) -> list[str]:
    issues = []
    for name, body in sections(md):
        if not name.startswith(CHECKED_SECTIONS):
            continue
        for ln in body:
            if _prose(ln) and not CITE_RE.search(ln) and "우열이나 추천이 아니라" not in ln:
                issues.append(f"근거 없는 문장({name}): {ln.strip()[:40]}")
    cited = {int(n) for m in CITE_RE.findall(md.split("# REFERENCE")[0]) for n in re.findall(r"(?:\[|; )(\d+)", m)}
    listed = {r.num for r in refs}
    if cited != listed:
        issues.append(f"REFERENCE 불일치: 본문 인용 {sorted(cited - listed)} 누락, 미인용 {sorted(listed - cited)}")
    if len(summary_text(md)) > SUMMARY_MAX_CHARS:
        issues.append(f"SUMMARY 분량 초과({len(summary_text(md))}자 > {SUMMARY_MAX_CHARS}자)")
    hits = lexicon_hits(md.split("# REFERENCE")[0])
    if hits:
        issues.append(f"우열·추천 어휘 {len(hits)}건: {', '.join(sorted(set(hits)))}")
    return issues


def postprocess(md: str, refs: list[Reference]) -> tuple[str, list[Reference], list[str]]:
    """Deterministic fallback after the revision limit: neutralise wording, cut SUMMARY to half a page, drop prose lines
    without citations in checked sections, drop REFERENCE entries no longer cited."""
    body, _, _ = md.partition("# REFERENCE")
    out, cur, summary_chars = [], "", 0
    for ln in body.split("\n"):
        if ln.startswith("# "):
            cur = ln[2:].strip()
        elif cur.startswith(CHECKED_SECTIONS) and _prose(ln) and not CITE_RE.search(ln) and "우열이나 추천이 아니라" not in ln:
            continue
        if cur == "SUMMARY" and ln.startswith("- "):
            if summary_chars + len(ln) > SUMMARY_MAX_CHARS:
                continue
            summary_chars += len(ln)
        out.append(neutralize(ln))
    body = "\n".join(out)
    cited = {int(n) for m in CITE_RE.findall(body) for n in re.findall(r"(?:\[|; )(\d+)", m)}
    refs2 = [r for r in refs if r.num in cited]
    ref_md = ["# REFERENCE"]
    for kind, ko in (("paper", "논문"), ("web", "웹페이지"), ("patent", "특허")):
        items = [r for r in refs2 if r.kind == kind]
        if items:
            ref_md += [f"**{ko}**", ""] + [f"- [{r.num}] {r.text}" for r in items] + [""]
    md2 = body + "\n".join(ref_md) + "\n"
    return md2, refs2, check_report(md2, refs2)
