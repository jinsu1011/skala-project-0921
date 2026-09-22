# Subject
본 프로젝트는 KV cache 최적화 기술을 소프트웨어, 하드웨어 두 진영에서 선정하여,
기술 성숙도(TRL)·시장·이해관계자·도메인 관점에서 평가하는 Agentic RAG를 개발하는 프로젝트 임.

## Overview
- Objective : 하나의 기술을 복수 관점에서 비교 평가 (우열·추천 판정 없이, 관점별 인식 차이와 그 근거·조건을 추적)
- Method : Multi-Agent(Distributed) + Agentic RAG
- Tools : LangGraph, LangChain, FAISS, BM25(rank-bm25), sentence-transformers, Tavily, PyMuPDF, LangSmith, uv
- 결과물 : 평가 보고서 `deliverables/RAG-Output_판교_9반_김정인+김지수+김진수+전진만+정원준.pdf` (그래프 실행으로 자동 생성)

## Directory Structure
```
├── app.py                 # 실행 스크립트 (CLI 옵션, API 키 없으면 offline 자동 전환)
├── config.yaml            # 선정 기술·기술 메타데이터(별칭·개발사)·LLM·코퍼스·검색·그래프 한도·팀 정보
├── agents/                # Agent 모듈
│   ├── tech_research.py   #   기술 조사 (selection_validator, tech_research, trl_assessor)
│   ├── market.py          #   시장 평가 (market_evaluator)
│   ├── stakeholder.py     #   이해관계자 평가 (stakeholder_evaluator)
│   ├── domain.py          #   도메인 평가 (domain_evaluator)
│   ├── perspective.py     #   관점 에이전트 공통 엔진(찬반 검색·근거 표시·Rubric 채점)
│   ├── synthesis.py       #   평가 종합 (synthesizer: 상충·H1·H3·민감도 코드 계산)
│   ├── judge.py           #   Judge (D.5 판정식)
│   └── report_writer.py   #   보고서 생성·검수 규칙
├── graph/                 # LangGraph
│   ├── state.py           #   State 27키·모델·reducer
│   ├── workflow.py        #   그래프(노드 18개)
│   ├── platform.py        #   보조 노드(initialize, index_builder, retry_router, final_check, pdf_renderer 등)
│   └── runtime.py         #   LLM 응답 캐시·offline 재생·우열 어휘 사전
├── tools/                 # @tool: paper_retrieve, web_search, summarize_sources, 출처 계열(evidence.py)
├── rag/                   # PDF 로딩·청킹·임베딩·하이브리드 검색(HybridRetriever)
├── prompts/               # 프롬프트 템플릿(C.6 Rubric·C.4 TRL 규칙 원문 포함)
├── tests/                 # 단위 테스트(mock·fixture, API 호출 없음)
├── eval/                  # 임베딩·검색 구성 평가(42문항 평가셋)
├── report/                # SKALA 양식 PDF 빌더, Mermaid 로컬 렌더러
├── scripts/               # 논문 다운로드, 그래프 이미지 렌더링, Rubric 표본 점검
├── data/                  # LLM·웹 검색 캐시(커밋), 논문 PDF·인덱스(실행 시 재생성, 커밋 안 함)
├── docs/                  # 설계 원문(DESIGN)·결정 기록(DECISIONS)·그래프·자체 점검·Rubric 점검·발표 노트·발표 대본
│   └── archive/           #   설계 개정 이력(v1.0~v1.4)·작업 지시서 보관
├── outputs/               # 평가 보고서 Markdown·docx·PDF, 실행 결과 스냅샷, 임베딩 평가 결과
├── deliverables/          # 설계서·평가 보고서 PDF (실행 시 자동 복사)
├── submission/            # 최종 제출 파일 모음(보고서·설계서 PDF, Git 링크, 제출 안내·팀원 설명, 발표 노트)
├── pyproject.toml / uv.lock
└── README.md
```

## Architecture
![전체 그래프(노드 18개, 4단계). 실선은 항상 지나는 경로, 점선은 조건에 따라 갈리는 경로, 마름모는 다음 경로를 정하는 노드](docs/graph_overview.png)

