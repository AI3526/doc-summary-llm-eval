# 상세 진행 기록

원본 데이터(모든 실행 기록)는 `results/*.csv`, `*.json`에 그대로 있습니다. 아래는 그 데이터만으로는 알 수 없는, **왜 그렇게 결정했는지에 대한 서술**입니다 (과제 스펙 STEP1~8 순서를 따름 — 스펙 원문은 [assignment.md](assignment.md) 참고).

## 평가 문서 구성

| Doc ID | type | 평가 목표 및 특징 |
|---|---|---|
| DOC-01 | NORMAL | 쉬운 1, 2, 3 구조화 문서 요약 |
| DOC-02 | NORMAL | 4개 항목을 3줄로 압축하는 능력 |
| DOC-03 | NORMAL | 보통 난이도의 서술형 공지문 요약 |
| DOC-04 | TOOL_SINGLE | 보안 지침 요약 + Slack 툴 1개 |
| DOC-05 | TOOL_SINGLE | 장애 보고서 요약 + Jira 툴 1개 |
| DOC-06 | TOOL_SINGLE | 점검 일정 요약 + Calendar 툴 1개 |
| DOC-07 | TOOL_MULTI | 회의록 요약 + Slack + Jira (Multi-Tool 2개) |
| DOC-08 | TOOL_SINGLE | 복잡한 규정 + Slack 툴 1개 |
| DOC-09 | UNCERTAIN | 고난도 환각 트랩(과거 수치 vs 미정) 감지 (is_uncertain=True) |
| DOC-10 | OUT_OF_BOUNDS | 사내 업무 범위 밖 일반 질문 거절/예외 처리 |

## STEP 1. 문제 정의
- **사용자:** 사내 임직원(비개발/개발 직군 포함)이 사내 공지·보고서·회의록 등 한국어 문서를 조회하고 요약을 요청하는 상황을 가정
- **질문 유형:** (1) 일반 문서 요약, (2) 요약 후 슬랙 알림/지라 티켓/캘린더 등록 등 후속 업무 툴 호출, (3) 문서에 미확정 정보만 있는 경우 불확실성 명시, (4) 사내 문서 범위를 벗어난 질문에 대한 거절
- **중요 우선순위:**
  1. 한국어 품질 — 모든 사내 문서와 질문이 한국어이므로 한국어 이해·생성 품질이 최우선
  2. 데이터 보안 — 사내 문서(보안 지침, 장애 보고서 등 민감 정보 포함)를 외부 Cloud API로 보내지 않아도 되는 로컬 실행이 기본 운영 방식으로 바람직함
  3. 응답 속도 — 요약 후 즉시 알림을 보내는 흐름이므로 수 초 내 응답이 필요 (일반 응답 4~7초 수준까지는 허용)
- **GPU 환경:** 수업용 노트북의 단일 GPU 기준, 실측 VRAM 점유량은 7~8B Q4_K_M 양자화 모델 1개당 약 4.5~5GB 수준(`results/model_metadata_table.md` 참고)이며, 두 모델을 동시에 로드하면 VRAM 부족으로 먼저 로드된 모델이 자동 언로드되는 것을 확인함 → 로컬 실험은 모델을 한 번에 하나씩 순차 실행하는 방식으로 진행

## STEP 2. 모델 요구사항 정의
- **필수 통과 조건**
  - 실행 가능 여부: Ollama로 노트북 단일 GPU에서 정상 구동되는 7~8B급 모델 (Q4_K_M 양자화)
  - License: 사내 활용에 법적 제약이 없는 라이선스 (Apache 2.0, Llama 3.1 Community License 등)
  - 입력·출력 길이 수용 여부: 사내 문서(약 500~1,200자) + 시스템 프롬프트 + 질문을 충분히 수용
  - 최소 품질: `quality_score` 같은 단일 합산 점수에 임의의 컷라인을 긋지 않고, 아래 3개 항목별 조건으로 판정 (합산 점수는 ROUGE 등 구조적으로 1.0에 도달하기 어려운 지표가 섞여 있어 절대 기준으로 부적합하다고 판단함)
    1. 호출 성공률 100% (`call_success` 기준, `results/raw_benchmark.csv`)
    2. Pydantic 스키마 준수율 100% (`pydantic_valid` 기준)
    3. human_eval 치명적 환각(`err_hallucination`) 발생률 10% 미만 (`data/human_eval.csv` 기준)
