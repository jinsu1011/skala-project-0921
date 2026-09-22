# 채점 기준 자체 점검

## Phase 1–3: 설계 산출물 (2026-09-21)

| 항목 | 배점 | 충족 근거 (설계서 절) | 남은 위험 |
|---|---|---|---|
| 문제 정의 | 5 | C.1: 도메인(데이터센터 장문맥·고동시성 서빙), 의사결정 상황, 분석 질문 Q1–Q3, 독자, KV 크기 계산 예시(A.1) | — |
| Agent 설계 | 15 | D.1: Notion 6개 + Judge, 구현 노드·입출력 State·책임 경계 표, 보조 노드를 따로 구분, DECISIONS D3·D4 | Judge 추가 근거를 발표에서 설명해야 함 |
| RAG 설계 | 15 | B.2–B.5: 적용 에이전트와 사유, 6편 136p 코퍼스와 역할 메타데이터, 파이프라인 구현 완료, 하이브리드·재작성·품질 루프 | — |
| Embedding 선택 | 10 | B.6: 후보 4종 자체 교차언어 평가(42문항, 복수 정답, 26% 사람 검수), 정확도·균형·길이·비용·라이선스 기준표, 초안 가정(Qwen3)을 측정 결과로 교체 | 평가셋 규모 한계(4장에 명시) |
| 평가 기준 설계 | 15 | C: 4관점 × 대상·기준·증거 유형·출력 형식, TRL 증거 사다리, 척도·상충 규칙, 가설 H1–H4, 편향 방지 | — |
| State Schema | 15 | D.2: 27개 키 × 타입·작성·읽기·갱신, reducer 명시, 객체 스키마 | 구현 때 1:1 유지 필요 |
| Graph 설계 | 15 | D.3–D.5: Loop(검색·관점 내부·보고서), Branch(Judge·검수, 재선정 분기 없음), Fan-out/Fan-in(`defer`), `retry_router` + `Command(goto)` 선택적 재실행(v1.4에서 `Send` 대신 채택), 종료 보장 | 구현 후 대조 완료(`docs/graph.png`, Phase 4 표) |
| 보고서 구조 | 10 | E: Notion 목차 순서, 장별 원천 State 매핑, SUMMARY ½p·REFERENCE 규칙 | — |

## v1.1 개정 점검 (2026-09-21, 자동 대조 스크립트 결과 포함)

| 점검 항목 | 결과 |
|---|---|
| 인용한 원문 문장이 실제 설계서와 글자 단위로 일치 | 통과. "bge-m3는 전체 Hit@1·3·5와 MRR이 모두 가장 높았다." 등을 v1.0 원문과 대조 |
| 모든 관점이 상충 판정용 점수 규칙을 가짐 | 통과. 시장·이해관계자·도메인 환산 규칙(C.5) |
| TRL이 인식 점수와 섞이지 않음 | 통과. 상충 판정에서 분리, H1 전용 사전 규칙 |
| 루프 4개(공통 검색·관점 내부·관점 재실행·보고서) 모두 횟수 한도 | 통과. 2 / 2 / 2 / 1 (vC.1: 2안만 반영해 재선정 루프 삭제) |
| Judge 판정 로직에 결정적 검사 포함 | 통과. 판정식 명시, 합계 행 삭제 |
| 평가셋 숫자(60 → 57 → 50 → 42, 29, 11)의 기준 집합·단위 명시 | 통과 |
| reducer 아닌 키의 단일 작성자 | 통과. 다중 작성 키 0개(자동 대조) |
| State 표 노드명 = 그래프 노드명 | 통과. 27개 키의 작성 노드가 모두 그림 2a·2b에 있음(v1.2) |
| SUMMARY(요약) ½페이지 이내 | 통과. 11.3cm(본문 영역 절반 약 12.4cm) |
| 그림 100% 배율 가독성 | 2a 약 7.7pt 통과, 2b 약 6.5pt(작지만 판독 가능) |

| (v1.2) 비교 공정성 = 적용 시점 | 통과 |
| (v1.2) 수치를 개발 지표로 표기, 구현 조건 v2 재측정 | 통과. 순위 불변, 채택 구성 동일(하한값) |
| (v1.2) 임계값 heuristic 명시·민감도 | 통과(민감도 결과는 보고서에서) |
| (v1.2) Agent/Node 정의 | 통과. 에이전트 7, 노드 18 |
| (v1.2) 원 출처 계열(origin_group) | 통과 |