- 흐름 : Human 기술 선정(2안) → ① 선정 검증 및 RAG 준비 → ② 기술 조사 및 관점별 병렬 평가 → ③ Judge 검증 및 선택적 재실행 → ④ 보고서 생성·검수 → END
- 규모 : 에이전트 7개(가이드 6 + Judge), 노드 18개, State 키 27개, 루프 4개(공통 검색 2 · 관점 내부 2 · 관점 재실행 2 · 보고서 수정 1, `recursion_limit` 50)
- 구현 확인 : 실제 코드에서 컴파일한 그래프를 `draw_mermaid()`로 그린 [`docs/graph.png`](docs/graph.png)가 위 설계 그림과 같은 구조임을 확인했다(`scripts/render_graph.py`)
- 아래 절은 이 그림의 순서(Human 기술 선정 → ① → ② → ③ → ④)를 따른다

## Selected Technologies (그림의 "Human 기술 선정")
- SW : **Google TurboQuant** — 재학습 없이 서빙 단계에서 KV cache를 온라인 양자화(data-oblivious). 무작위 회전 + 좌표별 scalar quantization, 잔차 1-bit QJL로 내적 추정 편향을 보정
- HW : **ITME (SK hynix)** — KV를 양자화하지 않고 CXL-hybrid 메모리(TB급, byte-addressable)로 저장 공간을 확장. vLLM 위에 구현, 양산급 CMM·FPGA 프로토타입으로 실측
- 공정 비교 근거 : 두 기술은 **같은 KV cache 용량 병목을, 같은 서빙 시점에서, 서로 다른 시스템 계층**(데이터 표현 vs 메모리 계층)으로 해결함. 후보 6개(Doc Pool)를 가중 기준표로 채점(TurboQuant 4.35, ITME 4.50)해 조가 직접 선정(2안)하고, 에이전트(`selection_validator`)는 원문 근거로 사후 검증만 수행

## Tech Stack
- Framework : LangGraph (1.2.x), LangChain
- LLM/Generator : gpt-4.1-mini (temperature 0, seed 42)
- LLM/Judge : gpt-4.1 (생성 모델과 다른 상위 모델, 같은 계열이라는 한계는 보고서에 명시)
- Retrieval : FAISS(IndexFlatIP) + BM25 3중 RRF(k=60) + bge-reranker-v2-m3 — Hit@1 0.786 · Hit@5 0.976 · MRR@10 0.863 (개발 지표: 구성 선택용 한→영 42문항, 구현 조건 입력 길이 2,048)
- Embedding : BAAI/bge-m3 (후보 4종 자체 교차언어 평가로 선정, min(SW, HW) MRR 기준 진영 간 균형 확인)
- Web Search : Tavily (결과를 날짜와 함께 `data/web_cache/`에 저장)
- Report : SKALA 공식 docx 양식(`report/docx_builder.py`) + LibreOffice PDF 변환, Mermaid 로컬 렌더링

## Agents
- 기술 조사 Agent : 선정 타당성 사후 검증(`selection_validator`), 원문에서 개요·실험 조건·한계 추출(`tech_research`), TRL 범위 추정(`trl_assessor`) — RAG (+Web)
- 시장 평가 Agent : 시장 규모·성장, 상용화·채택 사례, 생태계 지원, 도입 비용 구조 평가(`market_evaluator`) — RAG + Web
- 이해관계자 평가 Agent : 클라우드·데이터센터, GPU·메모리 벤더, 개발자 커뮤니티, 투자·분석·언론 집단별 입장(`stakeholder_evaluator`) — Web
- 도메인 평가 Agent : 데이터센터 장문맥 서빙의 W1(장문맥 배치)·W2(고동시성 다중 턴) 적합 조건·제약(`domain_evaluator`) — RAG + Web
- 평가 종합 Agent : 관점 간 일치·상충 매트릭스와 H1~H4 판정(상충·H1·민감도는 코드 계산, LLM은 해설)(`synthesizer`, defer)
- Judge Agent : 관점별 근거성·중립성·출처 다양성·완결성 채점 + 규칙 검사, 미달 관점 지정(`judge`)
- 보고서 생성 Agent : 목차대로 본문 작성, 인용·REFERENCE 정리(`report_writer`)

## Features (그림 ①~④ 단계별 동작과 선택 이유)

- **LangGraph Multi-Agent + Agentic RAG** : 에이전트 7개(가이드 6 + Judge)를 노드 18개로 구현하고, 검색 품질 판정·재질의·재실행을 그래프 루프로 둔다
  - 선택 이유 : 관점마다 판단 책임을 분리해야 한 관점의 결론이 다른 관점에 섞이지 않는다. 검색이 부족할 때 스스로 질의를 고쳐 다시 찾는 흐름(Agentic RAG)이 있어야 근거 부족과 검색 실패를 구분할 수 있다

