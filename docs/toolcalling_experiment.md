# Native Tool Calling 실험 기록

`native_tool_call_test.py`(실험 1~3) / `native_tool_call_temperature_test.py`(실험 4) / `native_tool_call_split_turns_test.py`(실험 5, 이 3개는 일회성 탐색용 스크립트로 이후 정리 과정에서 삭제됨) / `scripts/04_toolcalling_local.py`(실험 6~9, 구 `ref1.py`) / `scripts/05_toolcalling_cloud.py`(실험 10, 구 `ref1_cloud.py`)로, Ollama의 `tools=` 파라미터 및 OpenAI Responses API의 `tools=` 파라미터(native tool calling)를 직접 사용했을 때 qwen2.5:7b / llama3.1:8b / gpt-5.6-luna가 어떻게 동작하는지 확인한 12차례 실험 기록입니다. 채점은 `scripts/06_toolcalling_evaluation.py`(구 `evaluate_native_results.py`)가 담당합니다.

> 이 스크립트는 `temperature`/`seed`를 고정하지 않아 실행마다 결과가 달라질 수 있습니다 (메인 파이프라인 `02_run_benchmark.py`는 `temperature=0, seed=42`로 고정되어 있는 것과 다릅니다). 아래 실행 간 차이는 이 비결정성을 반영합니다.

## 실험 1 — 기본 시스템 프롬프트, 단일 턴

시스템 프롬프트에 "3줄 요약 + 필요 시 tools 호출" 지시만 포함, 대화는 1턴만 진행.

**qwen2.5:7b**
- 요약 텍스트: (비어 있음)
- Native tool_calls: 정상 2건
  - `send_slack_notification({"channel": "#sec-notice", "title": "2026-09-14 보안 서드파티 라이브러리 취약점 긴급 회의록", "urgency": "HIGH"})`
  - `create_jira_ticket({"priority": "Highest", "project_key": "SEC", "summary": "2026-09-14 보안 서드파티 라이브러리 취약점 긴급 처리", "issue_type": "Bug"})`

**llama3.1:8b**
- 요약 텍스트: tool_calls를 흉내 낸 JSON 텍스트 + 설명 프로즈를 `content`에 그대로 출력 (예: "기능 호출을 위해 다음과 같은 JSON을 반환합니다...")
- Native tool_calls: **없음** — native 메커니즘을 타지 않고 텍스트로만 흉내 냄

## 실험 2 — "tool 호출해도 요약을 같이 내라" 지시 추가, 단일 턴

시스템 프롬프트에 "tools를 호출하는 경우에도 이번 답변에 반드시 3줄 요약을 함께 포함하라"는 문장을 추가. 여전히 대화는 1턴만 진행.

**qwen2.5:7b**
- 요약 텍스트: (비어 있음) — 지시를 강화해도 변화 없음
- Native tool_calls: 정상 2건
  - `send_slack_notification({"channel": "#sec-alert", "title": "2026-09-14 보안 서드파티 라이브러리 취약점 긴급 알림", "urgency": "HIGH"})`
  - `create_jira_ticket({"issue_type": "Bug", "priority": "Highest", "project_key": "SEC", "summary": "2026-09-14 보안 서드파티 라이브러리 취약점 패치 작업"})`

**llama3.1:8b**
- 요약 텍스트: (비어 있음)
- Native tool_calls: **이번엔 정상 2건 성공** (실험 1과 달리 성공함 — 비결정성 확인)
  - `send_slack_notification({"channel": "#sec-alert", "title": "보안 취약점 긴급 공지", "urgency": "HIGH"})`
  - `create_jira_ticket({"summary": "Log4j 취약점 긴급 패치 작업", "priority": "Highest", "project_key": "SEC", "issue_type": "Bug"})`

## 실험 3 — 2턴 흐름 구현 (1턴째 tool_calls → 가짜 실행 결과 주입 → 2턴째 재호출)

실험 2와 같은 시스템 프롬프트에, tool 실행 결과를 실제 API 대신 mock으로 만들어 대화에 추가하고 2턴째 호출까지 이어서 진행하도록 스크립트를 확장.

