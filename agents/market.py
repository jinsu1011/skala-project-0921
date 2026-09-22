"""Market agent (market_evaluator): size/growth 25, adoption 30, ecosystem 30, cost structure 15 (C.5).
Papers are used only to check public claims against the original experimental conditions (B.2)."""
from __future__ import annotations

from agents.perspective import CriterionSpec, PerspectiveSpec, run_perspective
from graph.state import PerspectiveResult

SPEC = PerspectiveSpec(
    key="market", name_ko="시장성",
    criteria=[CriterionSpec("size_growth", "시장 규모·성장", 25), CriterionSpec("adoption", "상용화·채택", 30),
              CriterionSpec("ecosystem", "생태계 지원", 30), CriterionSpec("cost", "도입 비용 구조", 15)],
    rounds=[
        [("pro", "{sn} adoption deployment industry demand"),
         ("pro", "{sn} integration ecosystem support framework"),
         ("con", "{sn} limitations adoption barriers cost concerns")],
        [("pro", "{sn} customers partners production use"),
         ("con", "{sn} not production ready research stage criticism")],
        [("neutral", "{sn} market analysis outlook"),
         ("con", "{sn} infrastructure cost overhead drawbacks")],
    ],
    paper_query="{name} 논문의 실험 환경과 기준선, 보고된 성능 수치",
    extra_prompt="논문 근거는 발표 문구의 성능 수치를 원래 실험 조건과 대조하는 데만 쓴다(condition_mismatch 표시). "
                 "시장 규모·성장 기준은 해당 시장(추론 서빙 최적화 SW 또는 CXL 메모리 확장 HW)의 수요 언급으로 판단한다.",
)


def market_evaluator(state: dict) -> dict:
    by_tech, evidence, events, warns = run_perspective(SPEC, state)
    return {"market_result": PerspectiveResult(perspective="market", by_tech=by_tech), "evidence": evidence,
            "audit_log": events, "warnings": warns}
