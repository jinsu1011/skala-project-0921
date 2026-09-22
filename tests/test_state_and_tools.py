from graph.state import STATE_KEYS, State, add_unique, merge_by_id
from tools.evidence import (assign_origin_groups, cap_origin_share, classify_url, max_origin_share, registered_domain,
                            web_id)

DESIGN_KEYS = ["run_id", "selected_techs", "selection_validation", "document_manifest", "index_status", "queries",
               "rewritten_queries", "retrieved_chunks", "retrieval_grade", "retrieval_retry_count", "evidence",
               "tech_brief", "trl_result", "market_result", "stakeholder_result", "domain_result", "synthesis",
               "judge_scores", "failed_perspectives", "judge_feedback", "perspective_retry_count", "report_markdown",
               "references", "report_retry_count", "report_pdf_path", "warnings", "audit_log"]


def test_state_has_exactly_the_27_design_keys():
    assert len(STATE_KEYS) == 27
    assert list(STATE_KEYS) == DESIGN_KEYS


def test_reducers_only_on_three_keys():
    from typing import get_type_hints

    hints = get_type_hints(State, include_extras=True)
    reduced = [k for k, v in hints.items() if getattr(v, "__metadata__", None)]
    assert sorted(reduced) == ["audit_log", "evidence", "warnings"]


def test_merge_by_id_unions_perspectives_and_keeps_first(mkweb):
    a = mkweb("1", "a.com").model_copy(update={"perspectives": ["market"], "claim": "first"})
    b = mkweb("1", "a.com", stance="con").model_copy(update={"perspectives": ["domain"], "claim": "second"})
    c = mkweb("0", "b.com")
    out = merge_by_id([a], [b, c])
    assert [e.evidence_id for e in out] == ["W:0", "W:1"]          # sorted by id
    merged = out[1]
    assert merged.claim == "first"
    assert merged.perspectives == ["domain", "market"]
    assert merged.stances == ["con", "pro"]


def test_add_unique():
    assert add_unique(["a"], ["a", "b"]) == ["a", "b"]


def test_origin_group_republished_press_release_and_syndicated_title(mkweb):
    x = mkweb("1", "tomshardware.com", title="SK hynix unveils ITME CXL memory for AI inference")
    y = mkweb("2", "techpowerup.com", title="SK hynix unveils ITME CXL memory for AI inference")   # same title
    z = mkweb("3", "blocksandfiles.com")
    out = {e.evidence_id: e for e in assign_origin_groups([x, y, z], {"W:3": "skhynix"})}
    assert out["W:1"].origin_group == out["W:2"].origin_group == "tomshardware.com"
    assert out["W:3"].origin_group == "skhynix"


def test_url_classification_and_ids():
    assert classify_url("https://research.google/blog/turboquant") == ("google", "vendor")
    assert classify_url("https://blog.google/x") == ("google", "vendor")
    assert classify_url("https://news.skhynix.co.kr/a") == ("skhynix", "vendor")
    assert classify_url("https://arxiv.org/abs/1") == ("arxiv.org", "academic")
    assert registered_domain("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert web_id("https://a.com/x?utm=1") == web_id("https://a.com/x")
    assert web_id("https://a.com/x").startswith("W:")


def test_origin_share_cap(mkweb):
    evs = [mkweb(str(i), "vendor.com") for i in range(4)] + [mkweb("9", "other.com")]
    assert max_origin_share(evs) == 0.8
    capped = cap_origin_share(evs)
    assert max_origin_share(capped) <= 0.5
