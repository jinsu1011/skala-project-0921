# 목차

<!--w:6.5,1.5,6.5,1.5-->
| 장 | 쪽 | 장 | 쪽 |
|---|---|---|---|
| SUMMARY | 2 | 4. 관점별 평가 | 4 |
| 1. 분석 배경 | 2 | 5. 시사점 | 6 |
| 2. 기술 선정 | 3 | 6. 한계점 | 8 |
| 3. 기술 개요 | 3 | REFERENCE | 9 |
<!--toc-end-->

# SUMMARY

- TurboQuant: TRL 4–5(추정, 신뢰도 높음), 시장성 4.35†, 이해관계자 4.50†, 도메인 적합성 4.78† [1, p.15; 3]
- ITME: TRL 5–6(추정, 신뢰도 보통), 시장성 판단 보류, 이해관계자 5.00†, 도메인 적합성 판단 보류 [2, p.8]
- 가설 판정: H1 부분 지지, H2 기각, H3 판단 보류, H4 부분 지지 [2, p.2·8]
- TurboQuant은 NVIDIA A100 및 RTX 3090 GPU에서 실험적 검증과 오픈소스 구현을 통해 TRL 4~5 단계에 도달했다[1, p.15; 4; 5; 3; 6; 7].
- ITME는 FPGA 프로토타입과 실제 GPU 및 CXL 하이브리드 메모리 환경에서 성능 검증을 거쳐 TRL 5~6 단계로 추정된다[2, p.2·8·10; 8].
- 우열이나 추천이 아니라 관점별 평가 차이와 그 근거를 정리한 결과이다(판단 보류는 근거 부족을 뜻함).
† 잠정 점수: 이 관점은 재시도 1회 후에도 Judge 근거 기준(출처 계열 비중·찬반 근거 계열 수)에 미달해 해석이 제한된다(6장 참고).

# 1. 분석 배경
## 1.1 KV cache 병목
LLM은 토큰을 생성하면서 앞에서 계산한 Key·Value를 KV cache에 저장해 다시 쓰고, 그 크기는 문맥 길이와 동시 요청 수에 비례해 커진다. 두 기술의 원 논문도 이 메모리 병목을 출발점으로 삼는다 [1, p.1; 2, p.1]. Llama-3.1-8B(레이어 32, KV 헤드 8, 헤드 차원 128, FP16) 기준으로 토큰 하나에 128 KiB, 128K 토큰 요청 한 건에 16 GiB가 필요하다(조 계산, 설계서 A.1).
## 1.2 분석 질문과 가설
<!--w:2.0,14.0-->
| 구분 | 내용 |
|---|---|
| Q1 | 두 기술은 관점마다 어떤 근거로 어떻게 평가되는가 |
| Q2 | 관점끼리 어디서 일치하고 어디서 엇갈리며, 그 이유는 무엇인가 |
| Q3 | 워크로드(W1, W2)와 함께 쓰는 경우에 따라 평가 조건이 어떻게 달라지는가 |
| H1 | 기술 성숙도와 시장의 평가는 같은 방향이 아닐 수 있다(TRL×시장 격자로 판정) |
| H2 | 이해관계자 반응은 기술 자체보다 속한 생태계의 영향을 더 받을 수 있다 |
| H3 | 같은 도메인 안에서도 W1과 W2에 따라 적용 조건과 제약에 대한 평가가 달라질 수 있다 |
| H4 | 두 방식은 경쟁보다 보완 관계일 수 있다 |

