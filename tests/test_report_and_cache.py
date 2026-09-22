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
    assert refs[0].text.startswith("a.com(2026-03-24). T. a.com, https://a.com/x")
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
    assert "| **SUMMARY** | 3 |" in written and "| **4. 관점별 평가** | 3 |" in written   # cover 1, TOC 2
    assert (tmp_path / "deliverables" / pdf.name).exists()
    assert out["report_pdf_path"] == str(pdf)