### ① 선정 검증 및 RAG 준비
`initialize` → `index_builder` → `selection_validator` → `query_planner` → `hybrid_retriever` ⇄ `retrieval_grader` / `query_rewriter`
- `index_builder` : 논문 6편(136쪽)을 읽어 절 단위 청크 159개로 나누고 bge-m3 FAISS 인덱스와 BM25 인덱스를 만든다. 문서가 바뀌지 않았으면 다시 쓴다
- `selection_validator` : 조가 고른 두 기술이 ① 같은 문제(KV cache 용량) ② 같은 적용 시점(서빙 단계) ③ 공개 근거 충분성을 만족하는지 원 논문으로 확인하고 기록만 한다(재선정 분기 없음)
- `query_planner` → `hybrid_retriever` : 기술마다 원리·실험 조건·한계 질의를 만들고, 한국어 질의를 영어로 바꿔 3중 RRF + reranker로 찾는다
- `retrieval_grader` : 결과에 기술명·원리·수치나 한계가 빠졌으면 `query_rewriter`가 빠진 요소를 넣어 다시 찾는다(최대 2회, 한도 후에는 경고를 남기고 진행)

- **기술 선정 2안(Human) + 사후 검증** : 조가 후보 6개를 같은 기준표로 채점해 선정하고, `selection_validator`는 원문 근거로 검증만 한다(재선정 분기 없음)
  - 선택 이유 : 에이전트가 검색 결과로 대상을 고르면 최근 자료에 치우치거나 비교 계층이 어긋날 수 있다(가이드 권장안, 설계 B.1)
- **PDF 자료 기반 정보 추출** : 논문 6편(136p, 한도 200p)을 절 인식 청크 159개로 인덱싱하고 `tech`·`role`(1차 근거·기준선·대안) 메타데이터로 걸러 검색
  - 선택 이유 : 선정 기술 논문 2편만 넣으면 기준선(KIVI)·대안(InfiniGen 등)과 비교할 근거가 없다(설계 B.3)
- **3중 하이브리드 검색 + reranker** : 한국어 dense + 영어 dense + 영어 BM25를 RRF로 합치고 bge-reranker-v2-m3로 재정렬, 부족하면 `query_rewriter`가 최대 2회 재검색
  - 선택 이유 : 질의는 한국어, 논문은 영어라 한→영 교차언어 조건에서 측정했을 때 이 구성의 MRR이 가장 높았다(reranker로 MRR 0.07~0.09 상승, 설계 B.6)
- **한→영 교차언어 임베딩 자체 평가(bge-m3)** : 논문 청크로 만든 한국어 질문 42문항(SW 21, HW 21)으로 후보 4종을 직접 비교
  - 선택 이유 : 리더보드 순위는 "한국어 질의로 영어 논문 찾기"라는 이 과제 조건을 반영하지 않는다. 두 진영 문서를 모두 찾아야 하므로 약한 쪽 성능 min(SW, HW) MRR을 먼저 봤고, 입력 길이(가장 긴 청크 1,529토큰)를 담는지도 필수 조건으로 봤다

### ② 기술 조사 및 관점별 병렬 평가
`tech_research` → [`trl_assessor` · `market_evaluator` · `stakeholder_evaluator` · `domain_evaluator`] → `synthesizer`(defer)
- `tech_research` : 원 논문에서 작동 원리·적용 범위·실험 조건·보고된 성능·한계를 근거 ID와 함께 정리한다
- 네 관점은 동시에 실행되고 각자 자기 결과 키(`trl_result` 등)에만 쓴다. 공유 키(`evidence`, `warnings`, `audit_log`)는 reducer로 합친다
- 관점 에이전트 공통 루프 : 두 기술에 같은 찬반 질의 템플릿 → 웹·논문 검색 → 근거마다 긍정·부정·중립, 기술 고유 여부, 조건 불일치 표시 → 찬반 근거가 각 2계열 이상이 될 때까지 재질의(최대 2회)

