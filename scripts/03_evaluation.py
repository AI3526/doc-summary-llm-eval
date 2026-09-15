import csv
import json
import os
from pathlib import Path

from rouge_score import rouge_scorer
from configs.schemas import SummaryResponse

# 1. 경로 정의
BENCHMARK_CSV_PATH = "results/raw_benchmark.csv"
DOCUMENTS_PATH = "data/documents.json"
RUBRICS_PATH = "data/rubrics.json"
EVALUATION_CSV_PATH = "results/evaluation_scores.csv"


def load_json(path: str) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_rubric_group(doc_id: str, rubrics: dict) -> tuple[str, dict]:
    """doc_id에 적절한 루브릭 그룹 반환"""
    num = int(doc_id.split("-")[1])
    if 1 <= num <= 3:
        key = "DOC-01_to_03"
    elif 4 <= num <= 8:
        key = "DOC-04_to_08"
    elif num == 9:
        key = "DOC-09"
    elif num == 10:
        key = "DOC-10"
    else:
        key = "DOC-01_to_03"

    rule = rubrics.get(key, {})
    return rule.get("type_group", "NORMAL"), rule


def calculate_category_f1(pred_categories: list[str], gt_category: str | list[str]) -> float:
    """GT와 예측 카테고리 간의 F1-Score 계산 (단일/다중 및 부분 일치 처리)"""
    if not pred_categories:
        return 0.0

    # GT가 단일 문자열(str)로 입력된 경우 리스트 형태로 변환
    if isinstance(gt_category, str):
        gt_set = set([gt_category]) if gt_category else set()
    else:
        gt_set = set(gt_category)

    pred_set = set(pred_categories)

    if not gt_set or not pred_set:
        return 0.0

    # 교집합 계산
    intersection = gt_set.intersection(pred_set)
    if not intersection:
        return 0.0

    precision = len(intersection) / len(pred_set)
    recall = len(intersection) / len(gt_set)

    f1 = 2 * (precision * recall) / (precision + recall)
    return round(f1, 4)


def calculate_rouge_l(candidate_bullets: list[str], reference_bullets: list[str]) -> float:
    """LLM 요약문 불릿 목록과 Ground Truth 불릿 목록 간의 ROUGE-L F1 계산"""
    if not candidate_bullets or not reference_bullets:
        return 0.0

    cand_text = " ".join(candidate_bullets)
    ref_text = " ".join(reference_bullets)

    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=False)
    scores = scorer.score(ref_text, cand_text)

    return round(scores["rougeL"].fmeasure, 4)