# 2. 기술 선정
## 2.1 선정 방식과 기준
가이드의 두 방식 중 조가 직접 고르는 2안을 택했다. Doc Pool 후보 6개를 비교 공정성 35%, 근거 확보성 25%, 산업 연관성 25%, 최신성 15%로 채점하고, KV cache를 직접 다루지 않거나 적용 시점이 크게 다른 후보는 제외했다(설계서 A.2). 에이전트는 선정을 바꾸지 않고 원문 근거로 검증만 한다.
## 2.2 선정 결과와 사유
- **TurboQuant** (Google, SW): 후보 6개 가중합 SW 최고점(4.35). 재학습 없이 서빙 단계에서 KV를 양자화한다 [1, p.1]
- **ITME** (SK hynix, HW): 후보 6개 가중합 HW 최고점(4.50). vLLM 위의 서빙 계층 기술로 모델을 바꾸지 않고 KV 공간을 CXL-hybrid 메모리로 넓힌다 [2, p.1]
# 3. 기술 개요
## 3.1 TurboQuant
- **작동 원리**: TurboQuant은 입력 벡터를 무작위로 회전시켜 각 좌표가 베타 분포를 따르도록 유도하고, 고차원에서 좌표 간 독립성에 기반해 각 좌표별로 최적의 스칼라 양자화기를 적용하는 두 단계 방식으로 작동한다. 첫 단계에서는 평균제곱오차(MSE)에 최적화된 벡터 양자화를 수행하고, 두 번째 단계에서는 잔차에 1비트 양자화를 적용해 내적 왜곡을 줄인다 [1, p.1·2].
- **실험 조건**: KV cache 양자화 실험은 Llama-3.1-8B-Instruct 및 Ministral-7B-Instruct 모델을 사용하였으며, LongBench 데이터셋의 LongBench-E 서브셋을 통해 다양한 문맥 길이에 대해 평가하였다 [1, p.18·19].
- **보고된 성능**: TurboQuant은 LongBench 데이터셋에서 기존 KIVI 및 PolarQuant 방법보다 높은 평균 성능 점수를 기록하였으며, 스트리밍 생성 과정에서도 양자화를 적용하여 성능을 유지하였다 [1, p.18·19·20].
- **한계**: TurboQuant은 좌표별 독립성 가정과 베타 분포 모델링에 기반하므로, 입력 벡터가 이 가정을 크게 벗어나는 경우 최적의 왜곡률을 보장하지 못할 수 있다. 또한, 고차원에서의 무작위 회전 및 최적 스칼라 양자화 적용이 계산 비용과 구현 복잡도를 증가시킬 수 있다 [1, p.1·2].
## 3.2 ITME
- **작동 원리**: ITME는 대규모 언어 모델(LLM) 추론에서 키-값(KV) 캐시를 다중 계층 메모리 구조를 통해 효율적으로 관리한다. GPU 메모리, 호스트 메모리, NVMe SSD, 클러스터 공유 저장소 등 계층별 특성을 활용하여 예측 가능하고 대용량인 모델 가중치와 장기 컨텍스트 KV 캐시를 원격 확장 계층으로 오프로드함으로써 접근 지연을 숨긴다 [2, p.1·2·6].
- **실험 조건**: LLM 추론 실험은 Llama-3.1 8B 및 70B 모델을 사용하였으며, ShareGPT 데이터셋에서 128개의 동시 대화, 각 대화는 최소 2000 토큰, 최대 5턴으로 구성되었다 [2, p.10].
- **보고된 성능**: ITME는 Llama-3.1 8B 및 70B 모델에서 GPU 메모리 기반 재계산 대비 토큰 첫 생성 시간(TTFT)을 최대 약 2.5배까지 단축하였다 [2, p.9·10].
- **한계**: ITME는 NVMe SSD의 내부 아키텍처로 인해 대용량 비동기 쓰기 작업이 읽기 작업과 자원 경쟁을 일으켜 사전 로딩 성능 저하를 유발할 수 있다. 또한, 메타데이터 업데이트를 위한 하드웨어 수준의 잠금 메커니즘이 3클럭 사이클을 필요로 하여 집중적인 처리 시 메타데이터 처리량에 제한이 발생할 수 있다 [2, p.4·6].
# 4. 관점별 평가
## 4.0 평가 기준
기준마다 1~5점 인식 점수(공개 자료에 나타난 평가의 방향, 기술 품질 아님)를 C.6 Rubric 근거 조건으로 매긴다. 기술 고유 근거만 세고 같은 원 출처 계열은 하나로 센다. 근거 1계열 이하 기준은 판단 보류, 빠진 가중치가 50%를 넘으면 관점 전체를 판단 보류로 한다.
## 4.1 기술 성숙도(TRL)
<!--w:2.6,2.6,1.8,4.5,4.5-->
| 기술 | TRL 범위(추정) | 신뢰도 | 하한 근거 | 상한 근거 |
|---|---|---|---|---|
| TurboQuant | 4–5 | 높음 | [1, p.15; 5; 9] | [3; 7; 6] |
| ITME | 5–6 | 보통 | [2, p.8·10] | [2, p.8·10; 8] |

