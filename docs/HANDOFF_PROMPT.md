# 인수인계 프롬프트: KV cache 다관점 평가 Agentic RAG, 개발 단계

> 사용법: 아래 "프롬프트 본문" 전체를 코딩 에이전트(또는 담당자)에게 그대로 전달한다. 설계 단계(설계서 v1.3)는 끝났다. 이 프롬프트는 **개발 → 평가 보고서 → README·발표 준비 → 재현성 검증 → 제출**까지를 다룬다.

---

## 프롬프트 본문

### 0. 너의 역할과 첫 행동

너는 SKALA 4기 판교 9반 1조(김정인·김지수·김진수·전진만·정원준)의 캡스톤 과제 **"KV cache 최적화 기술 다관점 평가 Agentic RAG"**의 개발을 이어받는다. 설계는 끝났고, 너의 일은 **설계서를 1:1로 구현하고, 평가 보고서 PDF를 자동 생성하고, README와 발표 노트를 완성하는 것**이다.

**첫 행동(읽기만, 구현 금지):**
1. 아래 문서를 읽는다.
   - `docs/DESIGN.md`(설계 원문, 최우선 기준)
   - `docs/DECISIONS.md`(D1~D31 결정 기록)
   - `docs/REVISION_v1.1.md`(개정 이력·남은 한계)
   - `docs/SELF_CHECK.md`
   - `deliverables/RAG-Design_판교-9반_…pdf`
2. 과제 안내(Notion)를 읽는다: https://actually-war-1ea.notion.site/KV-cache-3ba7f4c866938099b7a8fdaa1831c07e (접힌 토글을 모두 펼칠 것)
3. 환경을 점검한다: `uv --version`, `cat .python-version`(3.11), `uv sync`, `gh auth status`, `.env` 존재 여부. 키 값은 절대 출력하지 않는다.
4. **한국어로 짧게 계획을 보고하고 승인을 기다린다.** 계획에는 단계, 산출물, 예상 시간, 예상 API 비용이 들어가야 한다.

### 1. 현재 상태 (이미 끝난 것)

- repo: https://github.com/jinsu1011/skala-project-0921 (PUBLIC, `main`)
- 로컬 작업 폴더: `~/Documents/SKALA_09.18_ai-service/capstone-v1/`
- 환경: uv + Python 3.11(`.python-version`), `pyproject.toml`·`uv.lock`. **pip 금지, `uv add`만 사용.** `uv sync` 전에 3.11 고정을 확인한다(이 Mac에는 3.14도 있어 uv가 잘못 고를 수 있다).
- 이미 구현된 것:

| 경로 | 내용 |
|---|---|
| `scripts/download_papers.py` | arXiv 6편 다운로드 + 136페이지 검증(PDF는 gitignore) |
| `rag/loader.py`, `rag/chunker.py` | 글꼴 기반 절 인식 로딩, 900토큰(cl100k)·15% 겹침 청킹, 159청크, 메타데이터 |
| `rag/embedder.py` | 후보 4종 레지스트리. **런타임은 bge-m3, `max_seq_length` 2,048**(`RUNTIME_MAX_SEQ`) |
| `rag/retriever.py` | `HybridRetriever`: FAISS dense + BM25 + RRF + 메타데이터 필터 + (선택) cross-encoder rerank, 인덱스 자동 생성·재사용 |
| `rag/llm.py` | `get_llm("generator"\|"judge")`: gpt-4.1-mini / gpt-4.1, temperature 0, seed 42, **SQLite 응답 캐시**(`data/llm_cache.sqlite`, 커밋됨, 키 없이 재생 가능) |
| `eval/` | 한국어→영어 42문항 평가셋, 임베딩·검색 구성 평가(v1: 1,024 / v2: 2,048), 잘림 점검, 풀링 커버리지 |
| `report/docx_builder.py` | **SKALA 공식 docx 양식**(`report/template/`)에 Markdown을 채워 LibreOffice로 PDF 변환 |
| `report/mermaid_render.py` | 로컬 mermaid 렌더링(vendored mermaid.js + headless Chrome). **mermaid.ink 등 외부 업로드 금지**(정책상 차단됨) |
| `report/build_design_doc.py` | 설계서 PDF 빌드(참고용 예시) |