| 관점 | 평가 기준(가중치) | 점수 방식 |
|---|---|---|
| TRL | 검증 수준, 코드·데이터 공개, 서빙 프레임워크 반영, 샘플·제품 출시 | 점수 대신 범위(하한·상한)와 신뢰도. 상·하한마다 근거 계열 |
| 시장성 | 시장 규모·성장 25, 상용화·채택 30, 생태계 지원 30, 도입 비용 구조 15 | C.6 Rubric 가중 평균 |
| 이해관계자 | 클라우드·데이터센터, GPU·메모리 벤더, 개발자, 투자·분석·언론 각 25 (개발사 발언 제외) | 집단별 지지 5 / 중립·혼재 3 / 우려 1 |
| 도메인 적합성 | W1 장문맥 배치 50, W2 고동시성 다중 턴 50 (각각 비용·지연·처리량·정확도·통합 20) | 워크로드별 가중 평균 |

- C.6 Rubric 근거 조건 : 긍정 2계열 이상·부정 없음 5 / 긍정이 더 많고 부정 1계열 이상 4 / 비슷하거나 중립 위주 3 / 부정이 더 많고 긍정 1계열 이상 2 / 부정 2계열 이상·긍정 없음 1 / **근거 1계열 이하 = 판단 보류**(빠진 가중치 50% 초과 시 관점 전체 보류). 기술 고유 근거는 제목·본문에 기술명이 있어야 인정
- `synthesizer` : 같은 기술 안에서 관점 점수 차이로 상충(2.0 이상)·부분 상충(1.0 이상)·일치를 판정하고, 가설 H1(TRL × 시장 격자)·H2(기술 언급 대 생태계 언급)·H3(W1·W2 차이)과 기준값 ±0.5 민감도를 코드로 계산한다. H4(보완 관계)와 해설만 LLM이 쓴다

- **찬반 양면 검색과 원 출처 계열 50% 규칙(확증편향 방지)** : 두 기술에 같은 지지·반대 질의 템플릿과 같은 검색 한도를 쓴다. 기술명이 명시된 기술 고유 근거만 찬반으로 세고, 같은 보도자료를 옮긴 기사는 한 원 출처 계열로 묶어 한 계열이 웹 근거의 50%를 넘지 않게 한다. 개발사(Google·SK hynix) 발언은 이해관계자 점수에서 제외
  - 선택 이유 : 웹 자료가 많은 기술이나 개발사 홍보 자료가 점수를 끌고 가는 것을 막기 위해서다(설계 C.2, C.8)
- **Rubric 기반 코드 채점** : 기준마다 C.6 Rubric의 근거 조건(예: 긍정 2계열 이상·부정 없음 = 5점, 근거 1계열 이하 = 판단 보류)으로 코드가 점수를 계산하고, LLM은 근거마다 긍정·부정 표시와 이유만 쓴다
  - 선택 이유 : LLM이 직접 매긴 점수는 근거가 부족한 기준에도 점수를 주는 경우가 있었다(표본 12개 중 4개 불일치, `docs/RUBRIC_CHECK.md`). 판정과 근거 문장이 어긋난 경우도 있어 H1~H3 가설 판정도 코드로 계산한다. 근거 부족을 낮은 점수로 바꾸지 않는 규칙을 지키려면 코드로 강제해야 한다
- **TRL 범위·신뢰도 추정** : 공개 정보 기반 추정임을 명시하고 단일 값이 아닌 범위(하한·상한)와 신뢰도로 제시, 상·하한마다 근거 계열을 연결
  - 선택 이유 : TRL 4~6 구간은 수율·실측치가 영업 비밀이라 공개 정보가 가장 적다. 한 숫자로 적으면 확인되지 않은 정밀도를 주장하게 된다(가이드 C, 설계 C.4)
- **상충·가설 판정과 민감도의 코드 계산** : 관점 간 점수 차 2.0 이상 상충·1.0 이상 부분 상충, H1(TRL × 시장 3×3 격자)·H2(기술 언급 대 생태계 언급)·H3(W1·W2 기준별 차이)을 코드로 판정하고 기준값을 ±0.5 바꿨을 때 달라지는 칸 수를 보고
  - 선택 이유 : 2.0·1.0 같은 기준값은 절대 기준이 아니므로, 판정을 재현 가능하게 하고 기준값에 얼마나 민감한지 함께 보여 줘야 한다