TRL은 공개 정보로 추정한 범위이다. 하한은 공개 근거로 확인된 가장 높은 단계, 상한은 발표·계획 같은 부분 신호로 보이는 단계이며, 양쪽 모두 개발사 외 독립 근거가 있으면 신뢰도 높음, 한쪽만 있으면 보통, 개발사 자료뿐이면 낮음이다(설계서 C.4).
TurboQuant은 NVIDIA A100 및 RTX 3090 GPU에서 실험적 검증과 오픈소스 구현을 통해 TRL 4 단계에 도달했으며, vLLM과의 통합 계획과 구글 공식 발표로 TRL 5 단계로 추정된다[1, p.15; 4; 5; 3; 6; 7]. 다만, 실제 대규모 워크로드에서 완전한 통합 및 성능 검증 정보는 공개되지 않았고, 공식 상용 배포는 계획 단계이다. ITME는 FPGA 프로토타입과 실제 GPU 및 CXL 하이브리드 메모리 환경에서 기능 및 성능 검증을 완료해 TRL 5 이상으로 확인되며, CXL 메모리 공유 프로토타입 개발 중으로 TRL 6 가능성도 보인다[2, p.2·8·10; 8].
## 4.2 시장성
<!--w:4.0,6.0,6.0-->
| 기준 (가중치) | TurboQuant | ITME |
|---|---|---|
| 시장 규모·성장 (25) | 4.00 [10; 11] | 판단 보류  |
| 상용화·채택 (30) | 4.00 [12; 13] | 판단 보류 [2, p.9] |
| 생태계 지원 (30) | 5.00 [1, p.16] | 판단 보류 [14] |
| 도입 비용 구조 (15) | 판단 보류  | 판단 보류  |
| **가중 평균** | **4.35†** (평가 가중치 85/100) | **판단 보류** |

TurboQuant은 구글 연구팀 주도로 오픈소스 프로젝트와 주요 추론 엔진에서 초기 통합과 검증이 진행 중이며, KV 캐시 메모리 6배 축소와 최대 8배 추론 속도 향상이 보고되었다[1, p.18; 10; 15; 16]. 일부 평가에서는 전체 메모리 사용량 감소가 제한적이고 기존 방법 대비 성능이 떨어진다는 의견도 있어 수요가 불확실하다[11]. ITME는 CXL 하이브리드 메모리와 다계층 메모리 관리 API를 활용해 대규모 KV 캐시 확장과 지연 민감 작업 배치를 지원하는 근거가 있으나, 시장 규모·성장과 도입 비용 구조에 관한 구체적 근거는 부족하다[2, p.2; 14; 17].
## 4.3 이해관계자
<!--w:4.0,6.0,6.0-->
| 집단 (각 25) | TurboQuant | ITME |
|---|---|---|
| (a) 클라우드·데이터센터 | 지지(5) [18; 10] | 판단 보류  |
| (b) GPU·메모리 벤더 | 지지(5) [19] | 지지(5) [20] |
| (c) 개발자 | 지지(5) [21; 22] | 지지(5) [23] |
| (d) 투자·분석·언론 | 중립·혼재(3) [24; 25] | 판단 보류  |
| **가중 평균** | **4.50†** | **5.00†** (2/4 집단) |

