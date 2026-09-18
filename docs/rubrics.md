# 1. 평가 구조 및 지표 조합
전체 평가 구조는 정량적 자동 검증 항목과 정성적 요약 품질 항목으로 구성됩니다.
```
[전체 종합 점수 (100점 만점)]
 ├── Categories (문서 분류 F1 Score) - 자동
 ├── Tool Calls (함수명 및 인자 정확도, 중복·과다 호출 감점 포함) - 자동
 ├── Is Uncertain (환각 트랩/범위 초과 감지) - 자동
 └── Summary Bullets (요약 품질 필드)
      ├── 키워드 매칭 (Keyword Match) ────── 40%
      ├── Kiwi ROUGE-1,2 Mean ───────────── 10%
      ├── 불릿 3개 준수 (Bullet Count) ───── 20%
      └── 인간 평가 감점 (Human Eval) ────── 30% (감점 방식)
```

# 2. 요약 품질(summary_bullets) 세부 산출 로직
요약 필드 내부 점수는 100점 만점으로 정규화된 4개 지표를 가중 합산하여 산출합니다.

지표 구성 및 비중
- 키워드 매칭 (40%): 하드 데이터(금액, 날짜, 조건 등) 필수 키워드 포함 비율 
- Kiwi ROUGE-1,2 Mean (10%): kiwipiepy 형태소 분리 기반 N-gram 단어 포섭율
- 인간 평가 감점 점수 (30%): 4가지 감점 체크리스트 기반 점수
- 불릿 3개 자동 검증 (20%): 파이썬 len(summary_bullets) == 3 만족 시 100점, 미달/초과 시 0점

**ROUGE 비중을 낮춘 이유**: ROUGE는 GT와 표현(단어·문장 구조)이 다르면 내용이 맞거나 오히려 더 충실해도 점수가 낮게 나오는 구조적 한계가 있다(패러프레이징에 취약). 실제로 DOC-01에서 GT보다 더 상세하고 정확한 요약이 문장 구조가 달라 ROUGE는 더 낮게 나온 사례가 확인됐다 — 즉 모델 간 상대 비교 용도로도 완전히 신뢰하기 어렵다. ROUGE가 노리던 "내용이 잘 전달됐는가"는 keyword_match(하드 데이터 포함 여부)와 human_eval(환각·사실 오류·언어·가독성 체크)이 더 직접적으로 담당하므로, ROUGE는 보조 지표로 비중을 25%→10%로 낮추고 그만큼을 keyword_match·human_eval·불릿 검증에 나눠 배분했다. 그럼에도 ROUGE 절대값 자체를 "품질 몇 점"으로 해석하지는 않는다.


# 3. 인간 평가 감점 체계 (Penalty Rubric)
인간 평가자는 점수를 직접 매기지 않고, 발견된 오류 항목에 체크(1)만 수행합니다. 기본 100점에서 차감되며 최저 점수는 0점 처리됩니다.

**검토 범위**: human_eval은 rubrics.json 구조상 summary_bullets 하위 지표로 반영되지만, 평가자는 summary_bullets 텍스트만이 아니라 **raw_response 전체(tool_calls 인자 포함)를 읽고 P1~P4를 체크**합니다. 예를 들어 Jira 티켓 생성 인자(issue_type, summary 등)에 중국어/영어가 섞여 나오는 경우도 P3(err_language) 대상입니다.

$$\text{Human Eval Score} = \max(0, 100 - \sum \text{Penalties})$$

| 감점 코드 | 감점 항목 | 차감 점수 | 평가 및 체크 기준 (1/0) |
| :--- | :--- | :--- | :--- |
| P1 | 치명적 환각 (err_hallucination) | -40점 | 원문에 전혀 없는 정보 창작 또는 정반대 사실 작성 |
| P2 | 수치/조건 오류 (err_fact_data) | -25점 | 금액, 날짜, 제출 기한, 서류명 등 핵심 데이터 오기 |
| P3 | 언어 오출력 (err_language) | -25점 | 한국어 지시를 무시하고 중국어/영어 등으로 응답 |
| P4 | 가독성/어색함 (err_readability) | -15점 | 문맥 단절, 비문, 동일 표현의 불필요한 반복 |

# 4. 문서 그룹별 가변 가중치 (Dynamic Weights)
문서의 평가 목적(type_group)에 맞춰 최상위 항목 간 가중치가 다르게 적용됩니다.

| 문서 유형 | 적용 문서 | 핵심 평가 요소 | 가중치 배분 |
| :--- | :--- | :--- | :--- |
| 일반 요약 (NORMAL) | DOC 01~03 | 요약문의 완성도 및 분류 | 요약 품질 (55%) + 카테고리 (25%) + 불확실성 플래그 (20%) |
| 툴 호출 (TOOL) | DOC 04~08 | 정확한 함수 이름 및 인자 전달 | 툴 호출 (50%) + 요약 품질 (30%) + 카테고리 (10%) + 불확실성 플래그 (10%) |
| 정보 불확실 (UNCERTAIN) | DOC 09 | 판단 불가 시 불확실성 감지 여부 | 불확실성 감지 (50%) + 요약 품질 (30%) + 카테고리 (20%) |
| 범위 초과 (OUT OF BOUNDS) | DOC 10 | 답변 거절 및 환각 방지 | 불확실성 감지 (50%) + 거절 요약문 일치도 (50%) |

# 5. 입력용 CSV 파일 구조 (human_eval.csv)
평가자는 엑셀에서 아래 템플릿의 err_* 컬럼에 오류 발생 시 1, 정상 시 0을 기입합니다. rep_id는 반복 회차(MAIN_1→1, MAIN_2→2)를 뜻하며, 회차별로 별도 행에 채점합니다.
```
model_name,doc_id,rep_id,err_hallucination,err_fact_data,err_language,err_readability,evaluator_note
qwen2.5:7b,DOC-01,1,0,0,0,0,정상 작성됨 (100점)
qwen2.5:7b,DOC-02,1,0,1,0,0,경조금 수치 오류 발생 (-25점 -> 75점)
qwen2.5:7b,DOC-05,1,0,0,1,0,Jira 인자(issue_type/summary)에 중국어 텍스트 혼입 -> 언어 오출력 (-25점 -> 75점)
```

# 6.사용 지표
- 키워드 매칭: Recall (포섭 비율)
- ROUGE-1, 2: F1-Score (기존 ROUGE-L과 동일)
- 카테고리: 단일 ➔ Exact Match, 다중 ➔ F1-Score
- Tool Name: Multiset(Counter) 기반 Precision/Recall/F1-Score
- Tool Args: Dict F1-Score (Key-Value 일치율)

**Tool Name을 F1-Score로 바꾼 이유**: 기존에는 `set(GT tool 이름) == set(LLM tool 이름)`으로만 비교해서, 같은 tool을 여러 번 중복 호출하거나(예: create_jira_ticket을 3번 호출) 요청하지 않은 tool을 추가로 호출해도 "이름 집합"만 같으면 만점 처리되는 사각지대가 있었다. 실제로는 이런 중복/과다 호출도 명백한 오류(불필요한 알림 반복 발송, 원치 않는 티켓 중복 생성 등)이므로, `Counter`로 호출 횟수까지 비교해 정확히 일치한 개수만 정답으로 인정하는 방식으로 변경했다. 중복·과다 호출은 Precision을, 누락은 Recall을 낮춰 자연스럽게 감점되고, 완전히 정확한 1회씩의 호출만 만점(F1=1.0)을 받는다.