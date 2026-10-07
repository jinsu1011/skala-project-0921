"""Run only the Orchestrator planner on different inputs and write docs/PLAN_VARIATIONS.md.

Shows that the SubTask plan is produced at runtime from the State (selected technologies, research gaps), not from a
fixed worker list. Each call is a real LLM call (cached in data/cache/, so the table is reproducible offline).

    uv run python scripts/plan_variations.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = [
    ("기본 조합", {"sw": "turboquant", "hw": "itme"}, {}),
    ("ITME 검색 공백(원리·수치 누락)", {"sw": "turboquant", "hw": "itme"}, {"itme": ["principle", "number_or_limit"]}),
    ("다른 기술 조합 1", {"sw": "kivi", "hw": "infinigen"}, {}),
    ("다른 기술 조합 2", {"sw": "mla", "hw": "cxl_pnm"}, {}),
]


def main() -> None:
    from graph.runtime import config, configure, has_keys
    from graph.state import RetrievalGrade, Technology

    configure(offline=not all(has_keys()))
    os.environ.setdefault("ORCH_DECISION_LOG", "0")
    from agents.orchestrator import orchestrator

    meta = config()["tech_meta"]
    rows = []
    for label, techs, gaps in CASES:
        state = {"run_id": "planvar", "selected_techs": [
            Technology(tech_id=t, name=meta[t]["name"], camp=meta[t]["camp"], developer=meta[t]["developer"],
                       developer_groups=meta[t].get("developer_groups", [])) for t in techs.values()],
            "evidence": [], "retrieval_grade": RetrievalGrade(sufficient=not gaps, missing=gaps)}
        out = orchestrator(state)
        plan = out["plan"]
        tasks = "<br>".join(f"{t.task_id} {t.perspective} → `{t.assigned_agent}` {t.tech_ids}" for t in out["subtasks"])
        rows.append(f"| {label} | {', '.join(meta[t]['name'] for t in techs.values())} | {plan.plan_source} | "
                    f"{len(out['subtasks'])} | {tasks} | {', '.join(out['unused_agents']) or '-'} | "
                    f"{'; '.join(plan.repairs) or '-'} |")
        print(label, len(out["subtasks"]), plan.plan_source)
    md = ["# Orchestrator 계획 변화 (입력별)", "",
          "같은 Orchestrator 프롬프트·코드에 입력(State)만 바꿔 계획 단계만 실행한 결과이다. 작업 수, 관점 분할, 추가 관점, "
          "배정 Agent가 입력에 따라 달라진다. temperature 0과 응답 캐시 때문에 **같은 입력이면 같은 계획**이 나온다(재현성). "
          "계획 단계만 실행했으므로 State에 기술 개요·근거가 없다. 전체 실행에서는 이 정보가 더해져 계획이 또 달라진다(아래 표). "
          "`uv run python scripts/plan_variations.py`로 다시 만든다.", "",
          "| 입력 | 기술 | 계획 출처 | SubTask 수 | SubTask (관점 → 담당 Agent, 기술) | 미사용 Agent | 코드 보정 |",
          "|---|---|---|---|---|---|---|", *rows, "",
          "## 전체 그래프 실행 비교 (LangSmith trace 있음)", "",
          "| 실행 | 기술 | SubTask | 미사용 Agent | Judge 실패 → 재시도 | 검토 | 품질 평가 |",
          "|---|---|---|---|---|---|---|",
          "| 최종 제출 `cdc64c3abebe` | TurboQuant, ITME | 5개 (4개 최소 관점 + 비용·구현 위험) | ecosystem, regulation "
          "| T01~T04 → 재시도 후 미달 | 8건 (작업당 2명) | 한계 기록 후 종료 |",
          "| 비교 `c3a0ce5a880f` | KIVI, InfiniGen | 6개 (4개 최소 관점 + 비용·구현 위험 + 규제) | ecosystem "
          "| T01~T04 → 재시도 후 미달 | 4건 (작업당 1명) | 통과 |", ""]
    (ROOT / "docs" / "PLAN_VARIATIONS.md").write_text("\n".join(md))


if __name__ == "__main__":
    main()