가중 평균은 판단 보류 기준을 뺀 평균이며, 괄호 안은 평균에 실제로 들어간 범위이다(예: 3/4 집단은 한 집단이 판단 보류). 개발사(TurboQuant는 Google, ITME는 SK hynix)의 발언·보도자료·원 논문은 점수에서 제외했고, 기술명을 언급하지 않는 회사 실적·주가 자료는 집단의 기술 평가로 세지 않았다.
TurboQuant은 클라우드·데이터센터 사업자와 GPU·메모리 벤더 집단에서 KV 캐시 압축과 메모리 비용 절감 효과에 대해 강한 지지를 받았으나, 개발자 커뮤니티에서는 품질 저하 우려도 일부 존재한다[18; 19; 21; 22]. 투자·분석·언론 집단은 긍정적 평가와 일부 중립적 시각을 보였다[25; 26]. ITME는 GPU·메모리 벤더와 개발자 커뮤니티에서 대규모 KV 캐시 확장과 AI 워크로드 지원 기술로 긍정적 평가를 받았으나, 클라우드·데이터센터 사업자와 투자·분석·언론 집단에서는 근거 부족으로 평가가 이루어지지 않았다[20; 23].
## 4.4 도메인 적합성(W1·W2)
<!--w:3.2,3.2,3.2,3.2,3.2-->
| 항목 (각 20) | TurboQuant W1 | TurboQuant W2 | ITME W1 | ITME W2 |
|---|---|---|---|---|
| 비용 | 5.00 [3; 27] | 5.00 [28; 24] | 5.00 [2, p.2; 23] | 5.00 [2, p.2; 23] |
| 지연 | 5.00 [29; 27] | 4.00 [29; 30] | 5.00 [2, p.2] | 5.00 [2, p.2] |
| 처리량·동시성 | 5.00 [3; 31] | 4.00 [28; 32] | 판단 보류 [2, p.11; 14] | 판단 보류 [2, p.11; 14] |
| 정확도 영향 | 5.00 [1, p.18; 29] | 5.00 [1, p.18; 29] | 판단 보류  | 판단 보류  |
| 통합 난이도 | 5.00 [33; 4] | 판단 보류 [4] | 판단 보류  | 판단 보류  |
| **가중 평균** | **4.78†** (9/10 항목) |  | **판단 보류** |  |

워크로드별 평균: TurboQuant W1 5.00, W2 4.50; ITME W1 판단 보류, W2 판단 보류. 가중 평균 행은 기술별 W1·W2 합산값이다(W1 열에 표기).
TurboQuant은 Llama-3.1-8B-Instruct 및 Ministral-7B-Instruct 모델을 대상으로 장문 시나리오에서 KV 캐시를 16비트에서 3비트로 압축해 최대 6배 메모리 절감과 8배 추론 속도 향상을 달성했으며, 재학습 없이 품질 저하 없는 결과를 보였다[1, p.18; 28; 29; 24; 34]. 다만 일부 워크로드에서 지연 증가와 처리량 감소가 보고되어 조건별 성능 차이가 있을 수 있다[30]. ITME는 LLM 데이터 유형을 지연 민감도와 용량에 따라 분류하고, PCIe Gen5 기반 CXL 하이브리드 메모리를 활용해 대용량 KV 캐시 확장과 지연 민감 작업의 고속 메모리 배치를 지원하며, API를 통해 효율적 데이터 이동과 프리페칭을 제공한다[2, p.2·11; 23; 17].

## 4.5 추가 관점(Orchestrator 동적 계획, 점수 없음)
- T05 research · TurboQuant: TurboQuant은 KV 캐시 메모리를 약 3비트 수준으로 극단적으로 압축하여 최대 6배까지 메모리 사용량을 줄이면서도 정확도 손실 없이 작동한다[27; 35].
- T05 research · ITME: KV 캐시 오프로딩 아키텍처는 중간 메모리 스테이징을 우회하여 GPU와 SSD 간의 직접적이고 고대역폭 데이터 전송을 가능하게 하여 누적 지연과 대역폭 병목 현상을 줄이고 운영 비용을 절감한다 [36].

# 5. 시사점
## 5.1 관점 간 일치·상충 표
<!--w:3.2,3.2,3.2,3.2,3.2-->
| 기술 | TRL(범위) | 시장성 | 이해관계자 | 도메인 |
|---|---|---|---|---|
| TurboQuant | 4–5 | 4.35† | 4.50† | 4.78† |
| ITME | 5–6 | 판단 보류 | 5.00† | 판단 보류 |

† 잠정 점수: 이 관점은 재시도 1회 후에도 Judge 근거 기준(출처 계열 비중·찬반 근거 계열 수)에 미달해 해석이 제한된다(6장 참고).
<!--w:3.0,6.0,2.5,4.5-->
| 기술 | 관점 쌍 | 점수 차 | 판정 |
|---|---|---|---|
| TurboQuant | 시장성 – 이해관계자 | 0.15 | 일치 |
| TurboQuant | 시장성 – 도메인 적합성 | 0.43 | 일치 |
| TurboQuant | 이해관계자 – 도메인 적합성 | 0.28 | 일치 |

