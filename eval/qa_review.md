# QA 평가셋 사람 검수 기록

- 대상: `eval/data/qa_eval_set.jsonl` (42문항: 논문별 7개, SW 21 / HW 21, 복수 정답 29문항)
- 검수 범위: **11쌍 (26%)**. 논문별로 층화해 뽑았다(`random.seed(7)`: TurboQuant 2, MLA 2, KIVI 1, InfiniGen 2, ITME 2, CXL-PNM 2).
- 검수 방법: 질문, 기준 답, 근거 문장(evidence)을 정답 청크 원문과 대조했다. 풀링 판정으로 추가된 복수 정답은 청크 원문을 직접 읽고 확인했다.
- 검수자: 김진수, 2026-09-21

| # | qid | 질문 (요약) | 정답 청크 대조 | 추가 정답 확인 | 판정 |
|---|---|---|---|---|---|
| 1 | q-turboquant-009 | QJL 분산 상한 | turboquant-009에 `Var ≤ π/(2d)·‖y‖²` 있음 | turboquant-014 ✅ / **turboquant-012 ❌** (MSE 왜곡 상한 √3π/2d·4⁻ᵇ이지 QJL 분산이 아님) | **수정: 012 제거** |
| 2 | q-turboquant-007 | 좌표 xj의 분포 | Beta 분포 수식 있음 | turboquant-005 ✅ (회전 후 좌표의 Beta 분포 언급) | 통과 |
| 3 | q-mla-013 | YaRN scale 값 | "scale s to 40" 있음 | 없음 | 통과 |
| 4 | q-mla-017 | KV cache 평균 양자화 비트 | "6 bits on average" 있음 | 없음 | 통과 |
| 5 | q-kivi-013 | KIVI-2 Llama2-13B QMSum 점수 | 표에 20.69 있음 | kivi-018 ✅ (부록 표에 같은 값 20.69) | 통과 |
| 6 | q-infinigen-002 | Transformer 블록 입력 텐서 차원 | "N × D" 정의 있음 | 없음 | 통과 (배경 지식형이라 분석 가치는 낮지만 정답은 유효) |
| 7 | q-infinigen-012 | 행렬 A의 성질 | 직교 행렬, 전치가 역행렬이라는 서술 있음 | infinigen-004, 013 ✅ (skewing의 직교 행렬 설명) | 통과 |
| 8 | q-itme-003 | T3.5 계층 구현 방식 | "SSD-backed capacity → direct-access memory expansion" 있음 | itme-002, 013, 015 ✅ | 통과 |
| 9 | q-itme-011 | KV 캐시 미스 처리 | "dynamic recomputation on the GPU" 있음 | 없음 | 통과 |
| 10 | q-cxl_pnm-001 | CXL 오프로딩 시 GPU 메모리 감소율 | "up to 87%" (Tang et al. 인용) 있음 | cxl_pnm-021 ✅ | 통과 |
| 11 | q-cxl_pnm-005 | CXL 메모리 모듈당 대역폭 | "1.1 TB/s per module" 있음 | cxl_pnm-015 ✅ | 통과 |

## 발견 사항과 조치

1. **풀링 판정 오판(1건):** `q-turboquant-009`의 추가 정답 `turboquant-012`는 비슷한 형태의 다른 상한식이었다. `eval/data/qa_manual_fixes.json`의 `remove_gold`로 제거하고 다시 채점했다.
2. **재작성 질의 오역(관찰):** `q-turboquant-009`의 한국어 "분산"이 영어 재작성에서 "distribution"으로 번역됐다(정답은 variance). 런타임 `query_rewriter`의 한계로 보고 그대로 두었다. 평가셋을 인위적으로 고치지 않고 실제 동작을 측정하기 위해서다. 이런 경우를 한국어 dense 순위가 보완하므로 3중 RRF를 채택했다.
3. **이전 버전에서 폐기한 것:** 1차 생성본은 복합 질문("어떤 방식 + 이점은?")이 많았다. 관대한 판정 때문에 문항당 추가 정답이 평균 6개였고 다른 논문 청크까지 정답 처리됐다. 그래서 전량 폐기하고 단일 사실 질문과 엄격 판정으로 다시 만들었다(추가 정답 298개 → 91개).
4. **누설 차단:** 초기 `query_en`은 정답 청크를 본 QC LLM이 만들어 BM25(EN) MRR이 0.816으로 부풀려져 있었다. 질문만 보는 생성 모델 재작성으로 바꿨다(0.770).