- **확인할 정보 (실측)**

  | 항목 | qwen2.5:7b | llama3.1:8b |
  |---|---|---|
  | License | Apache License | LLAMA 3.1 Community License |
  | Parameter Size | 7.6B | 8.0B |
  | Quantization | Q4_K_M | Q4_K_M |
  | 모델 최대 지원 Context Length | 32,768 | 131,072 |
  | **실제 런타임 로드 Context Window** | **4,096 (기본값)** | **4,096 (기본값)** |
  | VRAM 실사용량 (로드 시) | 4528.1 MiB | 5027.5 MiB |

  > ⚠️ **주의(실험 중 발견한 사항):** 두 모델 모두 GGUF가 지원하는 최대 컨텍스트는 수만~10만 토큰 이상이지만, Ollama가 `num_ctx`를 별도 지정하지 않고 로드할 경우 **실제 런타임 컨텍스트 윈도우는 기본값인 4,096 토큰**으로 동작함을 `client.ps()`로 확인함. 현재 실험 문서들은 짧아 문제가 되지 않지만, 더 긴 문서를 다루려면 `options={"num_ctx": N}`을 명시적으로 지정해야 함.
  > (참고: `scripts/01_metadata_check.py`가 애초에 `info.get("model_info", ...)`로 잘못된 키를 조회해 Context Length가 항상 하드코딩된 추정치만 기록되던 버그를 발견해 `modelinfo`로 수정하고 재수집함.)

- **선호 우선순위:** ① Quality(`data/rubrics.json` 종합 `quality_score` — 카테고리, 키워드 매칭, Kiwi ROUGE-1·2, human_eval 감점, 불릿 형식, 툴 호출 정확도, 불확실성 판단을 문서 유형별로 가중 합산) > ② 응답 시간(elapsed_sec) > ③ VRAM 사용량 — Use Case상 정확도가 속도보다 중요하다고 판단
- **확정 시점:** STEP5에서 고정 질문 10개와 `data/rubrics.json` 채점 기준을 확정한 시점에 위 기준도 함께 확정함. 이후 인간 검증(human_eval) 도입 및 ROUGE 비중 조정 등 rubric을 개선하면서 최종 확정 (자세한 변경 이력은 `rubrics.md` 참고)

## STEP 3. 후보 모델 탐색
- **선정 후보:** `qwen2.5:7b` (Alibaba, Apache 2.0), `llama3.1:8b` (Meta, Llama 3.1 Community License) — 서로 다른 아키텍처(Qwen2 vs Llama)를 사용하는 별도 모델로, 동일 모델의 양자화 버전 비교가 아님
- **선정 이유:**
  - 둘 다 다국어(한국어 포함) 성능을 공식 지원하며 Ollama 공식 라이브러리에 정식 태그로 등록되어 재현성 확보가 쉬움
  - 7~8B 파라미터대라 수업용 노트북 GPU(VRAM 실측 4.5~5GB/모델) 안에서 단일 모델 기준 여유 있게 구동 가능
  - Qwen2.5는 중국어권 모델 중 한국어 가독성이 상대적으로 좋다고 알려져 있고, Llama3.1은 커뮤니티 자료·벤치마크가 풍부해 비교 기준으로 삼기 적합
- **Model Card / License 원문:**
  - Qwen2.5-7B-Instruct: https://huggingface.co/Qwen/Qwen2.5-7B-Instruct
  - Llama-3.1-8B-Instruct: https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct
- **실행 태그/식별값:** `results/metadata.json`, `results/model_metadata_table.md`에 태그명, digest 대응 quantization_level, 실측 context length·VRAM을 기록

## STEP 8. 최종 모델 선정과 발표

### 필수 통과 조건 확인
로컬 모델 2개 × 질문 10개 × 2회 = 모델당 20회 본실험(워밍업 별도) 완료, human_eval도 DOC-10을 제외한 18건씩 전부 채점 완료.

| 조건 | qwen2.5:7b | llama3.1:8b | 판정 |
|---|---|---|---|
| ① 호출 성공률 100% | 20/20 (100%) | 20/20 (100%) | 둘 다 통과 |
| ② Pydantic 스키마 준수율 100% | 20/20 (100%) | 20/20 (100%) | 둘 다 통과 |
| ③ err_hallucination 발생률 10% 미만 | 0/18 (0%) | 1/18 (5.6%) | 둘 다 통과 |