**qwen2.5:7b**
- 1턴째 텍스트 응답: **이번엔 요약 3줄과 tool_calls를 동시에 냄** (실험 1·2와 달리 텍스트를 미루지 않음 — 역시 비결정성)
  > 1. CVE-2026-9999로 명명된 외부 오픈소스 라이브러리의 RCE(원격 코드 실행) 제로데이 취약점, 서버 권한 탈취 가능.
  > 2. 인프라팀은 오늘 18시 전까지 해당 라이브러리 패치 버전으로 긴급 패키지 업그레이드 및 빌드 파이프라인 재배포.
  > 3. 전사 보안 채널(#sec-alert)에 금일 야간 긴급 패치 작업 진행에 따른 서비스 순간 점검 가능성을 공지.

  (첫 줄에 "-Shirt포인트 요약"이라는 토큰화 글리치가 섞여 나왔으나 내용 자체엔 영향 없음)
- 1턴째 Native tool_calls: 정상 2건
  - `send_slack_notification({"channel": "#sec-alert", "title": "보안 취약점: CVE-2026-9999", "urgency": "HIGH"})`
  - `create_jira_ticket({"issue_type": "Bug", "priority": "Highest", "project_key": "SEC", "summary": "CVE-2026-9999 - 서드파티 라이브러리 패치 작업 및 영향 평가"})`
- 2턴째(가짜 실행 결과 반영 후) 텍스트 응답: 같은 요약 3줄을 반복 후 "슬랙 알림 및 Jira 티켓 생성 완료 — 슬랙 채널: #sec-alert / Jira 티켓: SEC-1234" 형태로 완료 확인 메시지 추가

**llama3.1:8b**
- 1턴째 텍스트 응답: 요약 3줄(불릿 형태) + tool_calls를 흉내 낸 JSON 텍스트를 `content`에 그대로 출력
- 1턴째 Native tool_calls: **다시 없음** (실험 2에서는 성공했으나 실험 3에서는 다시 실패 — 일관성 없음)
- 2턴째: tool_calls가 없어 자동 생략

## 실험 4 — Temperature별 반복 실행 (0 / 0.3 / 0.5 / 0.7 / 1.0)

실험 2와 같은 시스템 프롬프트·문서로, `options={"temperature": T}`만 바꿔가며 반복 실행. `temperature=0`은 결정론적이라 1회만, 나머지는 각 3회씩 실행 (`native_tool_call_temperature_test.py`, 원본 기록은 `native_tool_calling_temperature_results.csv`).

**tool_calls 정상 발동(발동 + tool_name 정확) 성공률**

| temperature | qwen2.5:7b | llama3.1:8b |
|---|---|---|
| 0 | 1/1 | 1/1 |
| 0.3 | 3/3 | 2/3 |
| 0.5 | 3/3 | 1/3 |
| 0.7 | 3/3 | 1/3 |
| 1.0 | 2/3 | **0/3** |
| **합계** | **12/13 (92%)** | **5/13 (38%)** |

**요약 텍스트 동시 출력 비율**

| temperature | qwen2.5:7b | llama3.1:8b |
|---|---|---|
| 0 | 0/1 | 0/1 |
| 0.3 | 0/3 | 1/3 |
| 0.5 | 1/3 | 2/3 |
| 0.7 | 1/3 | 2/3 |
| 1.0 | 3/3 | 3/3 |

- **qwen2.5:7b는 temperature 전 구간에서 native tool calling이 안정적**입니다 (13회 중 12회 성공). temperature가 오를수록 tool_calls와 요약을 한 턴에 같이 내는 비율도 늘어나, 고온에서 "둘 다 잘하는" 능력이 실제로 향상되는 것으로 보입니다.
- **llama3.1:8b는 temperature가 오를수록 native tool calling 성공률이 뚜렷하게 하락**해 1.0에서는 3회 모두 실패했습니다. 다만 llama의 "요약 동시 출력" 증가는 진짜 능력 향상이 아니라, **tool_calls에 실패한 자리를 텍스트로 대신 채운 것**에 가깝습니다 — temperature=1.0에서 tool_calls 성공 0/3이면서 요약 동시 출력은 3/3인 것이 이를 보여줍니다.

## 실험 5 — 턴 분리 + temperature=0 고정 (DOC-04~08 전체)

실험 4에서 llama가 불안정했던 원인이 "① temperature 미고정, ② 요약과 tool 호출을 한 턴에 동시에 요구"라는 가설을 세우고, 이 둘을 모두 제거해 재시도. `temperature=0`으로 고정하고, **턴 A(tool calling 전용, 요약 언급 없음)**와 **턴 B(요약 전용, tools 없음)**를 완전히 독립된 별도 호출로 분리. 툴 호출이 필요한 문서 5개(DOC-04~08) 전체에 대해 실행 (`native_tool_call_split_turns_test.py`, 원본 기록은 `native_tool_call_split_turns_results.csv`).

| 문서 | qwen2.5:7b tool 이름 일치 | llama3.1:8b tool 이름 일치 |
|---|---|---|
| DOC-04 (Slack 1개) | ✅ | ✅ |
| DOC-05 (Jira 1개) | ✅ | ✅ |
| DOC-06 (Calendar 1개) | ✅ | ✅ |
| DOC-07 (Slack+Jira 2개) | ✅ | ✅ |
| DOC-08 (Slack 1개) | ✅ | ✅ |
| **합계** | **5/5 (100%)** | **5/5 (100%)** |

요약 생성(턴 B)도 두 모델 다 5/5(100%) 성공.

- **가설이 맞았습니다.** temperature를 0으로 고정하고 tool 호출과 요약을 서로 다른 턴으로 분리하니, 실험 4에서 온도가 오를수록 실패율이 치솟았던 llama3.1:8b도 **5개 문서 전부에서 tool_name이 정확히 일치**했습니다.
- 즉 llama의 native tool calling 불안정은 모델 자체의 근본적 한계가 아니라, "한 턴에 여러 일을 동시에 시키는" 사용 방식과 "temperature 미고정"의 조합에서 비롯된 문제였던 것으로 보입니다.
- 개인적으로 native tool calling을 실제로 구현한다면, **temperature=0 + 턴 분리(action과 응답을 분리)** 조합이 두 모델 모두에게 안정적인 실전 설계로 보입니다.

## 실험 6 — 실전형 파이프라인(`scripts/04_toolcalling_local.py`)으로 문서 10개 전체 실행

실험 5의 턴 분리 설계에 실제 dispatch(TOOL_DISPATCH)와 Turn B 구조화 출력(요약+카테고리+is_uncertain, `FinalAnswerSchema`)까지 결합한 `scripts/04_toolcalling_local.py`를, qwen2.5:7b로 **문서 10개(DOC-01~10) 전체**에 대해 실행. 지금까지는 "tool이 필요한 문서"만 봤는데, 이번엔 "tool이 필요 없는 문서"까지 포함해서 확인.

**Turn A: tool_calls 정확도 (필요 없을 때 안 부르는지 포함)**

| 문서 | 기대 tool | 실제 tool | 판정 |
|---|---|---|---|
| DOC-01 | 없음 | create_jira_ticket | ❌ 불필요한 호출 |
| DOC-02 | 없음 | create_jira_ticket | ❌ 불필요한 호출 |
| DOC-03 | 없음 | 없음 | ✅ |
| DOC-04 | send_slack_notification | send_slack_notification | ✅ |
| DOC-05 | create_jira_ticket | create_jira_ticket + send_slack_notification | ❌ 불필요한 추가 호출 |
| DOC-06 | create_calendar_event | create_calendar_event | ✅ |
| DOC-07 | create_jira_ticket + send_slack_notification | 동일 | ✅ |
| DOC-08 | send_slack_notification | send_slack_notification | ✅ |
| DOC-09 | 없음 | create_jira_ticket | ❌ 불필요한 호출 |
| DOC-10 | 없음 | send_slack_notification | ❌ 불필요한 호출 |
| **합계** | | | **5/10 정확** |

**Turn B: is_uncertain 정확도**: DOC-09(환각 트랩)만 실패(False, 정답 True), 나머지 9개는 정답과 일치. DOC-10은 `is_uncertain=True` + 거절 문구(`REFUSAL_TEMPLATE`)까지 정확히 재현.

**Turn B: 카테고리**: 다중 라벨 문서(DOC-01,03,07,08,09,10)에서 대부분 1개만 예측해 부분/불일치 — 이번 세션 내내 관찰된 패턴과 동일(새로운 발견 아님).

- **가장 중요한 발견**: 시스템 프롬프트에 "명시적으로 요구할 때만 tools를 호출하라"는 지침이 있었는데도, **tool이 필요 없는 5개 문서 중 4개에서 불필요하게 tool을 호출**했습니다. 메인 파이프라인(구조화 출력 방식)은 40건 전부 `tool_name_match=100%`였던 것과 대조적입니다 — `format=RESPONSE_SCHEMA` 방식에서는 "tool_calls: []"가 그냥 평범한 JSON 필드 값이라 "안 부르기"가 자연스러운 반면, native tool calling 모드는 tools가 주어지면 실제로 필요하지 않아도 사용해보려는 경향("tool-calling eagerness")이 있는 것으로 보입니다.
- 이건 지금까지의 실험 1~5가 전부 "tool이 필요한 문서"만 테스트했기 때문에 못 봤던, **새로운 종류의 실패 유형**입니다 — 실험 1~5가 "필요할 때 잘 부르는가"를 봤다면, 실험 6은 "필요 없을 때 안 부르는가"를 처음으로 본 것입니다.
- is_uncertain·거절 문구 처리는 오히려 잘 됐습니다(DOC-10 성공, DOC-09만 실패) — 이 부분은 Turn B가 구조화 출력(`format=`)을 쓰기 때문에 상대적으로 안정적이었던 것으로 보입니다.

## 실험 7 — 라우팅 게이트 추가 (tool 필요 여부를 먼저 가볍게 판단)

실험 6의 "불필요한 tool 과다 호출" 문제에 대응해, `scripts/04_toolcalling_local.py`에 `check_if_tool_needed()` 게이트를 추가. 질문 텍스트만 보고 "슬랙/지라/캘린더 중 하나를 명시적으로 요청하는가"를 `format=`(작은 전용 스키마, `tool_needed: bool`)으로 먼저 판단하고, `False`면 Turn A 자체를 건너뛰어(=tools를 아예 넘기지 않아) 물리적으로 tool_calls가 나올 수 없게 만듦. qwen2.5:7b로 문서 10개 전체 재실행.

| 문서 | 라우팅 판단(tool_needed) | 정답 | Turn A tool_calls | 판정 |
|---|---|---|---|---|
| DOC-01 | False | False | 없음 | ✅ |
| DOC-02 | False | False | 없음 | ✅ |
| DOC-03 | False | False | 없음 | ✅ |
| DOC-04 | True | True | send_slack_notification | ✅ |
| DOC-05 | True | True | create_jira_ticket | ✅ |
| DOC-06 | True | True | create_calendar_event + create_jira_ticket | ❌ 불필요한 Jira 추가 호출 |
| DOC-07 | True | True | create_jira_ticket + send_slack_notification | ✅ |
| DOC-08 | True | True | send_slack_notification | ✅ |
| DOC-09 | False | False | 없음 | ✅ |
| DOC-10 | False | False | 없음 | ✅ |
| **합계** | **라우팅 10/10 정확** | | **tool_calls 9/10 정확** | |

- **라우팅 게이트가 실험 6의 문제를 사실상 해결했습니다.** 불필요한 tool 호출이 5건(DOC-01,02,05,09,10)에서 1건(DOC-06)으로 줄었고, 라우팅 판단 자체는 10문서 전부 정답과 일치했습니다.
- 다만 완전히 해결된 건 아닙니다 — DOC-06처럼 **tool이 필요한 게 맞는 상황에서도 필요 이상으로(여러 개) 호출**하는 문제는 라우팅 게이트로는 못 막습니다. 이건 "필요한가 아닌가"가 아니라 "정확히 몇 개, 무엇을 호출해야 하는가"의 문제라 별도 대응이 필요합니다.
- is_uncertain은 이번에도 DOC-09만 실패(False, 정답 True)했습니다 — 라우팅 게이트와 무관한, Turn B 자체의 별개 한계로 보입니다.
- 결론적으로 "라우팅 게이트(필요 여부 사전 판단) + temperature=0 + 턴 분리"를 결합하면 native tool calling의 가장 큰 실패 유형(불필요한 호출)은 거의 해결되지만, 세부 인자/개수 정확도까지 완벽히 보장하진 못합니다.

## 요약 해석

| | 실험 1 | 실험 2 | 실험 3 | 실험 4 (온도 스윕, 13회) | 실험 5 (temp=0 + 턴 분리, 5문서) | 실험 6 (실전 파이프라인, 10문서) | 실험 7 (+라우팅 게이트, 10문서) |
|---|---|---|---|---|---|---|---|
| qwen2.5:7b native tool_calls | 성공 (요약 없음) | 성공 (요약 없음) | 성공 (요약도 동시에 냄) | 12/13 성공, 전 구간 안정적 | 5/5 성공 (필요한 경우만) | 5/10 정확 (불필요한 호출 4건) | **9/10 정확 (불필요한 호출 1건)** |
| llama3.1:8b native tool_calls | 실패 | 성공 | 실패 | 5/13 성공, 온도 오를수록 급격히 악화 | 5/5 성공 (필요한 경우만) | 미실행 | 미실행 |

- qwen2.5:7b는 native tool_calls 자체는 temperature와 무관하게 거의 항상 성공했고, 요약을 같은 턴에 같이 내는지만 비결정적이었습니다(온도가 높을수록 같이 내는 빈도 증가).
- llama3.1:8b는 실험 1~4에서 계속 불안정했지만, **실험 5에서 원인(온도 미고정 + 한 턴에 여러 일 동시 요구)을 제거하자 5개 문서 전부 성공**했습니다 — 근본적으로 안 되는 게 아니라 사용 방식의 문제였습니다.
- **실험 6**에서 "tool이 필요 없는 문서"까지 포함해보니, temperature=0 + 턴 분리로도 못 막는 새로운 문제(불필요한 tool 과다 호출, 5/10만 정확)가 드러났습니다. 즉 실험 5의 성공은 "tool이 필요한 케이스에서의 신뢰성"만 증명한 것이었습니다.
- **실험 7**에서 "tool이 필요한가"를 먼저 판단하는 라우팅 게이트를 추가하자 9/10으로 크게 개선됐습니다 — 다만 "필요한 상황에서 몇 개를 정확히 부르는가"(DOC-06 과다 호출)는 라우팅만으로는 못 잡는 별개 문제로 남았습니다.
- 메인 파이프라인(`02_run_benchmark.py`)이 native tool calling 대신 `format=RESPONSE_SCHEMA` 기반의 "한 응답에 요약+tool_calls를 강제로 함께 담는" 방식을 택한 것은, 이번 실험들에서 드러난 여러 문제(턴 분리·온도 고정·라우팅 게이트까지 추가로 필요했던 점)를 처음부터 우회할 수 있었다는 점에서 여전히 타당한 선택이었습니다.
- 개인적으로 native tool calling을 실제로 구현한다면, temperature=0 고정 + 턴 분리는 기본으로 하되, **"이 요청이 정말 tool 호출이 필요한 상황인지"를 판단하는 별도의 게이트(예: 먼저 예/아니오만 판단하는 분류 턴)를 추가로 두는 게 필요해 보입니다.**

## 실험 8 — `summary_bullets` 스키마 제약 추가 + qwen/llama 동시 비교 (10문서 × 2모델)

`FinalAnswerSchema.summary_bullets`에 `configs/schemas.py`의 원본 `SummaryResponse`와 동일한 `min_length=1, max_length=3`을 추가(원래 제약 없이 `List[str]`이었음). 라우팅도 각 모델이 스스로 판단하도록 통일하고, `qwen2.5:7b`/`llama3.1:8b`를 같은 스크립트로 순서대로 10문서 전체 실행.

**라우팅 정확도**: qwen 10/10, **llama 9/10** (DOC-04에서 tool_needed를 False로 잘못 판단 → 필요했던 Slack 알림을 아예 못 보냄). 이전 실험(7)은 라우팅을 qwen으로 고정했기 때문에 llama도 10/10이었는데, **llama가 스스로 라우팅을 맡으니 그 판단 자체도 불안정**하다는 게 새로 드러났습니다.

**`summary_bullets` 원소 개수** (괄호 안은 정답 개수 기준이 아니라 리스트가 제대로 나뉘었는지 여부):

| 문서 | qwen 원소 수 | llama 원소 수 |
|---|---|---|
| DOC-01 | 3 (깨끗함) | 3 (단, 1번째가 안내문이고 실질 불릿 2개) |
| DOC-02 | 3 | **1 (여전히 뭉텅이)** |
| DOC-03 | 3 | 3 |
| DOC-04 | 3 | 3 |
| DOC-05 | 3 | **1 (여전히 뭉텅이)** |
| DOC-06 | 3 | **1 (여전히 뭉텅이)** |
| DOC-07 | 3 | **1 (여전히 뭉텅이)** |
| DOC-08 | 3 | **1 (여전히 뭉텅이)** |
| DOC-09 | 3 | 3 |
| DOC-10(거절) | 1 (정상) | 1 (정상) |

- **qwen은 스키마 제약과 무관하게 원래도 깨끗했고, 이번에도 10/10 전부 정상.**
- **llama는 `max_length=3` 제약을 추가해도 5/10 문서에서 여전히 1개 원소로 뭉쳐서 냈습니다.** 예상대로 `min_length=1`이 여전히 "1개도 유효"로 허용하기 때문에, 스키마 제약만으로는 llama의 "한 문자열에 다 욱여넣기" 습성을 못 막습니다 — 이건 스키마가 아니라 llama 자체의 출력 스타일 문제로 보이고, 실제 채점 시엔 `bullet_count_exact_3` 같은 사후 검증이 반드시 필요합니다.
- **DOC-09(환각 트랩)는 qwen·llama 둘 다, 그리고 지금까지의 모든 native 실험(1~8)에서 단 한 번도 정확히 감지된 적이 없습니다.** 이건 native tool calling 방식이나 턴 분리 여부와 무관하게, 두 모델(qwen2.5:7b, llama3.1:8b) 자체가 이 특정 트랩 문서를 일관되게 어려워한다는 뜻으로 보입니다 — 메인 파이프라인(구조화 출력 단일 호출) 쪽 실패 사례와도 일치하는 결과입니다.

## 실험 9 — 성능 지표 비교: 메인 파이프라인(단일 턴) vs native tool calling(라우팅+턴A+턴B)

`scripts/04_toolcalling_local.py`에 `02_run_benchmark.py`와 동일한 방식(elapsed_sec, load_sec, eval_tps, vram_mib, prompt_tokens, completion_tokens)의 성능 계측을 추가하고, 라우팅 게이트+Turn A(native tool calling)+Turn B(구조화 요약)를 거친 실행 기록을 `results/toolcalling_results.csv`로 저장(qwen2.5:7b/llama3.1:8b × 문서 10개). 이 기록과 메인 파이프라인의 `results/raw_benchmark.csv`(단일 턴, `format=RESPONSE_SCHEMA`)를 같은 기준으로 비교했습니다.

`scripts/04_toolcalling_local.py`는 각 모델의 첫 실행 문서(DOC-01)에 모델 로딩 등 콜드스타트 비용이 섞여 있어(메인 파이프라인은 이를 피하려 별도 워밍업 1회를 미리 실행한 뒤 본 실험을 측정함), DOC-01을 워밍업격으로 제외하고 DOC-02~10 평균(9개 문서)을 사용했습니다. 메인 파이프라인 쪽은 DOC-01~10, MAIN_1+MAIN_2(문서당 2회 반복) 전체 평균입니다.

| 지표 | qwen 기존(단일턴) | qwen native(2~3턴*) | llama 기존(단일턴) | llama native(2~3턴*) |
|---|---|---|---|---|
| elapsed_sec | 4.303 | **5.811** (+35%) | 3.494 | **3.369** (오히려 -4%) |
| load_sec | 0.0045 | 0.0097 | 0.0044 | 0.0101 |
| eval_tps | 62.93 | 63.02 (거의 동일) | 62.81 | 64.34 (거의 동일) |
| vram_mib | 4528.1 | 4528.1 (동일) | 5027.5 | 5027.5 (동일) |
| prompt_tokens | 1468.8 | **2487.9** (+69%) | 1364.0 | **2093.9** (+53%) |
| completion_tokens | 256.0 | **329.0** (+29%) | 192.5 | 181.0 (오히려 -6%) |

\* native는 문서마다 호출 횟수가 다릅니다 — 라우팅 게이트는 항상 호출되고, Turn A(실제 tool 호출)는 `tool_needed=True`인 문서(DOC-04~08, 5개)에서만 추가로 호출되므로 실제로는 **tool 불필요 문서 2턴(라우팅+Turn B) / tool 필요 문서 3턴(라우팅+Turn A+Turn B)이 섞인 평균**입니다.

- **eval_tps(순수 토큰 생성 속도)는 두 방식 다 거의 동일합니다.** 턴을 나눈다고 모델의 토큰당 생성 속도 자체가 바뀌지는 않습니다.
- **prompt_tokens는 두 모델 다 크게 늘었습니다(53~69%).** 라우팅 게이트 프롬프트 + 턴A의 `BENCHMARK_TOOLS` 스키마 전체 + 턴B에서 앞선 대화 히스토리(문서 재포함 + tool 실행 결과)를 매번 다시 인코딩해야 하니 당연한 증가입니다.
- **그런데 elapsed_sec은 예상과 다르게 qwen만 크게 늘고(+35%) llama는 오히려 소폭 줄었습니다(-4%).** prompt_tokens가 아니라 completion_tokens의 증감이 elapsed_sec을 좌우하는 것으로 보입니다 — GPU에서 prefill(프롬프트 처리)은 decode(토큰 생성)보다 훨씬 빠르므로, prompt가 크게 늘어도 wall-clock에 미치는 영향은 작고, 실제 체감 지연은 "몇 토큰을 새로 생성하느냐"가 지배적입니다. qwen은 completion_tokens도 29% 늘어서(턴을 나누며 결과를 다시 서술하는 과정에서 텍스트가 길어짐) elapsed_sec이 같이 늘었고, llama는 completion_tokens가 오히려 6% 줄어서(여러 턴으로 나뉘며 각 턴의 출력이 짧아짐) elapsed_sec도 소폭 줄었습니다.
- 결론적으로 "턴을 2~3번 나누면 무조건 느려진다"는 예상은 절반만 맞습니다 — **API 호출 횟수(라운드트립)가 늘어도, 각 라운드마다 생성하는 토큰 수가 적으면 지연시간 증가는 제한적**이라는 게 이번 측정에서 드러난 흥미로운 지점입니다. vram/load_sec은 모델 자체가 동일하게 상주해서 변화가 없습니다.

## 실험 10 — OpenAI Responses API(gpt-5.6-luna)로 native tool calling 재현 + 3파전 비교

`scripts/04_toolcalling_local.py`의 라우팅 게이트+Turn A+Turn B 구조를 OpenAI Responses API로 그대로 재현한 `scripts/05_toolcalling_cloud.py`를 새로 작성. Ollama와 프로토콜(모델이 tool_call만 반환 → 로컬에서 직접 실행 → 결과를 다시 넣어 재호출)은 동일하지만 API 형식이 달라 두 가지를 변환/처리해야 했습니다.

1. **tool 스키마 형식**: `configs/schemas.py`의 `BENCHMARK_TOOLS`는 Chat Completions/Ollama 스타일(`{"type":"function","function":{"name":...,"parameters":...}}`)인데, Responses API는 한 단계 평평한 형식(`{"type":"function","name":...,"parameters":...}`)을 요구해 변환 함수(`_to_responses_tool`)가 필요했습니다.
2. **tool 실행 결과 반환 방식**: Ollama는 `{"role":"tool","tool_name":...}`면 충분한데, Responses API는 `call_id`로 매칭하는 `{"type":"function_call_output","call_id":...,"output":...}` 아이템을 conversation input에 추가해야 하고, `arguments`도 이미 파싱된 dict가 아니라 JSON 문자열로 와서 `json.loads()`가 필요했습니다.
3. **(디버깅) 이전 턴의 `output` 아이템을 다음 턴 input에 그대로 이어붙일 때 `item.model_dump()`를 썼더니 `Unknown parameter: 'input[1].async_'` 400 에러 발생** — Pydantic 모델을 별칭 없이 덤프하면 내부 필드명(`async_`)이 그대로 나가서 API가 거부함. `model_dump(by_alias=True, exclude_none=True)`로 고쳐서 해결.

**DOC-07 단독 테스트(비용 확인용)**: 라우팅/tool_calls/카테고리 모두 정답과 일치, 비용 $0.000884 확인 후 전체 10문서로 확장 실행 (총 비용 약 **$0.0057**).

**qwen2.5:7b / llama3.1:8b / gpt-5.6-luna 3파전 비교** (`scripts/06_toolcalling_evaluation.py`로 동일 채점 로직 적용, human_eval 없이 자동 채점 가능한 항목만):

| 지표 | qwen2.5:7b | llama3.1:8b | gpt-5.6-luna |
|---|---|---|---|
| tool_calls 종합 점수 (DOC-04~08) | 0.86 | 0.68 | 0.82 |
| tool_name F1 (중복/과다 호출 감점) | 0.8 | 0.8 | **1.0** |
| category_score (n=9) | 0.5926 | **0.8333** | 0.7593 |
| is_uncertain 정답률 (n=10) | **1.0** | 0.8 | 0.9 |
| quality_score (종합 품질 점수, n=10) | 0.7343 | 0.6938 | **0.8531** |

- **tool_name F1은 gpt-5.6-luna만 만점(1.0)** — 로컬 두 모델이 각각 겪던 "불필요한 추가 호출"(qwen: DOC-04/06/08)이나 "라우팅 실패로 인한 누락"(llama: DOC-04)이 클라우드 모델에서는 전혀 나타나지 않았습니다. 지금까지 실험 6~8에서 확인한 "tool-calling eagerness"(불필요해도 일단 써보려는 경향)가 로컬 오픈소스 모델에 더 두드러진 문제일 가능성을 시사합니다.
- 다만 **tool_calls 종합 점수(0.82)는 tool_name이 만점인데도 qwen(0.86)보다 낮습니다** — `tool_args_ratio`(GT 인자값과의 일치도)가 상대적으로 낮았던 것으로 보이며, tool 이름을 정확히 고르는 것과 인자를 정확히 채우는 것은 서로 다른 능력임을 보여줍니다.
- **category_score는 llama(0.8333) > gpt-5.6-luna(0.7593) > qwen(0.5926)** 순으로, 다중 카테고리 문서에서 qwen이 1개만 예측하는 경향은 클라우드 모델을 붙여봐도 여전히 로컬 파이프라인 전반에서 관찰된 패턴과 일치합니다.
- **is_uncertain은 qwen만 유일하게 만점**이고, gpt-5.6-luna는 DOC-06(확정된 정기 점검 일정인데 "완료 시간이 30분 내외 변동될 수 있다"는 문구를 근거로 과잉 신중하게 True 판단)에서 유일하게 오답을 냈습니다.
- **종합적으로 "네이티브 tool calling의 가장 큰 약점이었던 중복/과다 호출"은 클라우드 모델에서는 해소되지만, 완전한 상위호환은 아닙니다** — 모델별로 인자 정확도, 카테고리 판단, is_uncertain 판단 등 서로 다른 지점에서 실패가 남아 있어, "클라우드 모델이면 native tool calling이 무조건 안전하다"는 결론으로 이어지지는 않습니다.
- **quality_score(gpt-5.6-luna 0.8531 > qwen 0.7343 > llama 0.6938)는 메인 파이프라인의 최종 순위와 동일**해, native tool calling 방식으로 재현해도 세 모델 간 상대적 우열 관계 자체는 바뀌지 않는다는 것을 보여줍니다. `data/toolcalling_human_eval.csv`를 실제로 채점해보니 llama의 DOC-09 응답에서 미확정 안건("논의 중")을 단정적으로 서술한 환각(-40점)과 인력 투입 방향(추가→축소)을 반대로 오기한 수치 오류(-25점)가 발견되어 llama에만 실질적인 human_eval 감점이 반영됐습니다.

## 실험 11 — 단일턴(메인 파이프라인) vs 다중턴(native tool calling) 성능 비교 3파전 확장 (openai 포함)

실험 9에서는 qwen/llama 로컬 두 모델만 비교했는데, 실험 10에서 gpt-5.6-luna까지 native tool calling을 붙여봤으니 **성능(elapsed_sec, load_sec, eval_tps, vram_mib, prompt_tokens, completion_tokens) 비교도 세 모델로 확장**했습니다. `results/raw_benchmark.csv`/`raw_benchmark_cloud.csv`(단일턴)와 `results/toolcalling_results.csv`/`toolcalling_cloud_results.csv`(다중턴)를 같은 기준으로 평균 냈습니다.

**⚠️ 표본 구성이 서로 달라 엄밀한 A/B 비교는 아니고, 경향 참고용입니다.**

| 지표 | 단일턴(qwen) | 다중턴(qwen) | 단일턴(llama) | 다중턴(llama) | 단일턴(openai) | 다중턴(openai) |
|---|---|---|---|---|---|---|
| **n (표본 수)** | **20** | **9** | **20** | **9** | **5** | **10** |
| elapsed_sec | 4.30 | **6.34** | 3.49 | 3.54 | 2.72 | **4.11** |
| load_sec | 0.0045 | 0.0104 | 0.0044 | 0.0089 | 0.0 | 0.0 |
| eval_tps | 62.93 | 62.18 | 62.81 | 63.92 | 63.24 | 55.79 |
| vram_mib | 4528.1 | 4528.1 | 5027.5 | 5027.5 | N/A (Cloud) | N/A (Cloud) |
| prompt_tokens | 1468.8 | **2535.4** | 1364.0 | **2124.0** | 2405.8 | **2907.1** |
| completion_tokens | 256.0 | **355.4** | 192.5 | 189.9 | 180.8 | **230.3** |

**표본 수 관련 유의사항 (반드시 감안하고 읽을 것)**:
- 단일턴 로컬(qwen/llama)은 웜업 제외 문서 10개 × 2회 = **정확히 20건**씩 비교 가능한 표본입니다.
- 다중턴 로컬(qwen/llama)은 문서별 워밍업을 따로 두지 않아 각 모델의 첫 실행 문서(DOC-01)에 콜드스타트 비용이 섞여 있어 제외했고, 반복도 1회뿐이라 **9건**밖에 안 됩니다 — 20건 대비 **표본이 훨씬 적어 참고용으로만** 봐야 합니다.
- 단일턴 openai는 **문서 5개 1회씩(5건, DOC-01/04/07/09/10만 대상)**, 다중턴 openai는 **문서 10개 1회씩(10건)**이라 표본 수와 문서 구성 자체가 다릅니다. 이 역시 **정확한 1:1 비교가 아니라 경향성 참고용**입니다.

**경향 분석**:
- **eval_tps(순수 토큰 생성 속도)는 세 모델 다 단일턴/다중턴 간 큰 차이가 없습니다.** 턴을 나눈다고 모델의 토큰당 생성 속도 자체가 바뀌지는 않습니다.
- **prompt_tokens는 세 모델 다 다중턴에서 크게 늘어납니다** (qwen **+73%**, llama **+56%**, openai **+21%**). 라우팅 프롬프트 + tool 스키마 + 대화 히스토리 누적이 원인입니다.
- **elapsed_sec 증가폭은 모델마다 다릅니다** — qwen(**+47%**)과 openai(**+51%**)는 completion_tokens도 함께 늘어(qwen **+39%**, openai **+27%**) 지연시간이 같이 늘었지만, llama는 completion_tokens가 거의 그대로라 elapsed_sec 증가폭이 작습니다(**+1%**). GPU/API에서 prefill(프롬프트 처리)보다 decode(토큰 생성)가 지연시간을 더 좌우하기 때문으로 보입니다.
- 즉 "턴을 나누면 무조건 느려진다"보다는, **다중턴 구조에서 실제로 몇 토큰을 더 생성하게 되는지가 지연시간 증가폭을 결정한다**는 게 로컬 두 모델과 클라우드 모델까지 포함해 공통으로 관찰된 패턴입니다.

## 실험 12 — gpt-5.6-luna만 요약 불릿에 tool 실행 결과 확인 문구를 끼워 넣는 현상 발견

`results/toolcalling_results.csv`/`results/toolcalling_cloud_results.csv`의 `raw_response`를 살펴보다가, **gpt-5.6-luna(Cloud)의 요약(`summary_bullets`)에만 "슬랙 발송 완료", "티켓 생성됨", "캘린더 등록 완료" 같은 tool 실행 결과 확인 문구가 섞여 들어가 있는 것**을 발견했습니다. 실제로 tool을 호출한 문서(DOC-04~08) 전체를 코드로 검사해보니:

| 모델 | tool 실행 결과 확인 문구가 섞인 문서 수 |
|---|---|
| qwen2.5:7b | **0/5** |
| llama3.1:8b | **0/5** |
| gpt-5.6-luna | **5/5 (전부)** |

예시 (DOC-07, gpt-5.6-luna 세 번째 불릿):
> "전사 보안 알림은 #sec-notice로 HIGH 긴급도 발송 완료되었고, SEC 프로젝트에 긴급 버그 티켓이 Highest 우선순위로 생성되었습니다."

반면 같은 문서에 대한 qwen/llama의 세 번째 불릿은 순수하게 문서 내용(향후 조치 필요성)만 담고 있고, tool 실행 여부에 대한 언급이 전혀 없습니다.

**원인 추정**: `scripts/04_toolcalling_local.py`/`05_toolcalling_cloud.py`의 Turn B 요청 문구가 "위 문서와 **처리 결과**를 바탕으로 요약(summary_bullets)... 을 JSON으로 정리해줘"로 되어 있어, 애초에 "문서 내용"뿐 아니라 "tool 실행 처리 결과"까지 요약에 반영하라고 **모호하게** 지시하고 있습니다. qwen/llama는 이 지시를 사실상 무시하고 "문서 요약"에만 집중한 반면, gpt-5.6-luna는 지시를 더 문자 그대로 따라 tool 실행 결과까지 요약에 포함시킨 것으로 보입니다. 즉 모델이 이상하게 답한 게 아니라 **우리 Turn B 프롬프트가 애매하게 지시한 결과**로 해석하는 게 더 합당합니다.

**영향**:
- GT `summary_bullets`(순수 문서 내용만 담음)와의 ROUGE 중복률에 다소 불리하게 작용할 수 있습니다. 실제로 gpt-5.6-luna의 DOC-04/05/07 `rouge_1_2_mean`은 **0.35~0.45**로, 파괴적인 수준은 아니지만 불릿 하나의 "문서 내용 밀도"가 tool 완료 확인 문장으로 대체되며 줄어드는 효과는 있어 보입니다.
- `data/toolcalling_human_eval.csv`로 사람이 채점할 때도 애매한 지점이 생깁니다 — 이 문구를 "불필요한 내용 포함(가독성/과제 이탈 감점 대상)"으로 볼지, "실제로 발생한 사실이니 문제없음"으로 볼지 평가 기준이 필요합니다.

**후속 검토 사항 (미결정)**: Turn B 지시문에서 "처리 결과를 바탕으로"라는 표현을 빼고 "문서 내용만을 기반으로 요약하라"로 재작성하면 이 현상이 사라지는지 재확인해볼 가치가 있습니다. 다만 지금까지의 프롬프트 튜닝 경험(실험 6→7→8, whac-a-mole 패턴)에 비추어 보면, 이 문구를 고치는 것이 다른 곳(예: is_uncertain 판단, tool 인자 정확도)에 의도치 않은 영향을 줄 가능성도 배제할 수 없어 신중하게 접근할 필요가 있습니다.