판정 기준: 점수 차 2.0 이상 상충, 1.0 이상 2.0 미만 부분 상충, 1.0 미만 일치. 같은 기술 안에서만 비교하고 두 기술을 합치거나 순위를 매기지 않는다.
## 5.2 주요 상충 지점
- 상충 또는 부분 상충으로 판정된 관점 쌍이 없거나, 해설 문장이 Judge 근거 검사를 통과하지 못해 표의 판정만 싣는다.
## 5.3 가설 판정
<!--w:1.4,2.2,12.4-->
| 가설 | 판정 | 근거 |
|---|---|---|
| H1 | 부분 지지 | TurboQuant: TRL 4–5 (신뢰도 높음), 시장성 4.35 → 부분 괴리 (기대 선행); ITME: TRL 5–6 (신뢰도 보통), 시장성 판단 보류 → 판단 보류 (C.5 격자, 코드 계산) [2, p.8·10; 1, p.15; 3] |
| H2 | 기각 | TurboQuant: 기술 언급 24건, 생태계·전략 언급 6건 → 기각; ITME: 기술 언급 15건, 생태계·전략 언급 11건 → 기각 (개발사 발언 제외, C.7 기준, 코드 계산) [20; 37; 38; 18] |
| H3 | 판단 보류 | TurboQuant: W1 5.0, W2 4.5, 3점을 사이에 두고 갈리는 기준 없음 → 기각; ITME: W1·W2 점수를 낼 근거가 부족해 판단 보류 (평가 가능한 기술이 일부뿐이라 가설 전체는 판단 보류) (C.7 기준, 코드 계산) [2, p.2·11; 29] |
| H4 | 부분 지지 | TurboQuant은 도입 주체별 근거(adopters)가 존재하나 함께 쓰는 사례(co_use_ids)는 없고, ITME는 함께 쓰는 사례가 있으나 도입 주체별 근거는 없다. 따라서 두 기술 모두 한쪽만 근거가 있어 H4 가설에 부분적으로 부합한다. |

H1은 TRL 범위의 가운데 값과 시장성 점수로 설계서 C.5의 3×3 격자에서 코드로 판정했다: TurboQuant 부분 괴리 (기대 선행); ITME 판단 보류.
## 5.4 조건별 시사점(추천 아님)
- 긴 컨텍스트 LLM 추론 환경에서는 TurboQuant이 3비트 양자화로 KV 캐시 메모리를 최대 6배 줄이고 최대 8배 추론 속도 향상을 보고한 근거가 관련된다[1, p.18; 10; 16].
- CXL 하이브리드 메모리와 PCIe Gen5 인터페이스를 갖춘 하드웨어 환경에서는 ITME가 대규모 KV 캐시 확장과 지연 민감 작업의 고속 메모리 배치를 지원하는 근거가 관련된다[2, p.11; 14].
- 기존 GPU 보유 여부에 따라 TurboQuant은 NVIDIA A100, RTX 3090, H100 등에서 실험적 검증과 오픈소스 구현이 진행 중인 점이 관련된다[1, p.15; 4].
- 도입 주체가 클라우드·데이터센터 사업자나 GPU·메모리 벤더인 경우 TurboQuant은 KV 캐시 압축과 메모리 비용 절감 효과에 대해 강한 지지를 받았으나, ITME는 해당 집단에서 근거가 부족한 점이 관련된다[18; 19; 20].
# 6. 한계점
## 6.1 확증편향 방지 조치와 실행 결과
<!--w:2.2,2.9,1.9,4.2,1.4,1.4,2.0-->
| 관점 | Judge 근거·중립·다양성·완결 | 최대 계열 비중 | 찬반·범위 근거 계열 | 우열 어휘 | 재실행 | 결과 |
|---|---|---|---|---|---|---|
| TRL | 5/5/4/5 | 67% | TurboQuant 하·상한 3, ITME 하·상한 1 | 0 | 1 | 판정 불확실 |
| 시장성 | 5/5/4/4 | 100% | TurboQuant 찬7/반3, ITME 찬1/반0 | 0 | 1 | 판정 불확실 |
| 이해관계자 | 5/5/4/4 | 50% | TurboQuant 찬14/반2, ITME 찬2/반0 | 0 | 1 | 판정 불확실 |
| 도메인 적합성 | 5/5/4/4 | 67% | TurboQuant 찬18/반1, ITME 찬2/반0 | 0 | 1 | 판정 불확실 |

