# Subject
본 프로젝트는 KV cache 최적화 기술을 소프트웨어, 하드웨어 두 진영에서 선정하여, 
기술 성숙도(TRL)·시장·이해관계자·도메인 관점에서 평가하는 Agentic RAG를 개발하는 프로젝트 임.


## Overview
- Objective : 하나의 기술을 복수 관점에서 비교 평가 (우열·추천 판정 없이, 관점별 인식 차이와 그 근거·조건을 추적)
- Method : Multi-Agent(Distributed) + Agentic RAG
- Tools : LangGraph, LangChain, FAISS, BM25(rank-bm25), sentence-transformers, Tavily, PyMuPDF, LangSmith, uv


## Selected Technologies
- SW : **Google TurboQuant** — 재학습 없이 서빙 단계에서 KV cache를 온라인 양자화(data-oblivious). 무작위 회전 + 좌표별 scalar quantization, 잔차 1-bit QJL로 내적 추정 편향을 보정
- HW : **ITME (SK hynix)** — KV를 양자화하지 않고 CXL-hybrid 메모리(TB급, byte-addressable)로 저장 공간을 확장. vLLM 위에 구현, 양산급 CMM·FPGA 프로토타입으로 실측
- 공정 비교 근거 : 두 기술은 **같은 KV cache 용량 병목을, 같은 서빙 시점에서, 서로 다른 시스템 계층**(데이터 표현 vs 메모리 계층)으로 해결함. 후보 6개(Doc Pool)를 가중 기준표로 채점(TurboQuant 4.35, ITME 4.50)해 조가 직접 선정(2안)하고, 에이전트는 사후 검증만 수행


## Features
- PDF 자료 기반 정보 추출 : 논문 6편(136p, 한도 200p)을 절 인식 청크 159개로 인덱싱. 머리글·쪽번호·워터마크 제거, 참고문헌 제외, `tech`·`role` 메타데이터로 1차 근거와 비교 근거 구분
- Agentic RAG : 질의 계획 → 한→영 질의 재작성 → 3중 하이브리드 검색(RRF) + reranker → `retrieval_grader` 품질 판정 → 부족 시 재작성 루프(최대 2회)
- 관점 에이전트 자기교정 루프 : 관점마다 지지·반대 질의를 짝으로 검색하고, 찬반 균형·출처군 비중이 부족하면 재질의(최대 2회)
- 4관점 병렬 평가 : TRL·시장·이해관계자·도메인을 같은 superstep에서 Fan-out, `synthesizer`(defer)로 Fan-in
- 선택적 재실행 : Judge가 미달로 판정한 관점만 `retry_router`가 `Command(goto=[노드명])`로 다시 실행(관점별 최대 2회, 통과 관점 점수 동결)
- 코드 기반 판정 : 관점 간 상충(점수 차 2.0/1.0), 가설 H1(TRL × 시장 3×3 격자)을 코드로 계산하고 임계값 ±0.5 민감도 점검
- TRL 추정 : 공개 정보 기반 추정임을 명시하고 단일 값이 아닌 범위와 신뢰도로 제시
- 종료 보장·재현성 : 모든 루프에 횟수 한도와 "한도 소진" 출구(최악 약 30 superstep, `recursion_limit` 50). LLM 응답·웹 검색 캐시 커밋으로 `--offline` 재생
- 확증 편향 방지 전략 : 찬반 양면 검색, 기술 고유(`tech_specific`) 근거만 찬반 할당량으로 인정, 원 출처 계열(`origin_group`) 기준 50% 상한, 개발사 본인 발언의 이해관계자 점수 제외, 인용 필수(근거 없는 문장 삭제), 우열 어휘 사전 검사, 상위 모델 Judge, 두 기술에 같은 템플릿·검색 한도·Rubric 적용


## Tech Stack
- Framework : LangGraph (1.2.x)
- LLM/Generator : gpt-4.1-mini (temperature 0, seed 42)
- LLM/Judge : gpt-4.1
- Retrieval : FAISS(IndexFlatIP) + BM25 3중 RRF(k=60) + bge-reranker-v2-m3 - Hit@1 0.786 · Hit@5 0.976, MRR@10 0.863 (한→영 42문항 개발 지표)
- Embedding : BAAI/bge-m3 (후보 4종 자체 교차언어 평가로 선정, MRR@10 0.703 · min(SW, HW) MRR 0.659)


