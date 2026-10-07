# Orchestrator 계획 변화 (입력별)

같은 Orchestrator 프롬프트·코드에 입력(State)만 바꿔 계획 단계만 실행한 결과이다. 작업 수, 관점 분할, 추가 관점, 배정 Agent가 입력에 따라 달라진다. temperature 0과 응답 캐시 때문에 **같은 입력이면 같은 계획**이 나온다(재현성). 계획 단계만 실행했으므로 State에 기술 개요·근거가 없다. 전체 실행에서는 이 정보가 더해져 계획이 또 달라진다(아래 표). `uv run python scripts/plan_variations.py`로 다시 만든다.

| 입력 | 기술 | 계획 출처 | SubTask 수 | SubTask (관점 → 담당 Agent, 기술) | 미사용 Agent | 코드 보정 |
|---|---|---|---|---|---|---|
| 기본 조합 | TurboQuant, ITME | llm | 7 | T01 technology readiness → `trl_specialist` ['turboquant']<br>T02 technology readiness → `trl_specialist` ['itme']<br>T03 marketability → `market_specialist` ['turboquant']<br>T04 marketability → `market_specialist` ['itme']<br>T05 stakeholder perspective → `stakeholder_specialist` ['turboquant', 'itme']<br>T06 domain applicability → `domain_specialist` ['turboquant', 'itme']<br>T07 additional research → `research_generalist` ['turboquant', 'itme'] | ecosystem_specialist, regulation_specialist | - |
| ITME 검색 공백(원리·수치 누락) | TurboQuant, ITME | llm | 8 | T01 technology readiness → `trl_specialist` ['turboquant']<br>T02 technology readiness → `trl_specialist` ['itme']<br>T03 marketability → `market_specialist` ['turboquant']<br>T04 marketability → `market_specialist` ['itme']<br>T05 stakeholder perspective → `stakeholder_specialist` ['turboquant']<br>T06 stakeholder perspective → `stakeholder_specialist` ['itme']<br>T07 domain applicability → `domain_specialist` ['turboquant', 'itme']<br>T08 additional research → `research_generalist` ['turboquant', 'itme'] | ecosystem_specialist, regulation_specialist | - |
| 다른 기술 조합 1 | KIVI, InfiniGen | llm | 6 | T01 technology readiness → `trl_specialist` ['kivi', 'infinigen']<br>T02 marketability → `market_specialist` ['kivi', 'infinigen']<br>T03 stakeholder perspective → `stakeholder_specialist` ['kivi', 'infinigen']<br>T04 domain applicability → `domain_specialist` ['kivi', 'infinigen']<br>T05 competition/ecosystem → `ecosystem_specialist` ['kivi', 'infinigen']<br>T06 cost structure and implementation risk → `research_generalist` ['kivi', 'infinigen'] | regulation_specialist | - |
| 다른 기술 조합 2 | DeepSeek MLA, CXL-PNM | llm | 8 | T01 technology readiness → `trl_specialist` ['mla']<br>T02 technology readiness → `trl_specialist` ['cxl_pnm']<br>T03 marketability → `market_specialist` ['mla']<br>T04 marketability → `market_specialist` ['cxl_pnm']<br>T05 stakeholder perspective → `stakeholder_specialist` ['mla', 'cxl_pnm']<br>T06 domain applicability → `domain_specialist` ['mla', 'cxl_pnm']<br>T07 competition/ecosystem → `ecosystem_specialist` ['mla', 'cxl_pnm']<br>T08 cost and implementation risk → `research_generalist` ['mla', 'cxl_pnm'] | regulation_specialist | - |

## 전체 그래프 실행 비교 (LangSmith trace 있음)

| 실행 | 기술 | SubTask | 미사용 Agent | Judge 실패 → 재시도 | 검토 | 품질 평가 |
|---|---|---|---|---|---|---|
| 최종 제출 `37d8bdd72d30` | TurboQuant, ITME | 6개 (4개 최소 관점 + 규제 + 경쟁·생태계, 계획 7개 중 검토자 확보로 1개 제외) | research_generalist | T01~T04 → T01 통과, T02~T04 FAILED_AFTER_RETRY | 4건 (작업당 1명) | 한계 기록 후 종료 |
| 비교 `802f65b077b3` | KIVI, InfiniGen | 5개 (4개 최소 관점 + 경쟁·생태계) | regulation_specialist, research_generalist | T02~T04 → 모두 FAILED_AFTER_RETRY, T01·T05는 1회 통과 | 6건 (작업당 2명) | 통과 |
