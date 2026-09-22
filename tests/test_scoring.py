from agents.judge import verdict
from agents.perspective import rubric_score, weighted
from agents.synthesis import (conflict_label, conflicts_for, h1_cell, h1_verdict, sensitivity)
from graph.state import Criterion, JudgeChecks, JudgeScore


def test_rubric_anchors():
    assert rubric_score({"a", "b"}, set(), set()) == 5
    assert rubric_score({"a", "b"}, {"c"}, set()) == 4
    assert rubric_score({"a"}, {"c"}, set()) == 3
    assert rubric_score({"a"}, {"b", "c"}, set()) == 2
    assert rubric_score(set(), {"b", "c"}, set()) == 1
    assert rubric_score({"a"}, set(), set()) is None          # <= 1 family -> 판단 보류
    assert rubric_score({"a"}, {"a"}, set()) is None          # same family counted once


def test_missing_weight_over_half_is_held():
    c = [Criterion(name="a", weight=30, score_1to5=4), Criterion(name="b", weight=30, score_1to5=None),
         Criterion(name="c", weight=40, score_1to5=None)]
    assert weighted(c) is None
    c[1].score_1to5 = 2
    assert weighted(c) == 3.0


def test_conflict_labels():
    assert conflict_label(2.0) == "상충"
    assert conflict_label(1.99) == "부분 상충"
    assert conflict_label(1.0) == "부분 상충"
    assert conflict_label(0.99) == "일치"
    cs = conflicts_for({"t": {"market": 4.5, "stakeholder": 2.0, "domain": None}})
    assert [(c.pair, c.label) for c in cs] == [(("market", "stakeholder"), "상충")]   # 판단 보류 skipped


def test_h1_grid_and_verdict():
    assert h1_cell(4.5, 3.0) == "일치"
    assert h1_cell(2.5, 4.2) == "괴리 (기대 선행)"
    assert h1_cell(4.5, 4.2) == "부분 괴리 (기대 선행)"
    assert h1_cell(7.0, 1.5) == "괴리 (채택 신호 지연)"
    assert h1_cell(None, 3.0) == "판단 보류"
    assert h1_verdict({"a": "일치", "b": "괴리 (기대 선행)"}) == "지지"
    assert h1_verdict({"a": "일치", "b": "부분 괴리 (기대 선행)"}) == "부분 지지"
    assert h1_verdict({"a": "일치", "b": "일치"}) == "기각"
    assert h1_verdict({"a": "판단 보류", "b": "판단 보류"}) == "판단 보류"


def test_sensitivity_counts_changed_cells():
    scores = {"t": {"market": 4.0, "stakeholder": 2.2, "domain": 3.0}}   # gaps 1.8, 1.0, 0.8
    s = sensitivity(scores, {"t": 4.5}, {"t": 4.0})
    by = {r["variant"]: r["changed"] for r in s["conflict"]}
    assert by["상충 기준 2.0→1.5"] == 1          # 1.8 becomes 상충
    assert by["부분 상충 기준 1.0→1.5"] == 1      # 1.0 becomes 일치
    assert by["부분 상충 기준 1.0→0.5"] == 1      # 0.8 becomes 부분 상충
    h1 = {r["variant"]: r["changed"] for r in s["h1"]}
    assert h1["시장 경계 4.0→4.5"] == 1            # market 4.0 moves to the middle band


def _score(p, llm=4, share=0.4, pro=2, con=2, bound=1, lex=0):
    return JudgeScore(grounding=llm, neutrality=4, source_diversity=4, completeness=4,
                      checks=JudgeChecks(max_origin_share=share, pro_origins={"a": pro, "b": 2},
                                         con_origins={"a": con, "b": 2}, bound_origins={"a": bound, "b": 1},
                                         lexicon_hits=lex))


def test_judge_verdict_pass_and_fail():
    assert verdict("market", _score("market"))
    assert not verdict("market", _score("market", llm=3))
    assert not verdict("market", _score("market", share=0.51))
    assert not verdict("stakeholder", _score("stakeholder", con=1))
    assert not verdict("domain", _score("domain", lex=1))
    assert verdict("trl", _score("trl", pro=0, con=0))             # TRL uses bound_origins, not pro/con
    assert not verdict("trl", _score("trl", bound=0))


def test_neutral_only_families_score_three():
    # C.6 row 3: facts without evaluation ("중립 서술 위주") from >= 2 families score 3, not 판단 보류
    assert rubric_score(set(), set(), {"a", "b"}) == 3
    assert rubric_score(set(), set(), {"a"}) is None


def test_judge_reads_the_perspectives_own_claim(techs, mkweb):
    import json
    from agents.judge import _payload
    from graph.state import PerspectiveResult, TechAssessment
    shared = mkweb("1", "a.com").model_copy(update={"claim": "market reading"})
    ta = TechAssessment(pro_ids=["W:1"], claims={"W:1": "domain reading"})
    body = json.loads(_payload("domain", PerspectiveResult(perspective="domain", by_tech={"turboquant": ta}),
                               {"W:1": shared}, techs))
    assert body["by_tech"]["TurboQuant"]["evidence"][0]["claim"] == "domain reading"