- 비어 있는 디렉토리(구현 대상): `agents/`, `graph/`, `tools/`, `prompts/`
- `config.yaml`: 선정 기술(`selected_techs`), LLM, 청킹, 검색(`embedding_model: bge-m3`, reranker), 그래프 한도, 팀 정보(`team.submit_date`는 **DAY 3 날짜 확인 필요**)

### 2. 절대 규칙

1. **설계 충실도가 채점 항목(15점)이다.** 노드 이름 18개, State 키 27개, 루프 4개와 한도, Judge 판정식, 점수 규칙을 `docs/DESIGN.md` C~E와 **글자 그대로** 맞춘다. 구현하다 설계와 달라져야 하면 `docs/DECISIONS.md`에 "결정 / 근거 / 대안"으로 기록하고 `docs/DESIGN.md`를 고친 뒤 설계서 PDF를 다시 빌드한다.
2. **우열 판정 금지.** 보고서·프롬프트·코드 어디에서도 기술 간 순위·승패·추천을 만들지 않는다. "우수하다/열등하다/더 낫다"는 금지 어휘이다.
3. **비밀정보 위생(public repo).**
   - `.env`는 절대 커밋하지 않는다.
   - push 전마다 다음 명령을 실행한다: ``git -c core.quotepath=false ls-files -z | xargs -0 grep -naIE "sk-[A-Za-z0-9_-]{20,}|tvly-|lsv2_"``. 결과가 없어야 한다.
   - 키 값은 콘솔이나 로그에 출력하지 않는다.
4. **커밋 작성자는 repo 소유자 본인(jinsoo kim)만.** `Co-Authored-By` 트레일러, "Generated with …" 문구, 문서 안의 AI 작업자 표기를 넣지 않는다.
5. **비용 통제.**
   - LLM이나 Tavily를 대량 호출하는 작업(전체 그래프 반복 실행, 대량 판정)은 **실행 전에 예상 비용을 보고하고 승인**을 받는다.
   - 캐시를 최대한 재사용한다.
   - Tavily 무료 크레딧(월 1,000) 안에서 쓴다.
6. **새 측정값은 기존 값과 구분해 표기**한다(v1/v2처럼). 기존 수치를 덮어쓰거나 꾸미지 않는다.
7. 모든 산출물(보고서·README·발표 노트)은 **한국어**로 쓴다. 코드와 주석은 영어도 된다.

### 3. 구현할 것 (설계서 D 기준)

**3-1. `graph/state.py`: State (TypedDict, 27개 키)**
- 표 D.2를 그대로 옮긴다. reducer가 있는 키는 3개뿐이다.
  - `evidence: Annotated[list[Evidence], merge_by_id]`
  - `warnings: Annotated[list[str], add_unique]`
  - `audit_log: Annotated[list, operator.add]`
- 관점 결과 키 4개(`trl_result`, `market_result`, `stakeholder_result`, `domain_result`)는 reducer 없이 **교체**한다.
- `merge_by_id` 정책:
  - `evidence_id`는 (출처, 위치)의 해시로 만든다.
  - 같은 ID가 다시 들어오면 `perspectives`와 `stances`만 합집합으로 합친다.
  - `claim`이 서로 다르면 `warnings`에 충돌을 기록한다.
  - `attempt` 필드로 대체된 이전 결과를 집계에서 뺀다.
- Evidence 필드: `scope`(tech_specific / category), `source_class`(vendor / third_party / academic), `source_group`(eTLD+1 또는 벤더 출처군), **`origin_group`**(원 출처 계열. 재인용 기사는 원 보도자료와 같은 계열).
- Pydantic 모델: `Evidence`, `PerspectiveResult`, `SynthesisResult`, `JudgeScore`(D.2의 객체 스키마).

