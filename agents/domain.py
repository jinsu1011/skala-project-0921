"""Domain agent (domain_evaluator): datacenter/cloud long-context LLM serving only.
W1 long-context batch and W2 high-concurrency multi-turn, 50% each; per workload cost/latency/throughput/
accuracy/integration 20% each (C.5). The W1-W2 gap feeds H3."""
from __future__ import annotations

from agents.perspective import CriterionSpec, PerspectiveSpec, run_perspective
from graph.state import PerspectiveResult

ITEMS = [("cost", "비용"), ("latency", "지연"), ("throughput", "처리량·동시성"), ("accuracy", "정확도 영향"),
         ("integration", "통합 난이도")]

SPEC = PerspectiveSpec(
    key="domain", name_ko="도메인 적합성",
    criteria=[CriterionSpec(f"{w}:{k}", f"{w} {n}", 10, workload=w) for w in ("W1", "W2") for k, n in ITEMS],
    rounds=[
        [("pro", "{sn} long context inference throughput latency results"),
         ("pro", "{sn} multi-turn serving concurrency memory capacity"),
         ("con", "{sn} accuracy degradation latency overhead integration"),
         ("neutral", "{sn} used together with KV cache compression or memory offloading in serving systems")],
        [("con", "{sn} criticism skeptical accuracy loss"),
         ("con", "{sn} limitations production serving challenges"),
         ("pro", "{sn} benchmark serving cost reduction")],
        [("con", "{sn} drawbacks hardware requirements compatibility"),
         ("neutral", "{sn} vLLM serving evaluation")],
    ],
    paper_query="{name}의 지연·처리량·정확도 실험 결과와 실험 조건(모델, 문맥 길이, 하드웨어)",
    extra_prompt=("도메인은 데이터센터·클라우드 장문맥 LLM 서빙 하나이다. W1은 요청 하나가 32K~128K 토큰 이상인 장문맥 배치 추론, "
                  "W2는 여러 사용자가 대화를 이어가며 앞 대화의 KV를 다시 쓰는 고동시성 다중 턴 서빙이다. "
                  "기준 키는 cost, latency, throughput, accuracy, integration 다섯 개이고, 근거가 어느 워크로드에 해당하는지 "
                  "workload에 \"W1\", \"W2\", \"both\", \"none\" 중 하나로 적는다."),
    extra_fields=', "workload": "W1"',
    score_extra="- 기준 이름은 \"W1:cost\"처럼 워크로드와 기준을 합친 키이다. W1과 W2를 따로 채점한다.",
)


def domain_evaluator(state: dict) -> dict:
    by_tech, evidence, events, warns = run_perspective(SPEC, state)
    return {"domain_result": PerspectiveResult(perspective="domain", by_tech=by_tech), "evidence": evidence,
            "audit_log": events, "warnings": warns}
