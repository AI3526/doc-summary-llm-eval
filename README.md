# doc-summary-llm-eval

사내 한국어 문서 요약 + 업무 툴 호출(Slack/Jira/Calendar) 어시스턴트 용도로 로컬 LLM 2개(`qwen2.5:7b`, `llama3.1:8b`)를 직접 비교해 1개를 선정하고, Cloud API 모델(`gpt-5.6-luna`)과도 소규모로 비교합니다.

## 결과 요약

| 모델 | Quality Score | 카테고리 | ROUGE-1,2 | 평균 응답시간 | VRAM |
|---|---|---|---|---|---|
| **qwen2.5:7b (최종 선정)** | **0.712** | 0.593 | 0.410 | 4.09s | 4528 MiB |
| llama3.1:8b | 0.662 | 0.741 | 0.383 | 3.27s | 5027 MiB |
| gpt-5.6-luna (Cloud, 참고용) | 0.885 | 0.792 | 0.416 | 2.72s | N/A (Cloud) |

**최종 선정: `qwen2.5:7b`** — 1순위 우선순위 지표(Quality Score)에서 우위이고, 응답 시간도 허용 범위(4~7초) 이내입니다. Cloud가 정량 지표상 더 높지만, 사내 문서를 외부로 보내지 않는 로컬 실행을 우선하기로 한 결정에 따라 최종 운영 모델에서는 제외했습니다. 선정 과정과 대표 실패 사례 등 상세 근거는 [docs/results.md](docs/results.md)에 있습니다.

## 사전 준비물

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/) 설치 + 벤치마크 대상 모델 pull
  ```bash
  ollama pull qwen2.5:7b
  ollama pull llama3.1:8b
  ```
- NVIDIA GPU + 드라이버 (`nvidia-smi` 명령을 사용할 수 있어야 함)
- (선택) OpenAI API 키 — Cloud 비교(STEP7)를 재현할 때만 필요

## 설치

```bash
uv sync
```