**3-2. `tools/`: LangChain `@tool` 3개 (docstring, 타입 필수)**
- `paper_retrieve(query, camp, tech, role, k)`
  - `rag.retriever.HybridRetriever`를 쓴다(bge-m3, 3중 RRF + rerank, `--no-rerank` 옵션).
  - 근거 ID는 `P:<chunk_id>` 형식이다.
  - 영어 재작성은 **질문만 보고** 한다(`eval/build_qa.py`의 `REWRITE_SYS`와 같은 프롬프트).
- `web_search(query, days, stance)`
  - Tavily를 호출하고 결과를 `data/web_cache/`에 날짜 스탬프와 함께 JSON으로 저장한다(커밋 대상).
  - `--offline`이면 캐시만 쓴다.
  - `days`는 news 토픽에만 적용되므로, general 토픽에서는 `time_range`로 바꿔 넘긴다.
  - URL을 eTLD+1로 정규화하고, 벤더 출처군 사전(Google 계열, SK hynix 계열 등)으로 `source_group`과 `source_class`를 붙인다.
  - 근거 ID는 `W:<hash>` 형식이다.
- `summarize_sources(docs, focus)`: 근거 ID를 보존하는 구조화 요약을 만든다. 근거 없는 문장은 금지한다.

**3-3. `agents/`: 에이전트 7개, 노드 18개 (파일 하나에 에이전트 하나)**

| 에이전트 | 노드 | 핵심 |
|---|---|---|
| 기술 조사 | `selection_validator`, `tech_research`, `trl_assessor` | 선정은 **2안(Human)**이다. 검증 결과만 기록하고 **그래프 안에 재선정 분기는 없다.** TRL은 C.4 증거 사다리에 따라 범위와 신뢰도로 매기고 "공개 정보 기반 추정"을 표기한다 |
| 시장 평가 | `market_evaluator` | 논문 RAG는 "발표 주장 ↔ 원 논문 실험 조건 대조"에만 쓴다. 기준 가중치는 25/30/30/15 |
| 이해관계자 평가 | `stakeholder_evaluator` | 웹만 쓴다. 집단 (a)~(d)는 각 25%이고, 입장을 지지 5 / 혼재·중립 3 / 우려 1로 환산한다. H2용 태그(기술 특성 / 생태계) |
| 도메인 평가 | `domain_evaluator` | W1·W2는 각 50%이고, 비용·지연·처리량·정확도·통합을 각 20%로 본다. 온디바이스 대조는 점수에서 뺀다(H3 전용) |
| 평가 종합 | `synthesizer` | `defer=True`. 상충 판정은 시장·이해관계자·도메인 사이에서, 기술 안에서만 한다(Δ≥2.0 상충, 1.0~2.0 부분 상충). TRL은 H1 사전 규칙에만 쓴다. 임계값 ±0.5 민감도를 함께 계산한다. 기술 간 순위는 금지 |
| Judge | `judge` | gpt-4.1. `passed = all(4항목 ≥ 4) and max_origin_share ≤ 0.5 and pro_origins ≥ 2 and con_origins ≥ 2 and lexicon_hits == 0`. 출력은 `judge_scores`, `failed_perspectives`, `judge_feedback` |
| 보고서 생성 | `report_writer` | E 목차대로 쓰고, 본문에 인용한 근거만 REFERENCE로 만든다(Notion 형식) |

- 관점 에이전트 내부 루프(최대 2회): 찬반 질의 생성(재실행 시 `judge_feedback` 반영) → 검색 → 근거 평가(관련성·`scope`·출처군) → 부족하면 재질의.
  - 찬반 할당량은 `tech_specific` 근거의 **독립 계열 수**로 센다.
  - 부족하면 거짓 균형을 만들지 않고 "근거 부족"으로 둔다.