- 본문 인용 근거의 출처 구분: 벤더 23건, 제3자 28건, 학술 5건. 관점마다 지지·반대 질의를 짝지어 검색했고, 수집 단계에서 한 원 출처 계열이 웹 근거의 50%를 넘으면 잘라냈으며, 개발사 발언은 이해관계자 점수에서 뺐다. 다만 웹 근거가 1~2건뿐인 경우에는 잘라도 비중이 내려가지 않아 최대 비중이 50%를 넘었고(TRL 67%, 시장성 100%, 도메인 적합성 67%), Judge는 이를 기준 미달로 판정했다.
- 반대(한계·우려) 근거 0계열: 시장성 ITME, 이해관계자 ITME, 도메인 적합성 ITME. 관점마다 한계·비판 질의를 지지 질의와 같은 수 이상 검색했으나 기술명을 명시한 독립 반대 근거를 찾지 못했다. 반대 근거가 없다는 것은 반대 의견이 없다는 뜻이 아니라 공개 자료에서 확인되지 않았다는 뜻이며, 이 관점의 점수는 긍정 쪽으로 기울어 있을 수 있다.
- 4개 관점은 두 기술 모두 평가했으나 시장성 ITME, 도메인 적합성 ITME은(는) 기술명을 명시한 독립 근거가 2계열 미만이라 판단 보류로 남겼다. 점수를 억지로 채우지 않은 것은 근거 부족을 낮은 점수로 바꾸지 않는다는 채점 규칙(C.6)에 따른 것이다.
- Orchestrator가 SubTask 5개를 동적으로 계획했다(계획 출처 llm, 사용 Agent 5개, 미사용 Agent 2개).
- Judge 판정식은 설계서 D.5를 그대로 썼다. 기준에 못 미친 작업만 같은 담당 Agent가 1회 다시 실행했고, 미사용 Agent의 교차 검토와 Agent 0의 재시도 지시를 거쳤다. 재시도 후에도 미달이면 판정 불확실로 남겼다.
- 재시도 1회 후에도 미달(판정 불확실): T01(FAILED_AFTER_RETRY) 한 출처 계열 비중 67%(50% 초과); T02(FAILED_AFTER_RETRY) 한 출처 계열 비중 100%(50% 초과); T03(FAILED_AFTER_RETRY) itme: 찬성 2계열·반대 0계열(각 2 이상 필요); T04(FAILED_AFTER_RETRY) 한 출처 계열 비중 67%(50% 초과)
## 6.2 실행 개요와 보고서 품질 평가
- run_id `cdc64c3abebe`(LangSmith trace·결정 로그와 같은 키). Orchestrator가 실행 전에 SubTask 5개를 계획(계획 출처 llm)하고 `Send`로 그 수만큼 Worker를 생성했다. 미사용 Agent 2개(ecosystem_specialist, regulation_specialist)가 실패 작업의 교차 검토자이다.

<!--w:1.3,4.2,3.6,1.1,2.4,3.4-->
| 작업 | 관점 | 담당 Agent | 시도 | 결과 | 교차 검토자 |
|---|---|---|---|---|---|
| T01 | technology readiness | trl_specialist | 2 | 재시도 후 미달 | ecosystem_specialist, regulation_specialist |
| T02 | marketability | market_specialist | 2 | 재시도 후 미달 | ecosystem_specialist, regulation_specialist |
| T03 | stakeholder perspective | stakeholder_specialist | 2 | 재시도 후 미달 | ecosystem_specialist, regulation_specialist |
| T04 | domain applicability | domain_specialist | 2 | 재시도 후 미달 | ecosystem_specialist, regulation_specialist |
| T05 | research | research_generalist | 1 | 통과 | - |

- `worker_results` reducer가 task_id별로 최신 시도만 남겨 병합하고, 재시도는 같은 담당 Agent가 1회만 한다.
- 품질 평가 Loop: 미달 시 서술 문제는 보고서 재작성(1회), 근거 문제는 해당 작업 재시도로 되돌아가며, 재시도를 마친 작업은 한계로 기록한다(이번 재작성 0회).
- 품질 평가 결과: 결정적 검사 근거성 통과, 중립성 통과, 편향 통제 통과, 관점 커버리지 통과 / LLM(1~5) 근거성 5, 중립성 5, 편향 통제 4, 관점 커버리지 4 / 한도 소진 후 한계 기록·후처리로 종료. 미해결 근거 문제(작업 T02, T03, T04)는 재시도를 마친 작업이라 한계로 기록했다.