| (v1.3) 문서 순서가 Notion A→B→C→D→E와 일치, 부록 없음 | 통과. 요약 → A~E → 참고자료. mermaid 소스는 D.3 본문 |
| (v1.3) E에 실제 목차(장·절) 명시 | 통과. E.1 목차, E.2 원천 State, E.3 작성 규칙 |
| (v1.3) 그래프 한 페이지 수록·가독성 | 통과. 그림 2a 한 페이지(인쇄 약 10pt), 2b 약 8pt |

| (v1.4) 모든 루프에 통과·한도 소진 두 출구 | 통과. `final_check` 한도 소진 시 결정적 후처리 후 PDF |
| (v1.4) 선택적 재실행이 `Command(goto=[노드명])` | 통과. `Send` 미사용, 구현 형태 D.4 |
| (v1.4) 최악 superstep·recursion_limit | 통과. 약 30 / 50, LangGraph 1.2.x 고정 |
| (v1.4) 도메인 1개(과제 안내) | 통과. 데이터센터 장문맥 서빙, W1·W2는 평가 조건 |
| (v1.4) 에이전트 채점 Rubric | 통과. C.6 점수 앵커·기준별 신호·사람 표본 점검 |
| (v1.4) H1 격자·TRL 검사·개발사 발언 제외 | 통과. 상세는 `docs/archive/REVISION_v1.4.md` 2절 |

## Phase 4: 개발 결과와 설계 일치 점검 (2026-09-22)

자동 검사는 `uv run pytest -q`(28개, API 호출 없음)로 재현한다.

| 항목 | 설계 기준 | 구현 근거 | 검증 | 결과 |
|---|---|---|---|---|
| 노드 18개 | D.1·그림 2 | `graph/workflow.py` `build_graph()`에 노드 18개 등록, 재선정 분기 없음 | `tests/test_graph.py::test_graph_has_the_18_design_nodes_and_limit`, `docs/graph.png`(실제 컴파일 그래프) | 일치 |
| 그래프 순서 | D42 | `initialize → index_builder → selection_validator → query_planner` (`graph/workflow.py`) | `docs/graph.png` | 일치 |
| State 키 27개 | D.2 | `graph/state.py:227` `State`, 키 순서까지 표 D.2와 동일 | `test_state_has_exactly_the_27_design_keys` | 일치 |
| reducer 3개 | D.2 | `evidence: merge_by_id`(`graph/state.py:202`), `warnings: add_unique`(:218), `audit_log: operator.add`(:254) | `test_reducers_only_on_three_keys`, `test_merge_by_id_unions_perspectives_and_keeps_first` | 일치 |
| 공통 검색 루프 ≤2 | D.4-2 | `graph/platform.py:16` `MAX_RETRIEVAL`, `route_after_grade` | `test_query_rewrite_loop_success_and_exhaustion`, `test_common_retrieval_loop_limit` | 일치 |
| 관점 내부 루프 ≤2 | D.1 | `agents/perspective.py:23` `MAX_ROUNDS = 1 + 2`, 라운드별 `audit_log`에 기록 | 실행 로그 `rounds` 값(최대 3) | 일치 |
| 관점 재실행 ≤2, 실패 관점만 | D.4-5 | `graph/platform.py:161` `retry_router -> Command[Literal[...]]`, `Command(goto=[관점 노드])`, `Send` 미사용 | `test_only_failed_perspective_is_rerun`, `test_rerun_limit_exhausted_records_uncertain` | 일치 |
| 보고서 수정 ≤1, 한도 후 결정적 후처리 | D.4-7 | `graph/platform.py` `final_check`·`route_after_check`, `agents/report_writer.py:545` `postprocess` | `test_uncited_prose_is_flagged_and_removed` | 일치(D44: 후처리 시 `final_check`가 보고서 키를 씀) |
| synthesizer defer | D.4-4 | `graph/workflow.py:38` `defer=True` | `test_fan_out_fan_in_once`(1회), 재실행 시 2회 | 일치 |
| recursion_limit 50 | D.4-8 | `graph/workflow.py:18`, `app.py`의 `graph.stream(config=...)` | 테스트, 실제 실행 | 일치 |
| Judge 판정식 | D.5 | `agents/judge.py:70` `verdict` = `LLM_OK and COMMON and PER_PERSPECTIVE`, TRL은 `bound_origins` | `test_judge_verdict_pass_and_fail` | 일치 |
| 통과 관점 동결, 재실행 관점·종합 해설만 채점 | D.4-5, D38 | `agents/judge.py` `judge()`의 `prev[p].passed` 건너뛰기, 종합 문장 `unsupported_claim_ids` | `test_only_failed_perspective_is_rerun` | 일치 |
| 상충·H1·민감도 코드 계산 | C.5, D.4-6 | `agents/synthesis.py:24` `conflict_label`, `:47` `h1_cell`, `:69` `sensitivity` | `test_conflict_labels`, `test_h1_grid_and_verdict`, `test_sensitivity_counts_changed_cells` | 일치 |
| Rubric 점수 규칙 | C.6 | `agents/perspective.py:76` `rubric_score`(근거 조건), 가중치 50% 규칙 `weighted` | `test_rubric_anchors`, `test_missing_weight_over_half_is_held`, `docs/RUBRIC_CHECK.md` | 일치(D45) |
| 개발사 발언 점수 제외 | C.2 | `agents/stakeholder.py` `exclude=developer_groups`, 판정 시에도 제외 | `docs/RUBRIC_CHECK.md` 표본 | 일치 |
| 원 출처 계열·50% 규칙 | C.2 | `tools/evidence.py` `assign_origin_groups`, `cap_origin_share` | `test_origin_group_republished_press_release_and_syndicated_title`, `test_origin_share_cap` | 일치(D46) |
| 보고서 목차·규칙 | E.1·E.3 | `agents/report_writer.py` `build_markdown`, `check_report`(:525) | `test_summary_length_check`, `test_citation_reference_consistency`, `test_lexicon_detects_and_neutralizes`, PDF 페이지 이미지 확인 | 일치 |
| offline 재현 | B.8 | `graph/runtime.py` 응답 캐시(`data/cache/llm_agents.sqlite`), `tools/web_search.py` 캐시 | `test_offline_cache_replay`, 새 clone에서 `.env` 없이 `app.py --offline` 실행 | 일치 |