## Agents
- 기술 조사 Agent : 선정 타당성 사후 검증(`selection_validator`), 원문에서 개요·실험 조건·한계 추출(`tech_research`), TRL 범위 추정(`trl_assessor`) — RAG (+Web: TRL 7~9 신호)
- 시장 평가 Agent : 시장 규모·성장, 상용화·채택 사례, 생태계 지원, 도입 비용 구조 평가(`market_evaluator`) — RAG + Web
- 이해관계자 평가 Agent : 클라우드 사업자·경쟁/협력 벤더·개발자 커뮤니티·투자/미디어의 입장 매트릭스(`stakeholder_evaluator`) — Web
- 도메인 평가 Agent : 데이터센터 장문맥 서빙의 W1(장문맥 배치)·W2(고동시성 다중 턴) 적합 조건·제약(`domain_evaluator`) — RAG + Web
- 평가 종합 Agent : 관점 간 일치·상충 매트릭스와 H1~H4 판정(상충·H1은 코드 계산, LLM은 해설)(`synthesizer`)
- Judge Agent : 관점별 근거성·중립성·출처 다양성·완결성 채점 + 결정적 검사, 미달 관점 지정(`judge`)
- 보고서 생성 Agent : 목차대로 본문 작성, 인용·REFERENCE 정리(`report_writer`)


## Architecture
![전체 그래프: 실선은 항상 지나는 경로, 점선은 조건에 따라 갈리는 경로](docs/graph_overview.png)

- 흐름 : Human 선정(2안) → 초기화 → 선정 검증(기록만) → 공통 RAG(Loop) → 기술 조사 → 4관점 병렬 평가(Fan-out) → 종합(Fan-in) → Judge → retry_router(선택적 재실행) → 보고서 생성·검수(Loop) → PDF
- 보조 노드 : `initialize`, `index_builder`, `query_planner`, `hybrid_retriever`, `retrieval_grader`, `query_rewriter`, `retry_router`, `final_check`, `pdf_renderer` (에이전트 7개, 노드 18개, State 키 27개)


## Directory Structure
```
├── app.py                 # 실행 스크립트
├── config.yaml            # 선정 기술·LLM·코퍼스·검색·그래프 한도 설정
├── agents/                # Agent 모듈
├── graph/                 # LangGraph State·워크플로(retry_router 포함)
├── tools/                 # @tool (paper_retrieve, web_search, summarize_sources)
├── rag/                   # PDF 로딩·청킹·임베딩·하이브리드 검색
├── prompts/               # 프롬프트 템플릿(채점 Rubric 포함)
├── eval/                  # 임베딩·검색 구성 평가(42문항 평가셋)
├── report/                # 보고서·설계서 PDF 생성, Mermaid 렌더링
├── scripts/               # 논문 다운로드·페이지 수 검증
├── data/                  # 문서 풀, LLM·웹 검색 캐시
├── docs/                  # 설계 원문, 의사결정 기록(DECISIONS.md), 그래프
├── deliverables/          # 설계 산출물·평가 보고서 PDF
├── outputs/               # 평가 결과 저장
├── pyproject.toml / uv.lock
└── README.md
```


## Usage
```bash
uv sync
cp .env.example .env                          # OPENAI_API_KEY, TAVILY_API_KEY, LANGSMITH_API_KEY 입력
uv run python scripts/download_papers.py     # 논문 6편 다운로드·페이지 수 검증
uv run python app.py                          # 전체 그래프 실행 → 평가 보고서 PDF 생성

# 옵션
uv run python app.py --offline                # 커밋된 캐시만 사용(API 키 없이 재현)
uv run python app.py --tech sw=turboquant,hw=itme
uv run python app.py --no-rerank              # reranker 생략(실행 시간 단축)
```

## Contributors
- 김정인 : (역할 기입)
- 김진수 : (역할 기입)
- 전진만 : (역할 기입)
- 정원준 : (역할 기입)