# REFERENCE
<!--fs:8-->
**논문**

- [1] Zandieh, A., Daliri, M., Hadian, M., & Mirrokni, V.(2025). TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate. arXiv, 2504.19874.
- [2] Jang, H., Min, Y., Kim, S., Ahn, T., Kim, H., Joo, Y., Kim, H., & Kim, J.(2026). ITME: Inference Tiered Memory Expansion with Disaggregated CXL-Hybrid Memories. arXiv, 2606.12556.

**웹페이지**

- [3] spheron.network(2026-09-22 접속). Google TurboQuant: 6x KV Cache Compression for LLM Inference. https://www.spheron.network/blog/google-turboquant-llm-compression-gpu-cloud
- [4] GitHub(2026-09-22 접속). GitHub - 0xSero/turboquant: TurboQuant: Near-optimal KV cache quantization for…. https://github.com/0xSero/turboquant
- [5] github.com(2026-09-22 접속). OmarHory/turboquant: Open-source implementation . https://github.com/OmarHory/turboquant
- [6] dev.to(2026-09-22 접속). TurboQuant: What Developers Need to Know About . https://dev.to/arshtechpro/turboquant-what-developers-need-to-know-about-googles-kv-cache-compression-eeg
- [7] tradingkey.com(2026-09-22 접속). What Is Google TurboQuant Compression Algorithm? How Does It Affect the AI…. https://tradingkey.com/analysis/stocks/us-stocks/261728257-what-is-google-turboquant-compression-algorithm-how-impact-ai-memory-chip-industry-tradingkey
- [8] arxiv.org(2026-09-22 접속). HyMCache: A KV Cache Frameworkfor Multi-Turn LLM . https://arxiv.org/html/2607.18141v2
- [9] GitHub(2026-09-22 접속). GitHub - hackimov/turboquant-kv: Open-source PyTorch implementation of Google…. https://github.com/hackimov/turboquant-kv
- [10] starkinsider.com(2026-03). Google’s TurboQuant: The Unsexy AI Breakthrough Worth Watching. https://www.starkinsider.com/2026/03/google-turboquant-llm-compression-less-memory.html
- [11] reddit.com(2026-09-22 접속). Will Google's TurboQuant technology save us? : r/StableDiffusion. https://www.reddit.com/r/StableDiffusion/comments/1s6t8yu/will_googles_turboquant_technology_save_us
- [12] arxiv.org(2026-09-22 접속). TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate. https://arxiv.org/html/2504.19874v1
- [13] youtube.com(2026-09-22 접속). Google's TurboQuant Memory Reduction Claim vs Reality. https://www.youtube.com/watch?v=haoAI2lIZ74
- [14] arxiv.org(2026-09-22 접속). ITME: Inference Tiered Memory Expansion with . https://arxiv.org/html/2606.12556v2
- [15] Renovate QR(2026-09-22 접속). Google TurboQuant: KV Cache Cut 6x, No Accuracy Loss. https://renovateqr.com/blog/google-turboquant-kv-cache-compression-2026
- [16] regolo.ai(2026-09-22 접속). Why TurboQuant matters for real-world LLM inference. https://regolo.ai/why-turboquant-matters-for-real-world-llm-inference
- [17] alphaXiv(2026-09-22 접속). ITME: Inference Tiered Memory Expansion with Disaggregated CXL-Hybrid Memories. https://www.alphaxiv.org/abs/2606.12556
- [18] substack.com(2026-09-22 접속). Deep·MU: TurboQuant Is Not Another DeepSeek Moment. https://fundaai.substack.com/p/deepmu-turboquant-is-not-another
- [19] NVIDIA Developer Forums(2026-09-22 접속). Why Turboquant saves DGX twice. https://forums.developer.nvidia.com/t/why-turboquant-saves-dgx-twice/364736
- [20] futurumgroup.com(2026-09-22 접속). SK Hynix ADR Issuance Strategy. https://futurumgroup.com/insights/will-sk-hynixs-record-265bn-adr-issuance-help-close-its-capex-intensity-gap
- [21] deepinfra.com(2026-09-22 접속). What Is Google TurboQuant and What Does It Mean . https://deepinfra.com/blog/google-turboquant
- [22] yage.ai(2026-09-22 접속). TurboQuant: Google Wants to Compress KV Cache Down to 3 Bits. https://yage.ai/share/turboquant-kv-cache-3-bit-en-20260325.html
- [23] Semantic Scholar(2026-09-22 접속). [PDF] ITME: Inference Tiered Memory Expansion with Disaggregated CXL-Hybrid…. https://www.semanticscholar.org/paper/ITME%3A-Inference-Tiered-Memory-Expansion-with-Jang-Min/935228feb1a5d966c6ece0532bf6e0136d614f7f
- [24] yahoo.com(2026-09-22 접속). What TurboQuant Actually Means for AI Memory Stocks. https://finance.yahoo.com/markets/stocks/articles/turboquant-actually-means-ai-memory-125500558.html
- [25] o-mega.ai(2026-09-22 접속). Google TurboQuant in August 2026: Where It Actually Runs. https://o-mega.ai/articles/google-turboquant-the-2026-llm-compression-guide
- [26] towardsai.net(2026-09-22 접속). Google's TurboQuant: The Compression Breakthrough . https://pub.towardsai.net/googles-turboquant-the-compression-breakthrough-that-could-reshape-llm-infrastructure-c09d68017567
- [27] vast.ai(2026-09-22 접속). TurboQuant Explained: How It Reduces LLM Memory by 5x and . https://vast.ai/article/turboquant-explained-llm-memory-inference
- [28] Everpure Blog(2026-09-22 접속). Up to 10X Faster KV Cache Restore: TurboQuant Meets FlashBlade. https://blog.everpuredata.com/purely-technical/up-to-10x-faster-kv-cache-restore-turboquant-meets-flashblade
- [29] Google(2026-09-22 접속). TurboQuant: Redefining AI efficiency with extreme . https://research.google/blog/turboquant-redefining-ai-efficiency-with-extreme-compression
- [30] vLLM(2026-05-11). A First Comprehensive Study of TurboQuant: Accuracy and . https://vllm.ai/blog/2026-05-11-turboquant
- [31] substack.com(2026-09-22 접속). TurboQuant: the new(?) and controversial ground breaking . https://boringbot.substack.com/p/turboquant-the-new-and-controversial
- [32] Intel(2026-09-22 접속). Enhancing long-context, high-concurrency LLM serving on a 32 GB . https://community.intel.com/t5/Blogs/Tech-Innovation/Data-Center/Enhancing-long-context-high-concurrency-LLM-serving-on-a-32-GB/post/1751209
- [33] tether.io(2026-09-22 접속). TurboQuant in QVAC SDK 0.12.0: KV-cache quantization for pr…. https://qvac.tether.io/blog/turboquant-in-qvac-sdk-0-12-0-kv-cache-quantization-for-production-local-ai
- [34] jangwook.net(2026-09-22 접속). Google TurboQuant: 3-Bit KV Cache With Zero Accuracy Loss. https://jangwook.net/en/blog/en/google-turboquant-kv-cache-3bit-compression
- [35] Medium(2026-09-22 접속). Implementing Google’s TurboQuant: KV Cache Compression and LLM Evaluation with…. https://medium.com/online-inference/implementing-googles-turboquant-kv-cache-compression-and-llm-evaluation-with-w-b-1403d460846b
- [36] neurips.cc(2026-09-22 접속). HiFC: High-efficiency Flash-based KV Cache Swapping for . https://papers.neurips.cc/paper_files/paper/2025/file/4431224d3762aa655f0aee4eaf04ff16-Paper-Conference.pdf
- [37] Global Equity Briefing(2026-09-22 접속). SK Hynix Deep Dive: 4x P/E is Too Cheap!. https://www.globalequitybriefing.com/p/sk-hynix-deep-dive-4x-pe-is-too-cheap
- [38] seagate.com(2026-09-22 접속). Enabling inference at massive scale with hybrid storage for . https://www.seagate.com/resources/enabling-inference-at-massive-scale-with-hybrid-storage-for-kv-cache-offloading

<!--fs:-->