### 재현성·보안 검사 결과 (2026-09-22, 실제 실행)

| 항목 | 방법 | 결과 |
|---|---|---|
| 새 clone + `.env` 없이 offline | `git clone` → `uv sync` → `uv run python app.py --offline` (키 환경변수 제거) | 통과. 논문 6편 다운로드(136쪽) → 청크·FAISS 인덱스 새로 생성 → 노드 29회 실행 → PDF 생성. LLM 호출 0회(캐시 498건), 웹 호출 0회(캐시 171건). 보고서 Markdown이 커밋본과 동일 |
| 인덱스 삭제 후 재생성 | 위 clone에는 `data/index/`가 없음(gitignore) | 통과. `chunks.jsonl`, `bge-m3/` 인덱스 재생성 |
| 키 없을 때 자동 offline | clone에서 `--offline` 없이 실행 | 통과. "offline 재생(API 키 없음, 자동 전환)"으로 실행, LLM·웹 호출 0회 |
| 발견·수정한 결함 | 첫 clone 실행에서 논문 다운로드 스크립트의 `sys.exit()`가 그래프를 조용히 종료 | 수정(커밋 3dcd15a) 후 재검증 통과 |
| `.env` 미추적 | `git ls-files` | 추적 안 됨 |
| 논문 PDF 미추적 | `git ls-files data/papers` | 추적 안 됨 |
| 비밀키 패턴 | HANDOFF 52행 스캔 명령 + 실제 키 값 문자열 대조 | 실제 키 0건. 넓은 패턴의 적중은 모두 `sk-hynix-…` URL 문자열(오탐) |
| 공백 오류 | `git diff --check` | 통과(생성 보고서 끝 빈 줄 수정 후) |
| 커밋 작성자 | `git log` | jinsoo kim만, Co-Authored-By·AI 표기 0건 |
| 단위 테스트 | `uv run pytest -q` | 31 passed (API 호출 없음) |