### ③ Judge 검증 및 선택적 재실행
`judge` → `retry_router` ⇢ 미달 관점 노드만 / ⇢ `report_writer`
- `judge`(gpt-4.1) : 관점별로 근거성·중립성·출처 다양성·완결성을 1~5점 채점하고, 코드로 한 출처 계열 비중 ≤ 50%, 우열 어휘 0건, 찬반 각 2계열 이상(TRL은 상·하한 근거)을 검사한다. `passed = LLM 4항목 모두 4점 이상 and 규칙 검사 통과`
- `retry_router` : 미달 관점의 재실행 횟수를 올리고 `Command(goto=[관점 노드명])`로 그 관점만 다시 실행한다(최대 2회). 통과한 관점은 다시 채점하지 않는다. 한도 후에도 미달이면 "판정 불확실"로 보고서 한계점에 싣는다

- **Judge 선택적 재실행** : Judge(gpt-4.1)가 설계서 D.5 판정식(LLM 4항목 4점 이상 + 출처 비중 50% 이하 + 우열 어휘 0건 + 찬반 각 2계열 이상)으로 미달 관점을 지정하면 `retry_router`가 `Command(goto=[관점 노드명])`로 그 관점만 다시 실행(최대 2회). 통과 관점은 동결하고, 한도 후에도 미달이면 "판정 불확실"로 보고서에 기록
  - 선택 이유 : 전체를 다시 돌리면 비용이 들고 통과한 관점까지 결과가 흔들린다. `Send`로 부른 노드는 넘겨준 값만 받아 `tech_brief`·`evidence`를 못 읽으므로 `Command(goto)`를 썼다

### ④ 보고서 생성·검수
`report_writer` → `final_check` ⇢ `report_writer`(수정 1회) / ⇢ `pdf_renderer`
- `report_writer` : 설계서 E.1 목차(SUMMARY → 1~6장 → REFERENCE)로 쓴다. 수치·표는 State에서 코드로 만들고 LLM은 근거 ID가 붙은 해설만 쓴다. 근거 ID는 인용 번호로 바뀌고 REFERENCE에는 본문에 인용한 자료만 들어간다
- `final_check` : 근거 없는 문장, 본문 인용과 REFERENCE 일치, SUMMARY ½쪽, 우열 어휘, TRL 수치를 검사한다. 실패하면 한 번 다시 쓰게 하고, 그래도 실패하면 결정적 후처리(어휘 치환·문장 삭제)를 한 뒤 경고를 남긴다
- `pdf_renderer` : 목차 쪽 번호를 채운 SKALA 양식 PDF를 만들고 빈 페이지를 검사한 뒤 `deliverables/`에 복사한다

- **보고서 자동 생성·검수** : 설계서 E.1 목차(목차 페이지 포함), 인용 번호, 본문에 인용한 자료만 담은 REFERENCE. `final_check`가 근거 없는 문장·REFERENCE 일치·SUMMARY 분량(½쪽)·우열 어휘·TRL 수치를 검사하고 1회 수정, SKALA 양식 PDF로 변환
  - 선택 이유 : 과제의 핵심 조건(우열·추천 금지, SUMMARY ½쪽, 실제 인용 자료만 REFERENCE)을 사람 검토 없이도 매번 지키게 하기 위해서다. "우열 어휘 검사"는 "우수하다·더 낫다·추천한다·압도" 같은 표현 사전을 코드로 찾아 0건인지 확인하고, 있으면 중립 표현으로 바꾸는 검사이다
- **종료 보장·재현성** : 모든 루프에 횟수 한도(공통 검색 2, 관점 내부 2, 관점 재실행 2, 보고서 수정 1)와 `recursion_limit` 50. LLM 응답·웹 검색 결과를 저장소에 커밋해 `--offline`으로 API 키 없이 같은 보고서를 다시 만든다
  - 선택 이유 : 웹 검색 결과와 LLM 출력은 시간이 지나면 바뀌므로, 평가자가 키 없이도 제출한 보고서를 그대로 재현할 수 있어야 한다

## 평가 결과 (보고서 핵심 포인트)
평가 보고서 : [`deliverables/RAG-Output_판교_9반_김정인+김지수+김진수+전진만+정원준.pdf`](deliverables/RAG-Output_판교_9반_김정인+김지수+김진수+전진만+정원준.pdf) (16쪽, 수집 근거 167건, 본문 인용 참고문헌 58건)

| | TurboQuant | ITME |
|---|---|---|
| TRL (공개 정보 기반 추정) | 4–6, 신뢰도 높음 | 5–6, 신뢰도 보통 |
| 시장성 | 4.45 | 판단 보류 |
| 이해관계자 | 5.00 | 4.50 |
| 도메인 적합성 | 4.78 (W1 5.0 · W2 4.5) | 판단 보류 |