def evaluate_single_run(
    raw_response: str, doc_id: str, doc_info: dict, rubrics: dict
) -> dict:
    """Ground Truth 기반 정량 채점 (카테고리 F1 + ROUGE-L)"""
    type_group, rubric_rule = get_rubric_group(doc_id, rubrics)
    scoring_logic = rubric_rule.get("scoring_logic", {})
    gt = doc_info.get("ground_truth", {})

    parsed_res: SummaryResponse | None = None
    is_parsed = False
    try:
        parsed_res = SummaryResponse.model_validate_json(raw_response)
        is_parsed = True
    except Exception:
        parsed_res = None

    scores = {}

    # 공통 데이터 추출
    llm_bullets = parsed_res.summary_bullets if (is_parsed and parsed_res) else []
    pred_cats = parsed_res.categories if (is_parsed and parsed_res and parsed_res.categories) else []
    gt_cat = gt.get("category", "")
    gt_bullets = gt.get("summary_bullets", [])

    # 1. ROUGE-L F1 계산
    rouge_l_score = calculate_rouge_l(llm_bullets, gt_bullets)

    # 2. 카테고리 F1 점수 산출 (0.0 ~ 1.0)
    cat_f1_score = calculate_category_f1(pred_cats, gt_cat) if is_parsed else 0.0

    # 3. 그룹별 루브릭 채점
    if type_group == "NORMAL":
        # Bullet Count (0.3)
        b_w = scoring_logic.get("bullet_count", {}).get("weight", 0.3)
        scores["bullet_count"] = (1.0 if is_parsed and len(llm_bullets) == 3 else 0.0) * b_w

        # Category Match with F1-Score (0.3)
        c_w = scoring_logic.get("category", {}).get("weight", 0.3)
        scores["category"] = round(cat_f1_score * c_w, 4)

        # Keywords & Content Quality using ROUGE-L + Must Keywords (0.4)
        k_w = scoring_logic.get("keywords", {}).get("weight", 0.4)
        must_keywords = gt.get("must_include_keywords", [])
        llm_full_text = " ".join(llm_bullets)
        keyword_pass = all(kw in llm_full_text for kw in must_keywords) if must_keywords else True
        
        quality_content_score = (0.5 * (1.0 if keyword_pass else 0.0)) + (0.5 * rouge_l_score)
        scores["keywords_and_rouge"] = round(quality_content_score * k_w, 4)

    elif type_group == "TOOL_CALLING":
        b_w = scoring_logic.get("bullet_count", {}).get("weight", 0.2)
        scores["bullet_count"] = (1.0 if is_parsed and len(llm_bullets) == 3 else 0.0) * b_w

        c_w = scoring_logic.get("category", {}).get("weight", 0.2)
        scores["category"] = round(cat_f1_score * c_w, 4)

        tn_w = scoring_logic.get("tool_name", {}).get("weight", 0.2)
        ta_w = scoring_logic.get("tool_args", {}).get("weight", 0.4)

        gt_tools = gt.get("expected_tool_calls", [])
        has_tools = is_parsed and parsed_res.tool_calls and len(parsed_res.tool_calls) > 0

        if not gt_tools:
            scores["tool_name"] = (1.0 if not has_tools else 0.0) * tn_w
            scores["tool_args"] = (1.0 if not has_tools else 0.0) * ta_w
        else:
            if has_tools:
                gt_tool_names = [t.get("tool_name") for t in gt_tools]
                llm_tool_names = [tc.tool_name for tc in parsed_res.tool_calls]
                name_match = set(gt_tool_names) == set(llm_tool_names)
                scores["tool_name"] = (1.0 if name_match else 0.0) * tn_w

                args_valid = all(len(tc.arguments) > 0 for tc in parsed_res.tool_calls)
                scores["tool_args"] = (1.0 if args_valid else 0.0) * ta_w
            else:
                scores["tool_name"] = 0.0
                scores["tool_args"] = 0.0

    elif type_group == "UNCERTAIN":
        b_w = scoring_logic.get("bullet_count", {}).get("weight", 0.15)
        scores["bullet_count"] = (1.0 if is_parsed and len(llm_bullets) == 3 else 0.0) * b_w

        c_w = scoring_logic.get("category", {}).get("weight", 0.15)
        scores["category"] = round(cat_f1_score * c_w, 4)

        u_w = scoring_logic.get("is_uncertain_detected", {}).get("weight", 0.35)
        u_match = is_parsed and parsed_res.is_uncertain == gt.get("is_uncertain", False)
        scores["is_uncertain_detected"] = (1.0 if u_match else 0.0) * u_w

        h_w = scoring_logic.get("no_hallucinated_facts", {}).get("weight", 0.35)
        must_keywords = gt.get("must_include_keywords", [])
        llm_full_text = " ".join(llm_bullets)
        keyword_pass = all(kw in llm_full_text for kw in must_keywords) if must_keywords else True
        scores["no_hallucinated_facts"] = (1.0 if keyword_pass else 0.0) * h_w

    elif type_group == "OUT_OF_BOUNDS":
        r_w = scoring_logic.get("refusal_success", {}).get("weight", 1.0)
        refusal_ok = False
        if is_parsed:
            if parsed_res.is_uncertain is True or any(
                "찾을 수 없습니다" in b for b in llm_bullets
            ):
                refusal_ok = True
        scores["refusal_success"] = (1.0 if refusal_ok else 0.0) * r_w

    total_score = sum(scores.values())

    return {
        "type_group": type_group,
        "total_score": round(total_score, 4),
        "category_f1": cat_f1_score,
        "rouge_l_f1": rouge_l_score,
        "detail_scores": json.dumps(scores, ensure_ascii=False),
        "is_parsed": is_parsed,
    }


