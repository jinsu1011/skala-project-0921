"""Build docs/RUBRIC_CHECK.md from outputs/state_snapshot.json: for each perspective and technology, two sampled
criteria are re-checked against C.6 (family counts recomputed from the cited evidence, expected Rubric score,
the LLM's own Rubric score, stance labels). Sampling rule is fixed in advance: the two criteria with the largest
weight (ties: design order); TRL is checked on its lower and upper bound."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.perspective import rubric_score  # noqa: E402
from agents.stakeholder import _group_score  # noqa: E402

NAMES = {"turboquant": "TurboQuant", "itme": "ITME"}
DEV = {"turboquant": "google", "itme": "skhynix"}
KO = {"size_growth": "시장 규모·성장", "adoption": "상용화·채택", "ecosystem": "생태계 지원", "cost": "도입 비용 구조",
      "group_a": "(a) 클라우드·데이터센터", "group_b": "(b) GPU·메모리 벤더", "group_c": "(c) 개발자", "group_d": "(d) 투자·분석·언론"}


def fmt(x):
    return "보류" if x is None else f"{x:g}"


def main():
    snap = json.loads((ROOT / "outputs" / "state_snapshot.json").read_text())
    ev = {e["evidence_id"]: e for e in snap["evidence"]}
    rows = []
    for p in ("market", "stakeholder", "domain"):
        for t, ta in snap[f"{p}_result"]["by_tech"].items():
            crit = sorted(ta["criteria"], key=lambda c: -c["weight"])
            if p == "domain":   # one per workload so both W1 and W2 are covered
                crit = [c for c in ta["criteria"] if c["name"] == "W1:accuracy"] + \
                       [c for c in ta["criteria"] if c["name"] == "W2:latency"]
            for c in crit[:2]:
                excl = {DEV[t]} if p == "stakeholder" else set()
                pro = {ev[i]["origin_group"] for i in c["pro_ids"] if i in ev and ev[i]["origin_group"] not in excl}
                con = {ev[i]["origin_group"] for i in c["con_ids"] if i in ev and ev[i]["origin_group"] not in excl}
                neu = {ev[i]["origin_group"] for i in c["evidence_ids"] if i in ev and i not in c["pro_ids"]
                       and i not in c["con_ids"] and ev[i]["origin_group"] not in excl}
                if p == "stakeholder":
                    exp = _group_score({"pro": pro, "con": con, "neutral": neu})
                else:
                    exp = rubric_score(pro, con, neu)
                dev_in = [i for i in c["evidence_ids"] if i in ev and ev[i]["origin_group"] == DEV[t]]
                ok = exp == c["score_1to5"] and (p != "stakeholder" or not dev_in)
                rows.append([
                    {"market": "시장성", "stakeholder": "이해관계자", "domain": "도메인"}[p], NAMES[t],
                    KO.get(c["name"], c["name"]), f"{len(pro)} / {len(con)} / {len(neu)}", fmt(c["score_1to5"]),
                    fmt(exp), fmt(c.get("llm_score")),
                    ("개발사 근거 없음" if p == "stakeholder" and not dev_in else
                     "개발사 근거 포함" if dev_in and p == "stakeholder" else
                     f"찬 {len(c['pro_ids'])}·반 {len(c['con_ids'])}건 표시"),
                    "일치" if ok else "불일치"])
    trl_rows = []
    for t, ta in snap["trl_result"]["by_tech"].items():
        low = {ev[i]["origin_group"] for i in ta["low_ids"] if i in ev}
        high = {ev[i]["origin_group"] for i in ta["high_ids"] if i in ev}
        indep = bool(low - {DEV[t]}), bool(high - {DEV[t]})
        exp = "high" if all(indep) else ("mid" if any(indep) else "low")
        trl_rows.append([NAMES[t], f"{ta['trl_low']}–{ta['trl_high']}", f"{len(low)} ({', '.join(sorted(low))})",
                         f"{len(high)} ({', '.join(sorted(high))})", ta["confidence"], exp,
                         "일치" if exp == ta["confidence"] else "불일치"])
    agree = sum(1 for r in rows if r[4] == r[6])
    out = ["# 채점 Rubric 표본 점검 (C.6)", "",
           "설계서 C.6에 따라 관점마다 기술별 기준 2개를 골라, 점수·근거 계열 수·입장 표시가 Rubric대로인지 확인했다. "
           "표본은 결과를 보기 전에 정한 규칙으로 골랐다(시장성·이해관계자: 가중치가 큰 기준 2개, 도메인: W1 정확도·W2 지연, "
           "TRL: 하한·상한). 원천은 `outputs/state_snapshot.json`이고 `uv run python scripts/rubric_check.py`로 다시 만든다.", "",
           "- 계열 수: 인용 근거의 `origin_group`을 다시 세어 긍정 / 부정 / 중립 계열 수로 적었다(같은 원 출처 계열은 1로 셈).",
           "- 기대 점수: 다시 센 계열 수를 C.6 근거 조건(이해관계자는 집단 다수 입장 규칙)에 넣어 계산한 값이다.",
           "- LLM 점수: 관점 에이전트가 Rubric 원문을 보고 직접 매긴 점수이다. 보고서에는 코드 점수(C.6 근거 조건)를 쓴다(D45).",
           "- 입장 표시: 이해관계자는 개발사 근거가 점수에 들어가지 않았는지, 나머지는 찬반 표시 건수를 확인했다.", "",
           "## 시장성·이해관계자·도메인", "",
           "| 관점 | 기술 | 기준 | 계열 수(긍정/부정/중립) | 보고서 점수 | 기대 점수 | LLM 점수 | 입장 표시 | 판정 |",
           "|---|---|---|---|---|---|---|---|---|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    out += ["", f"코드 점수와 기대 점수는 {sum(1 for r in rows if r[-1] == '일치')}/{len(rows)}건 일치했다. "
                f"LLM이 직접 매긴 점수는 코드 점수와 {agree}/{len(rows)}건 같았다. LLM 점수가 다른 경우는 대부분 근거가 "
                "1계열 이하라 코드가 판단 보류로 둔 기준에 LLM이 점수를 준 경우로, C.6의 판단 보류 규칙을 코드로 강제한 이유이다.",
            "", "## TRL (하한·상한 근거와 신뢰도, C.4)", "",
            "| 기술 | 범위 | 하한 근거 계열 | 상한 근거 계열 | 보고서 신뢰도 | 규칙상 신뢰도 | 판정 |", "|---|---|---|---|---|---|---|"]
    out += ["| " + " | ".join(r) + " |" for r in trl_rows]
    out += ["", "규칙: 하한·상한 모두 개발사 외 독립 근거가 있으면 high, 한쪽만 있으면 mid, 개발사 자료뿐이면 low.", "",
            "## 사람 점검 메모", "",
            "- 위 표는 코드로 다시 센 자동 점검이다. 표본 근거의 원문(URL·논문 쪽)을 열어 긍정·부정 표시가 C.6 신호 열과 맞는지는 "
            "팀원이 직접 확인하고 아래에 점검자와 결과를 적는다. <!-- TODO: 팀 점검 필요 -->",
            "- 점검자: (기입) / 확인한 표본 수: (기입) / 표시가 C.6과 다른 근거: (기입)",
            "- ITME는 공개 후 기간이 짧아 기술 고유 근거가 적고, 대부분의 기준이 판단 보류로 남았다. 이는 근거 부족을 낮은 점수로 "
            "바꾸지 않는다는 C.5 규칙대로 처리된 결과이다.", ""]
    (ROOT / "docs" / "RUBRIC_CHECK.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