- 근거가 부족한 기준은 낮은 점수가 아니라 "판단 보류"로 두고 계산에서 뺀다. 빠진 가중치가 50%를 넘으면 그 관점 전체를 "판단 보류"로 한다.

**3-4. `graph/workflow.py`: 그래프 (그림 2a·2b)**
- 흐름: `initialize → selection_validator → index_builder → query_planner → hybrid_retriever → retrieval_grader`
  - 부족하고 재시도 < 2이면 `query_rewriter → hybrid_retriever`로 돌아간다.
  - 충분하면 `tech_research → [trl_assessor, market_evaluator, stakeholder_evaluator, domain_evaluator]`(Fan-out) `→ synthesizer`(defer) `→ judge → retry_router`로 간다.
- **`retry_router`는 노드이다.** 조건부 엣지 함수는 State를 갱신할 수 없기 때문이다.
  - `perspective_retry_count`를 1 올린다.
  - `Command(update=…, goto=[Send(관점 노드, {judge_feedback})…])`를 반환한다.
  - 한도(2)에 이른 관점은 `warnings`에 "판정 불확실"을 기록하고, 다시 돌릴 관점이 없으면 `report_writer`로 간다.
- 이어서 `report_writer → final_check`로 간다.
  - 수정이 필요하고 재시도 < 1이면 `report_writer`로 되돌아간다.
  - 통과하면 `pdf_renderer → END`로 간다.
- `final_check`의 점검 항목:
  - 모든 핵심 주장에 유효한 근거 ID가 있는가
  - REFERENCE가 본문 인용과 일치하는가
  - SUMMARY가 ½페이지 이내인가
  - 우열 어휘가 0건인가
- 구현한 뒤 `draw_mermaid_png`로 `docs/graph.png`를 만들고 그림 2a와 대조한다. 외부 API를 쓰면 안 되니, 필요하면 `draw_mermaid()` 소스를 `report/mermaid_render.py`로 로컬 렌더링한다.

**3-5. `prompts/`**
- 에이전트별 템플릿을 둔다. 두 기술에 **같은 질문 템플릿**을 쓴다(대칭).
- 가설 H1~H4를 결론처럼 쓰지 말고 "검증할 가설"로만 제시한다.

**3-6. `app.py`**
- 실행: `uv run python app.py [--offline] [--tech sw=turboquant,hw=itme] [--no-rerank] [--rebuild-index]`
- 동작 순서:
  1. 인덱스를 자동으로 만들거나 재사용한다.
  2. 그래프를 실행한다.
  3. `outputs/RAG-Output_판교_9반_김정인+김지수+김진수+전진만+정원준.md`와 `.pdf`를 생성하고, PDF를 `deliverables/`에도 복사한다.
  4. 로그를 `outputs/logs/`에 남기고 LangSmith tracing을 켠다.
- **API 키가 없으면 자동으로 offline 재생 모드**로 실행한다. LLM 캐시와 웹 캐시로만 보고서를 다시 만든다.

### 4. 평가 보고서 (Notion 기준 DAY 3 15시 마감)

- 파일명: `RAG-Output_판교_9반_김정인+김지수+김진수+전진만+정원준.pdf`(설계서와 달리 **언더스코어**)
- **SKALA 양식** 사용: `report/docx_builder.py`의 `ReportBuilder`와 `CoverInfo`(`report_kind="과제 제출 보고서 · 평가 보고서"`)
- 목차(설계서 E):
  1. SUMMARY(½페이지 이내, 개요가 아닌 핵심 발견)
  2. 분석 배경
  3. 기술 선정
  4. 기술 개요
  5. 관점별 평가(TRL, 시장, 이해관계자, 도메인)
  6. 시사점(상충 매트릭스, H1~H4 판정, 민감도)
  7. 한계점
  8. REFERENCE