Cloud 비교를 재현하려면 프로젝트 루트에 `.env` 파일을 만듭니다 (git에 커밋되지 않음):

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
```

## 실행 순서

파이프라인은 반드시 순서대로 실행해야 하며, **3번과 5번 사이에 사람이 직접 채점하는 단계**가 끼어 있어 전체를 한 번에 자동 실행할 수 없습니다.

| 순서 | 명령어 | 설명 | 산출물 |
|---|---|---|---|
| 1 | `uv run python scripts/00_env_check.py` | CLI(`ollama run`)·Python SDK 양쪽 호출 확인, GPU 정보 기록 | `results/env_check.json` |
| 2 | `uv run python scripts/01_metadata_check.py` | 모델 메타데이터(라이선스, 파라미터 크기, 컨텍스트 길이 등) 수집 | `results/model_metadata.json`, `results/model_metadata.md` |
| 3 | `uv run python scripts/02_run_benchmark.py` | 로컬 2개 모델 × 문서 10개 × 2회 + Cloud 1개 모델 × 문서 5개 × 1회 실행 | `results/raw_benchmark.csv`, `results/raw_benchmark_cloud.csv` |
| **4** | **`data/human_eval.csv` 직접 채점** | 각 응답의 raw_response를 읽고 환각/사실오류/언어오류/가독성을 `err_*` 컬럼에 1(발생)/0(없음)으로 기록 | — |
| 5 | `uv run python scripts/03_evaluation.py` | `data/rubrics.json` 채점 기준 + 4번의 사람 채점을 반영해 최종 점수 산출 | `results/evaluation_scores.csv`, `results/evaluation_scores_cloud.csv` |

- 로컬만 다시 실행: `uv run python scripts/02_run_benchmark.py --local-only`
- Cloud만 다시 실행(실제 과금 발생): `uv run python scripts/02_run_benchmark.py --cloud-only`
- 로컬 생성은 `temperature=0, seed=42`로 고정되어 있어 같은 프롬프트·설정이면 재실행해도 응답이 동일합니다. 즉 3번만 다시 돌려서 새 필드를 추가하는 경우, 4번(사람 채점)을 다시 할 필요는 없습니다.

## 프로젝트 구조

```
.
├── README.md                  # 이 파일 — 결과 요약 + 실행 방법
├── docs/
│   ├── assignment.md           # 과제 스펙 원문 (참고용)
│   ├── results.md              # STEP별 실제 진행 기록 + 최종 선정 근거 (상세 서술)
│   └── rubrics.md              # 채점 기준 설계 문서 (사람이 읽는 설명, 변경 이력 포함)
├── configs/
│   ├── prompt_templates.py    # 시스템/유저 프롬프트, 거절 문구 등 공용 상수
│   └── schemas.py             # 응답 Pydantic 스키마 (SummaryResponse), 툴 JSON 스키마
├── data/
│   ├── documents.json         # 평가용 문서 10개 + Ground Truth
│   ├── rubrics.json           # 채점 가중치 설정 (코드가 실제로 읽는 파일)
│   └── human_eval.csv         # 사람이 직접 채점하는 감점 체크리스트
├── scripts/
│   ├── 00_env_check.py        # STEP4: 실행 환경 확인 (CLI + Python 호출)
│   ├── 01_metadata_check.py   # STEP3: 모델 후보 메타데이터 조사
│   ├── 02_run_benchmark.py    # STEP6/7: 로컬 + Cloud 벤치마크 실행
│   └── 03_evaluation.py       # rubric 기반 채점 + 리포트 출력
└── results/                   # 각 스크립트 실행 시 갱신되는 산출물 (원본 데이터)
```

> `configs/schemas.py`, `configs/prompt_templates.py`에는 과제와 무관한 개인 native tool calling 실험용 스키마/프롬프트도 일부 함께 들어있습니다(`scripts/04~06_toolcalling_*.py`, `docs/toolcalling_experiment.md` 참고). 채점 파이프라인(00~03)과는 독립적으로 동작합니다.

## 평가 방법 요약

- **문서 10종**: 일반 요약(3) · 툴 호출(5, 단일/다중) · 환각 트랩(1) · 범위 밖 질문 거절(1) — 전체 목록은 [docs/results.md의 평가 문서 구성](docs/results.md)
- **자동 채점**: 카테고리 정확도(단일 라벨 완전 일치 / 다중 라벨 F1), 필수 키워드 Recall, Kiwi 형태소 기반 ROUGE-1·2, 불릿 3개 형식 준수, 툴 이름·인자 일치도
- **사람 채점(human_eval)**: 환각·사실 오류·언어 오출력·가독성 4개 항목을 응답 전체(요약 + 툴 호출 인자 포함)에서 체크해 감점
- 각 문서 유형(NORMAL/TOOL_CALLING/UNCERTAIN/OUT_OF_BOUNDS)별 가중치 배분 등 상세 설계는 [docs/rubrics.md](docs/rubrics.md) 참고

## 알려진 한계

- ROUGE-1,2는 패러프레이징에 취약해 내용이 같아도 표현이 다르면 낮게 나올 수 있습니다. 절대값이 아니라 두 로컬 모델 간 상대 비교 용도로만 해석합니다.
- Ollama는 `num_ctx`를 명시하지 않으면 런타임 컨텍스트 윈도우가 기본값 4096으로 동작합니다 (모델이 지원하는 최대 컨텍스트와 다름).
- Cloud 모델(`gpt-5.6-luna`)은 `temperature` 커스텀 값을 지원하지 않아 로컬(0, 결정론적)과 다른 조건(기본값)으로 호출되며, `reasoning effort` 등 로컬에 없는 설정도 적용됩니다. Cloud 비교 결과는 이 차이를 감안해서 해석해야 합니다.
- Cloud API의 예상 비용(`est_cost_usd`)은 코드로 계산한 추정치이며, OpenAI 대시보드의 실제 사용 내역과는 별도로 확인이 필요합니다.

## 부록: Native Tool Calling 실험 (개인 탐구, 과제와 무관)

메인 파이프라인(위 실행 순서 1~5)은 tool_calls를 응답 JSON의 한 필드로 강제하는 "시뮬레이션" 방식(`format=RESPONSE_SCHEMA`)을 씁니다. Ollama/OpenAI가 지원하는 실제 native tool calling(`tools=` 파라미터)을 쓰면 어떻게 달라지는지 별도로 탐구한 개인 실험이며, **채점 결과나 최종 모델 선정에는 반영되지 않습니다.**

**구조**: 라우팅 게이트(tool 필요 여부 판단) → Turn A(실제 `tools=` 호출) → Turn B(구조화 요약/카테고리/is_uncertain 응답) 3단계로 구성됩니다.

```bash
PYTHONPATH=. uv run python scripts/04_toolcalling_local.py      # qwen2.5:7b / llama3.1:8b (로컬)
PYTHONPATH=. uv run python scripts/05_toolcalling_cloud.py      # gpt-5.6-luna (Cloud)
PYTHONPATH=. uv run python scripts/06_toolcalling_evaluation.py # 03_evaluation.py의 채점 로직 재사용
```

| 파일 | 역할 |
|---|---|
| `scripts/04_toolcalling_local.py` | Ollama native tool calling 파이프라인 (라우팅+Turn A+Turn B) |
| `scripts/05_toolcalling_cloud.py` | 동일 구조를 OpenAI Responses API로 재현 |
| `scripts/06_toolcalling_evaluation.py` | 결과 CSV를 `03_evaluation.py`의 채점 함수(특히 중복/과다 호출을 감점하는 `calculate_tool_name_score`)로 재사용해 채점 |
| `configs/schemas.py`의 `FinalAnswerSchema`/`ToolNeedSchema` | Turn B 응답 스키마 / 라우팅 게이트 스키마 |
| `configs/prompt_templates.py`의 `TOOL_SYSTEM_PROMPT`/`TOOL_ROUTING_SYSTEM_PROMPT` | native tool calling 전용 프롬프트 |
| `results/toolcalling_*.csv` | 실행/채점 결과 (로컬·Cloud 각각) |
| `docs/toolcalling_experiment.md` | 12차례 실험 전체 기록 (실패 사례, 원인 분석, whac-a-mole 프롬프트 튜닝 과정 포함) |

**핵심 발견**:
- Native tool calling도 결국 여러 번 왕복(라우팅 → 호출 → 최종 응답)이 필요해, 단일 호출로 끝나는 메인 파이프라인 방식보다 구조가 복잡합니다.
- 로컬 모델은 불필요한 tool을 추가로 호출하거나(qwen: DOC-04/06/08에서 과다 호출), 라우팅 자체를 잘못 판단해 필요한 tool을 놓치는(llama: DOC-04) 등 "과다/누락 호출" 문제가 프롬프트를 아무리 다듬어도 한쪽을 고치면 다른 쪽에서 재발하는 패턴(whac-a-mole)을 보였습니다.
- gpt-5.6-luna(Cloud)는 중복/과다 호출 문제가 없었지만(tool_name F1 만점), 인자 정확도·is_uncertain 판단 등 다른 지점에서는 여전히 실패가 남아 있어 "클라우드면 무조건 안전하다"고 보기는 어렵습니다.
- 이런 불안정성이, 메인 파이프라인이 tool_calls를 구조화 출력의 한 필드로 강제하는 "시뮬레이션" 방식을 택한 것을 채점 재현성 관점에서
 뒷받침하는 근거가 됩니다.

qwen/llama/openai 3파전 채점 비교(tool_calls 점수, category_score, is_uncertain 정답률, quality_score)는 [docs/toolcalling_experiment.md의 실험 10](docs/toolcalling_experiment.md)을 참고하세요.

단일턴 vs 다중턴 성능(elapsed_sec, prompt/completion_tokens 등) 비교는 표본 구성이 서로 달라 [docs/toolcalling_experiment.md의 실험 11](docs/toolcalling_experiment.md)에 정리했습니다.

## 더 읽어보기

- [docs/assignment.md](docs/assignment.md) — 과제 스펙 원문
- [docs/results.md](docs/results.md) — STEP별 실제 진행 기록, 최종 선정 근거, 대표 실패 사례
- [docs/rubrics.md](docs/rubrics.md) — 채점 기준 설계와 변경 이력
- [docs/toolcalling_experiment.md](docs/toolcalling_experiment.md) — Native Tool Calling 실험 전체 기록 (개인 탐구, 과제와 무관)