- 점수는 공개 자료에 나타난 평가의 방향을 나타내는 인식 점수이며 기술의 품질이나 우열이 아니다. 두 기술의 점수를 서로 비교하지 않는다
- 가설 판정 : H1 부분 지지(TurboQuant는 TRL 4–6 대비 시장 평가가 앞서는 "기대 선행"), H2 기각(두 기술 모두 이해관계자 근거에서 기술 자체 언급이 생태계·전략 언급보다 많음), H3 판단 보류(TurboQuant는 W1·W2 차이가 기준을 넘지 않아 기각 쪽, ITME는 근거 부족), H4 부분 지지
- 관점 간 상충 : 비교 가능한 관점 쌍은 모두 일치. 기준값을 ±0.5 바꿨을 때 판정이 바뀐 칸은 상충 판정 1칸, H1 격자 1칸
- 확증편향 방지 결과 : Judge 통과는 이해관계자 관점. TRL·시장성·도메인은 재실행 2회 후에도 ITME의 독립 근거(기술명을 언급한 제3자 자료)가 부족해 "판정 불확실"로 공개했다. ITME는 2026년 6월 공개로 공개 후 기간이 짧다

## Lessons Learned
- **기준은 코드로 확인할 수 있어야 지켜진다** : "기술 고유 근거"를 LLM 표시에만 맡기자 ITME를 언급하지 않은 다른 기술 논문이 ITME 근거로 붙었다. "제목·본문에 기술명 명시"라는 확인 가능한 조건으로 바꿨다
- **판정은 코드, 문장은 LLM** : LLM이 가설을 "지지"로 판정하면서 근거 문장에는 "비슷하다"고 쓴 사례가 있었다. SUMMARY의 TRL 수치를 틀리게 쓴 사례도 있었다. 수치와 판정은 State에서 코드로 만들고, LLM 문장의 수치는 코드 값과 대조한다
- **공유 데이터의 속성은 한 곳에서 정한다** : 같은 기사를 관점마다 "보도자료 재인용 여부"로 다르게 판정해 출처 계열이 어긋났다. 문서당 한 번 판정하도록 바꾼 뒤 Rubric 표본 점검이 12/12로 맞았다
- **병렬 노드와 로컬 모델** : 네 관점이 MPS 위의 임베딩·reranker를 동시에 호출하자 프로세스가 죽었다. 검색만 순서대로 처리했다
- **근거 부족을 그대로 쓰는 것이 중립성이다** : 모든 관점을 통과시키려고 기준을 낮추지 않았다. 판단 보류와 판정 불확실은 보고서 한계점에 공개했다

## Usage
```bash
uv sync
cp .env.example .env                          # OPENAI_API_KEY, TAVILY_API_KEY, LANGSMITH_API_KEY 입력
uv run python app.py                          # 전체 그래프 실행 → outputs/·deliverables/에 평가 보고서 생성

# 옵션
uv run python app.py --offline                # 커밋된 LLM·웹 캐시만 사용(API 키 없이 재현). 키가 없으면 자동 전환
uv run python app.py --tech sw=turboquant,hw=itme
uv run python app.py --no-rerank              # reranker 생략(실행 시간 단축)
uv run python app.py --rebuild-index          # 청크·FAISS 인덱스 다시 만들기

uv run pytest -q                              # 단위 테스트(API 호출 없음)
uv run python scripts/render_graph.py         # docs/graph.png 다시 그리기
```
- 첫 실행은 논문 PDF 6편(arXiv)과 HuggingFace 모델(bge-m3 약 2.2GB, bge-reranker-v2-m3 약 2.2GB)을 내려받는다. PDF 변환에는 LibreOffice가 필요하다.
- `--offline`은 API를 호출하지 않지만 모델·논문 다운로드에는 인터넷이 필요하다. `--no-rerank`나 다른 `--tech`는 캐시에 없는 요청이 생기므로 API 키가 필요하다.

## Contributors
<!-- TODO: 팀 확인 필요 -->
- 김정인 : 평가 관점·채점 Rubric 설계, 보고서 검토
- 김지수 : 기술 조사·후보 평가표 작성, 참고문헌 정리
- 김진수 : RAG 파이프라인·임베딩 평가, LangGraph 에이전트 구현
- 전진만 : 시장·이해관계자 자료 조사, 발표 자료
- 정원준 : README·문서화, 그래프 설계 검토