- 한계점 장에 반드시 넣을 것:
  - 공개 정보 기반 추정
  - 개발/테스트셋 미분리
  - 같은 계열 Judge
  - ITME 공개 후 기간이 짧음
  - 평가 주체의 소속 편향 가능성
  - "판정 불확실"로 남은 관점
- REFERENCE 형식:
  - 논문: `저자(YYYY). 제목. 학회/학술지, 권(호), 페이지.` (arXiv만 있으면 `arXiv, <id>`)
  - 웹: `기관(YYYY-MM-DD). 제목. 사이트, URL`
  - 특허: `출원인(YYYY-MM). 특허명, 번호, URL`
- 생성한 PDF는 **페이지를 이미지로 바꿔 직접 확인**한다(한글, 표, 페이지 나눔, SUMMARY 분량). 우열 표현이 있는지도 직접 다시 읽는다.

### 5. README·발표

- `README.md`는 Notion 샘플 구조를 **그대로** 따른다: Subject, Overview, Selected Technologies, Features, Tech Stack, Agents, Architecture(그래프 이미지), Directory Structure, Usage, Contributors.
- Tech Stack에 넣을 수치: bge-m3와 검색 구성의 Hit@K·MRR. **"개발 지표(구성 선택용 42문항)"라고 표기**하고, v2(2,048) 수치를 쓴다.
- 차별점은 앞쪽에 둔다. 발표는 README 화면으로 10분 동안 한다.
  - 찬반 양면 검색과 원 출처 계열 50% 규칙
  - Judge 선택적 재실행(`retry_router` + Send)
  - 한국어→영어 교차언어 임베딩 자체 평가(측정 중 오류 3건을 찾아 바로잡은 과정)
  - 키 없이 재생 가능한 offline 재현성
- Contributors: 5명의 역할을 제안하되 PM/PL 역할은 넣지 않는다. `<!-- TODO: 팀 확인 필요 -->`를 표기한다.
- `docs/PRESENTATION_NOTES.md`: 10분 흐름(차별점 → 보고서 핵심 포인트 → Lessons Learned).

### 6. 재현성 검증 (채점 10점)

1. repo를 새 폴더에 `git clone`한다.
2. `.env` 없이 `uv sync && uv run python app.py --offline`을 실행해 PDF가 생성되는지 확인한다.
3. `.env`가 있는 상태에서도 한 번 실행한다(비용은 사전 승인을 받는다).
4. venv와 인덱스를 지운 뒤 실행해도 되는지 확인한다.
5. 첫 실행은 HuggingFace 모델(bge-m3 약 2.2GB, reranker 약 2.2GB)과 arXiv PDF를 다운로드한다는 점을 README에 적는다.

### 7. 제출 전 체크리스트

- [ ] 노드 18개, State 27개, 루프 4개의 한도, Judge 판정식이 설계서와 일치(`docs/SELF_CHECK.md`에 근거 기록)
- [ ] 보고서가 설계 목차를 따르고, SUMMARY가 ½페이지 이내이며, REFERENCE는 인용한 것만 형식대로
- [ ] 우열 어휘 0건
- [ ] offline 클린 재현으로 PDF 생성
- [ ] 비밀정보 스캔 통과, `.env` 미추적, 커밋 작성자는 본인만
- [ ] `config.yaml`의 `team.submit_date`를 실제 DAY 3 날짜로 수정
- [ ] 최종 보고(한국어): 파일 경로, repo URL, Hit@K·MRR, 자체 점검 요약. 사람이 할 일(Contributors 역할 확인, Slack 스레드에 PDF 두 개와 Git 링크 업로드)

---

## 참고: 남은 개선 과제 (시간이 있을 때만)

- 평가셋 확충(논문별 15문항 이상) 후 개발셋과 테스트셋 분리, 정답 청크 전수 사람 검수
- v2 재측정에서 판정 이력이 없는 후보 쌍 추가 판정(약 $0.5, 승인 필요)
- Judge를 다른 계열 모델로 바꾸는 방안 검토(현재 같은 GPT-4.1 계열)