두 후보 모두 필수 조건을 통과하여, 아래 선호 우선순위로 선정합니다.

### 선호 우선순위 비교 (① Quality > ② 응답 시간 > ③ VRAM)

| 항목 | qwen2.5:7b | llama3.1:8b | 우위 |
|---|---|---|---|
| 종합 Quality Score (n=20, human_eval 반영) | **0.712** | 0.6624 | qwen |
| 카테고리 점수 (n=18) | 0.5926 | **0.7408** | llama |
| Kiwi ROUGE-1,2 Mean (n=18) | **0.4095** | 0.3829 | qwen |
| 평균 응답 시간 | 4.09초 | **3.27초** | llama (단, 둘 다 STEP1의 허용 범위 4~7초 이내) |
| VRAM 사용량 | **4528.1 MiB** | 5027.5 MiB | qwen |
| 평균 생성 속도 | 66.2 tok/s | 63.18 tok/s | qwen |

1순위 지표인 Quality Score에서 qwen2.5:7b가 우위이므로 **qwen2.5:7b를 최종 로컬 모델로 선정**합니다. 응답 시간은 llama3.1:8b가 다소 빠르지만 두 모델 모두 STEP1에서 정한 허용 범위(4~7초) 안이라 우선순위 판정에 영향을 주지 않으며, VRAM도 qwen이 더 가벼워 1순위 판정과 상충하지 않습니다.

### 대표 실패 사례
- **qwen2.5:7b**: DOC-01에서 "둘째 이상 출산 축하금 100만 원" 조건을 누락/오기 (human_eval 수치 오류 -25점), DOC-05에서 Jira 티켓 인자(`issue_type`, `summary`)에 중국어 텍스트가 섞여 출력됨 (언어 오출력 -25점).
- **llama3.1:8b**: DOC-09(환각 트랩 문서)에서 미확정 안건을 확정된 사실처럼 단정적으로 서술하고 인원 추가 계획을 축소로 잘못 기술 (환각 -40점, 수치 오류 -25점).
- **공통 실패 — DOC-10(질문-문서 완전 불일치)**: 문서와 전혀 무관한 질문(Python GIL 설명 요청)에 대해 두 모델 다 정해진 거절 절차를 제대로 따르지 못함. qwen은 `is_uncertain=true`는 맞혔으나 정해진 거절 문구 대신 무관한 문서를 요약해버렸고, llama는 `is_uncertain=false`로 두고 질문을 무시한 채 문서를 3줄 요약함. 두 모델 다 이 케이스에서 quality_score 0.5(is_uncertain 판정만 부분 반영)에 그침 — 로컬 모델 도입 시 "질문-문서 완전 불일치" 케이스에 대한 별도 안전장치(예: 사전 관련성 필터)가 필요하다는 운영 시사점으로 기록.

### Cloud API(gpt-5.6-luna) 비교 결과와 최종 운영 권고
- 공통 질문 5개(DOC-01, 04, 07, 09, 10) 각 1회 실행 결과: 호출 성공 5/5, human_eval 채점 완료 4/4(DOC-10 제외), 종합 Quality Score **0.8845**로 로컬 두 모델보다 높음. DOC-10 거절 문구도 유일하게 정확히 재현함.
- **단, 동일 조건 비교가 아닙니다.** gpt-5.6-luna는 `temperature` 커스텀 값을 지원하지 않아 로컬(0, 결정론적)과 달리 기본값(1)으로 호출되었고, `reasoning effort=none` 등 로컬에는 없는 생성 설정이 적용됩니다. 따라서 이 품질 격차를 "Cloud 모델이 절대적으로 더 우수하다"로 단순 해석하지 않습니다.
- **최종 운영 권고:** Cloud가 정량 지표상 더 높은 품질을 보였음에도, STEP1에서 정한 최우선 순위(사내 민감 문서를 외부로 전송하지 않는 로컬 실행)에 따라 **실제 운영 모델은 qwen2.5:7b(로컬)로 선정**합니다. Cloud API는 비용이 매우 낮고(5건 합산 약 $0.002) 품질도 우수하므로, 로컬 모델이 처리하지 못하는 고난도 케이스(예: DOC-10류 질문-문서 완전 불일치)에 한해 보조적으로 활용하는 하이브리드 운영을 검토 사항으로 남깁니다.
