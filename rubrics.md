# 1. 평가 구조 및 지표 조합
전체 평가 구조는 정량적 자동 검증 항목과 정성적 요약 품질 항목으로 구성됩니다.

[전체 종합 점수 (100점 만점)]
 ├── Categories (문서 분류 F1 Score) - 자동
 ├── Tool Calls (함수명 및 인자 정확도) - 자동
 ├── Is Uncertain (환각 트랩/범위 초과 감지) - 자동
 └── Summary Bullets (요약 품질 필드)
      ├── 키워드 매칭 (Keyword Match) ────── 35%
      ├── Kiwi ROUGE-1,2 Mean ───────────── 25%
      ├── 불릿 3개 준수 (Bullet Count) ───── 15%
      └── 인간 평가 감점 (Human Eval) ────── 25% (감점 방식)

# 2. 요약 품질(summary_bullets) 세부 산출 로직
요약 필드 내부 점수는 100점 만점으로 정규화된 4개 지표를 가중 합산하여 산출합니다.

지표 구성 및 비중
- 키워드 매칭 (35%): 하드 데이터(금액, 날짜, 조건 등) 필수 키워드 포함 비율 
- Kiwi ROUGE-1,2 Mean (25%): kiwipiepy 형태소 분리 기반 N-gram 단어 포섭율
- 인간 평가 감점 점수 (25%): 4가지 감점 체크리스트 기반 점수
- 불릿 3개 자동 검증 (15%): 파이썬 len(summary_bullets) == 3 만족 시 100점, 미달/초과 시 0점


# 3. 인간 평가 감점 체계 (Penalty Rubric)
인간 평가자는 점수를 직접 매기지 않고, 발견된 오류 항목에 체크(1)만 수행합니다. 기본 100점에서 차감되며 최저 점수는 0점 처리됩니다.

$$\text{Human Eval Score} = \max(0, 100 - \sum \text{Penalties})$$

감점 코드감점 항목차감 점수평가 및 체크 기준 (1/0)
P1 | 치명적 환각 (err_hallucination) | -40점 | 원문에 전혀 없는 정보 창작 또는 정반대 사실 작성
P2 | 수치/조건 오류 (err_fact_data) | -25점 | 금액, 날짜, 제출 기한, 서류명 등 핵심 데이터 오기
P3 | 언어 오출력 (err_language) | -25점 | 한국어 지시를 무시하고 중국어/영어 등으로 응답 
P4 | 가독성/어색함 (err_readability) | -15점 | 문맥 단절, 비문, 동일 표현의 불필요한 반복

# 4. 문서 그룹별 가변 가중치 (Dynamic Weights)
문서의 평가 목적(type_group)에 맞춰 최상위 항목 간 가중치가 다르게 적용됩니다.

문서 유형 | 적용 문서 | 핵심 평가 요소 | 가중치 배분
일반 요약 (NORMAL) | DOC 01~03 | 요약문의 완성도 및 분류 | 요약 품질 (55%) + 카테고리 (25%) + 불확실성 플래그 (20%)
툴 호출 (TOOL) | DOC 04~08 | 정확한 함수 이름 및 인자 전달툴 호출 (50%) + 요약 품질 (30%) + 카테고리 (10%) + 불확실성 플래그 (10%)
정보 불확실 (UNCERTAIN) | DOC 09 | 판단 불가 시 불확실성 감지 여부 | 불확실성 감지 (50%) + 요약 품질 (30%) + 카테고리 (20%)
범위 초과 (OUT OF BOUNDS) | DOC 10 | 답변 거절 및 환각 방지 | 불확실성 감지 (50%) + 거절 요약문 일치도 (50%)

# 5. 입력용 CSV 파일 구조 (human_eval.csv)
평가자는 엑셀에서 아래 템플릿의 err_* 컬럼에 오류 발생 시 1, 정상 시 0을 기입합니다.
```
doc_id,model_name,err_hallucination,err_fact_data,err_language,err_readability,evaluator_note
DOC-01,Qwen2.5-7B,0,0,0,0,정상 작성됨 (100점)
DOC-02,Qwen2.5-7B,0,1,0,0,경조금 수치 오류 발생 (-25점 -> 75점)
DOC-04,Model_B,0,0,1,1,중국어 출력 및 가독성 불량 (-40점 -> 60점)
```

# 6.사용 지표
- 키워드 매칭: Recall (포섭 비율)
- ROUGE-1, 2: F1-Score (기존 ROUGE-L과 동일)
- 카테고리: 단일 ➔ Exact Match, 다중 ➔ F1-Score
- Tool Name: Exact Match (Pass/Fail)
- Tool Args: Dict F1-Score (Key-Value 일치율)