def main():
    if not os.path.exists(BENCHMARK_CSV_PATH):
        print(f"❌ 평가 대상 파일이 없습니다: {BENCHMARK_CSV_PATH}")
        return

    rubrics = load_json(RUBRICS_PATH)
    documents_list = load_json(DOCUMENTS_PATH)
    doc_map = {doc["doc_id"]: doc for doc in documents_list}

    evaluated_records = []
    print("📊 Ground Truth, 카테고리 F1 & ROUGE-L 기반 정량 채점 중...")

    with open(BENCHMARK_CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "WARMUP" in row.get("run_type", "").upper():
                continue

            doc_id = row.get("doc_id", "")
            raw_response = row.get("raw_response", "")
            doc_info = doc_map.get(doc_id, {})

            eval_res = evaluate_single_run(raw_response, doc_id, doc_info, rubrics)

            record = {**row}
            record["type_group"] = eval_res["type_group"]
            record["category_f1"] = eval_res["category_f1"]
            record["rouge_l_f1"] = eval_res["rouge_l_f1"]
            record["quality_score"] = eval_res["total_score"]
            record["score_breakdown"] = eval_res["detail_scores"]
            record["pydantic_valid"] = eval_res["is_parsed"]

            evaluated_records.append(record)

    # 평가 결과 CSV 저장
    Path(EVALUATION_CSV_PATH).parent.mkdir(parents=True, exist_ok=True)
    if evaluated_records:
        fieldnames = list(evaluated_records[0].keys())
        with open(EVALUATION_CSV_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(evaluated_records)

        print(f"✅ 정량 평가 완료! 저장 경로: {EVALUATION_CSV_PATH}")

        # 리포트 출력
        print("\n==========================================")
        print("🏆 Ground Truth 기반 모델 종합 리포트")
        print("==========================================")

        model_stats = {}
        for rec in evaluated_records:
            m = rec["model_name"]
            if m not in model_stats:
                model_stats[m] = {
                    "count": 0,
                    "total_score": 0.0,
                    "total_cat_f1": 0.0,
                    "total_rouge": 0.0,
                    "pydantic_pass": 0,
                    "total_tps": 0.0,
                    "total_sec": 0.0,
                }

            model_stats[m]["count"] += 1
            model_stats[m]["total_score"] += float(rec["quality_score"])
            model_stats[m]["total_cat_f1"] += float(rec["category_f1"])
            model_stats[m]["total_rouge"] += float(rec["rouge_l_f1"])
            if str(rec["pydantic_valid"]).lower() == "true":
                model_stats[m]["pydantic_pass"] += 1
            model_stats[m]["total_tps"] += float(rec.get("eval_tps", 0.0))
            model_stats[m]["total_sec"] += float(rec.get("elapsed_sec", 0.0))

        for model, stats in model_stats.items():
            cnt = stats["count"]
            avg_score = round(stats["total_score"] / cnt, 4) if cnt else 0.0
            avg_cat_f1 = round(stats["total_cat_f1"] / cnt, 4) if cnt else 0.0
            avg_rouge = round(stats["total_rouge"] / cnt, 4) if cnt else 0.0
            pydantic_rate = (
                round((stats["pydantic_pass"] / cnt) * 100, 2) if cnt else 0.0
            )
            avg_tps = round(stats["total_tps"] / cnt, 2) if cnt else 0.0
            avg_sec = round(stats["total_sec"] / cnt, 2) if cnt else 0.0

            print(f"\n📌 모델명: {model}")
            print(f"  - 평균 카테고리 F1 Score: {avg_cat_f1:.4f}")
            print(f"  - 요약문 ROUGE-L F1 Score: {avg_rouge:.4f}")
            print(f"  - 종합 품질 점수 (Quality Score): {avg_score:.4f} / 1.0000")
            print(
                f"  - Pydantic 스키마 준수율: {pydantic_rate}% ({stats['pydantic_pass']}/{cnt})"
            )
            print(f"  - 평균 생성 속도 (TPS): {avg_tps} tokens/sec")
            print(f"  - 평균 응답 시간 (Latency): {avg_sec} 초")


if __name__ == "__main__":
    main()