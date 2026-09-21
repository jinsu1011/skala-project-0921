# Subject
본 프로젝트는 KV cache 최적화 기술을 소프트웨어·하드웨어 두 진영에서 선정하여,
기술 성숙도(TRL)·시장·이해관계자·도메인 관점에서 평가하는 Agentic RAG를 개발하는 프로젝트임. (SKALA 4기 · 판교 9반 1조)

> 🚧 **현재 단계: 설계 산출물 v1.1 (피드백 반영 개정)** — [설계서 PDF](deliverables/) · [설계 원문](docs/DESIGN.md) · [개정 기록](docs/REVISION_v1.1.md) · [의사결정 기록](docs/DECISIONS.md)
> 에이전트·그래프 구현과 평가 보고서, 전체 README는 개발 단계에서 이어서 작성합니다.

## Selected Technologies
- SW : **Google TurboQuant** — 재학습 없이 서빙 단계에서 KV cache를 온라인 양자화(모델 불변 → 대칭 비교 가능)
- HW : **ITME (SK hynix)** — KV를 양자화하지 않고 CXL-hybrid 메모리로 TB급 계층 확장(vLLM 기반, 양산급 CMM 실측)

## Tech Stack (설계 단계 확정분)
- Framework : LangGraph
- LLM/Generator : gpt-4.1-mini · LLM/Judge : gpt-4.1 (교차 모델)
- Embedding : `BAAI/bge-m3` — 자체 한→영 교차언어 평가 42문항에서 Hit@1 0.548 · Hit@5 0.905 · MRR 0.703 (후보 4종 중 최고)
- Retrieval : FAISS + BM25 3중 RRF + bge-reranker-v2-m3 — **Hit@1 0.786 · Hit@5 0.976 · MRR@10 0.863**

## 재현 (설계 단계 산출물)
```bash
uv sync
uv run python scripts/download_papers.py          # 논문 6편(136p) 다운로드·페이지 검증
uv run python eval/run_embedding_eval.py encode   # 후보 4종 인코딩(성능·메모리 측정)
uv run python eval/run_embedding_eval.py select   # 평가셋 확정(캐시된 판정 사용)
uv run python eval/run_embedding_eval.py score    # Hit@K / MRR 표·차트
uv run python eval/check_truncation.py            # 모델 최대 입력 vs 평가 설정·잘린 청크 수
bash docs/render_graphs.sh                         # 그림 2a/2b 로컬 렌더링
uv run python report/build_design_doc.py          # 설계서 PDF (LibreOffice 필요)
```
