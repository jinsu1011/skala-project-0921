import shutil

import pytest

import graph.runtime as runtime
from agents.report_writer import SUMMARY_MAX_CHARS, Citer, check_report, postprocess
from graph.runtime import OfflineCacheMiss, lexicon_hits, neutralize
from graph.state import Evidence, Reference


def _md(summary_lines, body="# 4. 관점별 평가\n본문 주장이다 [1].\n", refs="- [1] X"):
    return "# SUMMARY\n" + "\n".join(f"- {s}" for s in summary_lines) + "\n" + body + "# REFERENCE\n" + refs + "\n"


def test_summary_length_check():
    ok = _md(["핵심 발견이다 [1]."])
    assert check_report(ok, [Reference(num=1, kind="web", text="x")]) == []
    long = _md(["가" * (SUMMARY_MAX_CHARS + 10) + " [1]"])
    issues = check_report(long, [Reference(num=1, kind="web", text="x")])
    assert any("SUMMARY 분량" in i for i in issues)


def test_citation_reference_consistency():
    md = _md(["발견 [1]."], body="# 4. 관점별 평가\n주장 [2].\n")
    issues = check_report(md, [Reference(num=1, kind="web", text="x")])
    assert any("REFERENCE 불일치" in i for i in issues)


def test_uncited_prose_is_flagged_and_removed():
    md = _md(["발견 [1]."], body="# 4. 관점별 평가\n근거 없는 주장이다.\n근거 있는 주장이다 [1].\n")
    refs = [Reference(num=1, kind="web", text="x")]
    assert any("근거 없는 문장" in i for i in check_report(md, refs))
    md2, refs2, left = postprocess(md, refs)
    assert "근거 없는 주장이다." not in md2 and left == []


def test_lexicon_detects_and_neutralizes():
    assert lexicon_hits("A가 B보다 더 낫다") and lexicon_hits("we recommend it")
    assert lexicon_hits("우열·추천 판정이 아니다") == []            # design vocabulary is allowed
    assert lexicon_hits(neutralize("TurboQuant가 우수하다")) == []


def test_citer_groups_papers_and_numbers_in_order():
    ev = {"P:tq-001": Evidence(evidence_id="P:tq-001", kind="paper", doc_id="2504.19874", page=3),
          "P:tq-002": Evidence(evidence_id="P:tq-002", kind="paper", doc_id="2504.19874", page=5),
          "W:abc": Evidence(evidence_id="W:abc", kind="web", source_url="https://a.com/x", source_group="a.com",
                            title="T", published_at="2026-03-24")}
    c = Citer(ev)
    assert c.sub("문장 [W:abc]. 다음 [P:tq-001, P:tq-002].") == "문장 [1]. 다음 [2, p.3·5]."
    refs = c.references()
    assert [r.num for r in refs] == [1, 2]
    assert refs[0].text.startswith("a.com(2026-03-24). T. https://a.com/x")
    assert refs[1].text.startswith("Zandieh")


def test_offline_cache_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(runtime, "LLM_CACHE_DB", tmp_path / "c.sqlite")
    runtime.configure(offline=True)
    with pytest.raises(OfflineCacheMiss):
        runtime.llm_text("generator", "sys", "user")
    con = runtime._db()
    con.execute("INSERT INTO llm VALUES (?,?,?,?)",
                (runtime._key(runtime.config()["llm"]["generator"], "sys", "user", False), "m", "t", "cached answer"))
    con.commit()
    con.close()
    assert runtime.llm_text("generator", "sys", "user") == "cached answer"
    runtime.configure()


def test_offline_web_cache_miss(tmp_path, monkeypatch):
    import tools.web_search as ws

    monkeypatch.setattr(ws, "WEB_CACHE", tmp_path)
    runtime.configure(offline=True)
    with pytest.raises(OfflineCacheMiss):
        ws.tavily("nothing cached")
    runtime.configure()


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_markdown_and_pdf_generation(tmp_path, monkeypatch):
    import graph.platform as plat

    monkeypatch.setattr(plat, "ROOT", tmp_path)
    (tmp_path / "deliverables").mkdir()
    md = _md(["핵심 발견이다 [1]."]) .replace("- [1] X", "- [1] Org(2026-01-01). Title. a.com, https://a.com")
    out = plat.pdf_renderer({"report_markdown": md})
    pdf = tmp_path / "outputs" / f"{plat.output_stem()}.pdf"
    assert pdf.exists() and pdf.stat().st_size > 10_000
    written = (tmp_path / "outputs" / f"{plat.output_stem()}.md").read_text()
    assert written.startswith("# 목차") and written.endswith(md)          # TOC page + report body
    assert "| SUMMARY | 2 |" in written and "| 4. 관점별 평가 | 2 |" in written   # cover 1, TOC + SUMMARY 2
    assert out["audit_log"][0].detail["pages"] <= 10
    assert (tmp_path / "deliverables" / pdf.name).exists()
    assert out["report_pdf_path"] == str(pdf)


def test_trl_number_guard():
    from agents.report_writer import guard_numbers
    txt = "TurboQuant은 TRL 5–7로 추정됐다 [W:a]. TurboQuant은 TRL 3에서 7까지 추정됐다 [W:b]."
    assert guard_numbers(txt, [(5, 7), (5, 6)]) == "TurboQuant은 TRL 5–7로 추정됐다 [W:a]."


def test_compact_report_drops_optional_sections_and_renumbers():
    from agents.report_writer import compact_report
    from graph.state import Reference

    md = ("# SUMMARY\n- 요약 [2].\n# 2. 기술 선정\n## 2.1 선정 방식\n방식 [1].\n## 2.2 후보 평가표\n표 [3].\n"
          "## 2.3 선정 결과\n결과 [4].\n# REFERENCE\n- [1] a\n")
    refs = [Reference(num=i, kind="web", text=f"r{i}") for i in range(1, 5)]
    body, refs2, dropped = compact_report(md, refs, 0)
    assert dropped == [] and len(refs2) == 4
    body, refs2, dropped = compact_report(md, refs, 1)
    assert dropped == ["2.2 후보 평가표"] and "## 2.2 선정 결과" in body      # sub-sections renumbered
    assert [r.num for r in refs2] == [1, 2, 3] and [r.text for r in refs2] == ["r1", "r2", "r4"]
    assert "결과 [3]." in body and "요약 [2]." in body and "- [3] r4" in body  # citations follow the new numbers


def test_first_sentences_keeps_paragraph_citation():
    from agents.report_writer import first_sentences

    txt = "첫 문장이다. 둘째 문장은 3.5배라고 한다. 셋째 문장이다 [P:a-1, P:a-2]."
    assert first_sentences(txt, 2) == "첫 문장이다. 둘째 문장은 3.5배라고 한다 [P:a-1, P:a-2]."
    assert first_sentences("하나다 [W:x]. 둘이다 [W:y].", 1) == "하나다 [W:x]."


def test_review_feedback_rules():
    """Review feedback: clean clipping, low-quality pages, developer paper origin, H4 without surviving rationale."""
    from agents.report_writer import effective_hypotheses, quality_note
    from graph.orchestration import clip
    from graph.state import Hypothesis, ReportQuality, SynthesisResult
    from tools.evidence import low_quality, own_paper_group

    long = "가: 첫째 사유 / " + "나" * 300
    assert clip(long, 100).endswith("…(이하 생략)") and "나나나" not in clip(long, 100)
    assert clip("짧다", 100) == "짧다"
    assert low_quality("https://www.instagram.com/popular/turboquant") and not low_quality("https://arxiv.org/abs/1")
    assert own_paper_group("https://arxiv.org/html/2606.12556", "itme") == "skhynix"
    assert own_paper_group("https://arxiv.org/abs/2402.02750", "itme") is None
    syn = SynthesisResult(hypotheses={"H1": Hypothesis(verdict="부분 지지", rationale="r"),
                                      "H4": Hypothesis(verdict="지지", rationale="보완 관계이다 [W:a].")},
                          statements={})
    from agents.synthesis import split_sentences
    syn.statements = {f"S{i}": x for i, x in enumerate(split_sentences(syn.hypotheses["H4"].rationale), 1)}
    assert effective_hypotheses(syn, set(syn.statements)) == {"H1": "부분 지지", "H4": "판단 보류"}
    assert effective_hypotheses(syn, set())["H4"] == "지지"
    note = quality_note(ReportQuality(passed=False, scores={"groundedness": 4}, action="finalize",
                                      deterministic={"groundedness": True}, evidence_issues={"T03": "x"}))
    assert "근거성 통과" in note and "T03" in note and "한도 소진" in note


def test_stakeholder_counts_only_evidence_naming_the_technology(techs, mkweb):
    from agents import stakeholder
    from agents.perspective import Collected

    evs = [mkweb("a", "x.com", tech="itme"), mkweb("b", "y.com", tech="itme")]
    ann = {"W:a": {"relevant": True, "group": "a", "scope": "tech_specific",
                   "signals": [{"criterion": "group_a", "stance": "pro"}]},
           "W:b": {"relevant": True, "group": "a", "scope": "category",          # company news, no technology name
                   "signals": [{"criterion": "group_a", "stance": "pro"}]}}
    import agents.stakeholder as sh
    sh.llm_json = lambda *a, **k: {}
    ta = stakeholder.assess(techs[1], Collected(evidence=evs, ann=ann), "t")
    crit = next(c for c in ta.criteria if c.name == "group_a")
    assert crit.evidence_ids == ["W:a"]


def test_citation_label_inside_brackets_is_recognised():
    from agents.report_writer import ID_RE, first_sentences

    assert ID_RE.search("실험했다[근거 ID: P:kivi-008, P:kivi-012].").group(1) == "P:kivi-008, P:kivi-012"
    assert ID_RE.search("[P:a-1]").group(1) == "P:a-1"
    assert "P:k-1" in first_sentences("첫 문장이다. 둘째다[근거 ID: P:k-1].", 1)
