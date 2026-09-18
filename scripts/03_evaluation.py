import csv
import json
import os
from collections import Counter
from pathlib import Path

from rouge_score import rouge_scorer

from configs.schemas import SummaryResponse
from configs.prompt_templates import REFUSAL_TEMPLATE

# 1. 경로 정의
BENCHMARK_CSV_PATH = "results/raw_benchmark.csv"
CLOUD_BENCHMARK_CSV_PATH = "results/raw_benchmark_cloud.csv"
DOCUMENTS_PATH = "data/documents.json"
RUBRICS_PATH = "data/rubrics.json"
HUMAN_EVAL_PATH = "data/human_eval.csv"
EVALUATION_CSV_PATH = "results/evaluation_scores.csv"
CLOUD_EVALUATION_CSV_PATH = "results/evaluation_scores_cloud.csv"

RUN_TYPE_TO_REP_ID = {"MAIN_1": 1, "MAIN_2": 2, "CLOUD_MAIN": 1}

HUMAN_EVAL_PENALTIES = {
    "err_hallucination": 40,
    "err_fact_data": 25,
    "err_language": 25,
    "err_readability": 15,
}


def load_json(path: str) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_human_eval_map(path: str) -> dict[tuple[str, str, int], dict[str, int]]:
    """(model_name, doc_id, rep_id) -> {err_*: int} 맵 생성. 파싱 불가 행은 미채점으로 간주해 무시."""
    human_eval_map: dict[tuple[str, str, int], dict[str, int]] = {}
    if not os.path.exists(path):
        return human_eval_map

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                key = (row["model_name"], row["doc_id"], int(row["rep_id"]))
                errors = {col: int(row[col]) for col in HUMAN_EVAL_PENALTIES}
            except (KeyError, ValueError, TypeError):
                continue
            human_eval_map[key] = errors
    return human_eval_map


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


def calculate_category_f1(pred_categories: list[str], gt_category: list[str]) -> float:
    """다중 카테고리(리스트) 간 F1-Score 계산"""
    if not pred_categories:
        return 0.0

    gt_set = set(gt_category)
    pred_set = set(pred_categories)

    if not gt_set or not pred_set:
        return 0.0

    intersection = gt_set.intersection(pred_set)
    if not intersection:
        return 0.0

    precision = len(intersection) / len(pred_set)
    recall = len(intersection) / len(gt_set)

    f1 = 2 * (precision * recall) / (precision + recall)
    return round(f1, 4)


def calculate_category_score(pred_categories: list[str], gt_category: str | list[str]) -> float:
    """GT가 단일 문자열이면 완전 일치(부분점수 없음), 다중 리스트면 F1-Score로 채점"""
    if isinstance(gt_category, str):
        gt_set = {gt_category} if gt_category else set()
        pred_set = set(pred_categories or [])
        if not gt_set or not pred_set:
            return 0.0
        return 1.0 if pred_set == gt_set else 0.0
    return calculate_category_f1(pred_categories, gt_category)


_KIWI = None


def _get_kiwi():
    """Kiwi 초기화 비용이 커서 모듈 전역에 1회만 생성(lazy singleton)"""
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi
        _KIWI = Kiwi()
    return _KIWI


class KiwiRougeTokenizer:
    """rouge_score의 tokenizer 인터페이스(tokenize(text) -> List[str])를 Kiwi 형태소 분석기로 구현.
    한국어는 조사/어미가 붙어 공백 기준 토큰화로는 ROUGE 중복률이 과소평가되므로 형태소 단위로 비교한다."""

    def tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        return [token.form for token in _get_kiwi().tokenize(text)]


def calculate_rouge_1_2_mean(candidate_bullets: list[str], reference_bullets: list[str]) -> float:
    """Kiwi 형태소 토큰화 기반 ROUGE-1, ROUGE-2 F1의 평균"""
    if not candidate_bullets or not reference_bullets:
        return 0.0

    cand_text = " ".join(candidate_bullets)
    ref_text = " ".join(reference_bullets)

    scorer = rouge_scorer.RougeScorer(["rouge1", "rouge2"], tokenizer=KiwiRougeTokenizer())
    scores = scorer.score(ref_text, cand_text)

    mean_f1 = (scores["rouge1"].fmeasure + scores["rouge2"].fmeasure) / 2
    return round(mean_f1, 4)


def score_summary_bullets(
    is_parsed: bool,
    parsed_res: SummaryResponse | None,
    gt: dict,
    sub_weights: dict,
    human_eval_row: dict | None,
) -> tuple[float | None, dict]:
    """summary_bullets 항목의 세부 지표 계산 및 가중합 (NORMAL/TOOL_CALLING/UNCERTAIN 그룹 공용).
    human_eval_row가 없으면(사람이 아직 채점 안 함) raw score는 None을 반환해 상위에서 quality_score를
    공란 처리하고 집계에서 제외하도록 한다. 단, 다른 자동 지표(keyword/rouge/bullet_count)는 계산 가능하므로
    detail에 그대로 담아 진단용으로 노출한다."""
    llm_bullets = parsed_res.summary_bullets if (is_parsed and parsed_res) else []
    llm_text = " ".join(llm_bullets)

    must_keywords = gt.get("must_include_keywords", [])
    keyword_recall = (
        sum(1 for kw in must_keywords if kw in llm_text) / len(must_keywords)
        if must_keywords else 1.0
    )

    rouge_mean = calculate_rouge_1_2_mean(llm_bullets, gt.get("summary_bullets", []))
    bullet_ok = 1.0 if is_parsed and len(llm_bullets) == 3 else 0.0

    human_score = None
    if human_eval_row is not None:
        penalty = sum(
            HUMAN_EVAL_PENALTIES[col] * human_eval_row.get(col, 0)
            for col in HUMAN_EVAL_PENALTIES
        )
        human_score = max(0, 100 - penalty) / 100.0

    detail = {
        "keyword_match_recall": round(keyword_recall, 4),
        "rouge_1_2_mean": rouge_mean,
        "bullet_count_exact_3": bullet_ok,
        "human_eval_score": human_score,
        "human_eval_available": human_eval_row is not None,
    }

    if human_score is None:
        return None, detail

    raw = (
        keyword_recall * sub_weights["keyword_match"]
        + rouge_mean * sub_weights["rouge_1_2_mean"]
        + human_score * sub_weights["human_eval"]
        + bullet_ok * sub_weights["bullet_count_exact_3"]
    )
    return round(raw, 4), detail


def calculate_tool_name_score(expected_tool_names: list[str], actual_tool_names: list[str]) -> float:
    """호출된 tool 이름이 기대한 tool과 '개수'까지 정확히 일치하는지 precision/recall/F1로 채점.
    (기존에는 set()으로 비교해 중복 호출을 걸러내지 못했음 — 예: jira를 3번 불러도 통과되던 문제)
    중복 호출·불필요한 tool 호출은 precision을, 누락은 recall을 낮춰 감점된다."""
    if not expected_tool_names and not actual_tool_names:
        return 1.0
    if not actual_tool_names or not expected_tool_names:
        return 0.0

    expected_counts = Counter(expected_tool_names)
    actual_counts = Counter(actual_tool_names)
    correct = sum(min(expected_counts[name], actual_counts[name]) for name in expected_counts)

    precision = correct / len(actual_tool_names)
    recall = correct / len(expected_tool_names)
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def score_tool_calls(
    is_parsed: bool, parsed_res: SummaryResponse | None, gt: dict, sub_weights: dict
) -> tuple[float, dict]:
    """tool_calls 항목(tool_name/tool_args)의 가중합 계산"""
    gt_tools = gt.get("expected_tool_calls", [])
    has_tools = bool(is_parsed and parsed_res and parsed_res.tool_calls)

    gt_tool_names = [t.get("tool_name") for t in gt_tools]
    llm_tool_names = [tc.tool_name for tc in parsed_res.tool_calls] if has_tools else []
    tool_name_score = calculate_tool_name_score(gt_tool_names, llm_tool_names)

    if not gt_tools:
        args_ratio = 1.0 if not has_tools else 0.0
        detail = {"tool_name_score": tool_name_score, "tool_args_ratio": args_ratio}
        raw = tool_name_score * sub_weights["tool_name"] + args_ratio * sub_weights["tool_args"]
        return round(raw, 4), detail

    if not has_tools:
        detail = {"tool_name_score": tool_name_score, "tool_args_ratio": 0.0}
        return 0.0, detail

    # tool_name별 마지막 호출의 arguments를 기준으로 GT 인자값과 실제 일치 여부 비교
    llm_args_by_name = {tc.tool_name: (tc.arguments or {}) for tc in parsed_res.tool_calls}

    def _args_match(expected: dict, actual: dict) -> bool:
        if not expected:
            return True
        if not actual:
            return False
        return all(
            str(actual.get(k, "")).strip().lower() == str(v).strip().lower()
            for k, v in expected.items()
        )

    matched = sum(
        1 for t in gt_tools
        if _args_match(t.get("arguments", {}), llm_args_by_name.get(t.get("tool_name"), {}))
    )
    args_ratio = matched / len(gt_tools)

    detail = {"tool_name_score": tool_name_score, "tool_args_ratio": round(args_ratio, 4)}
    raw = tool_name_score * sub_weights["tool_name"] + args_ratio * sub_weights["tool_args"]
    return round(raw, 4), detail


def _cond_category(is_parsed: bool, parsed_res: SummaryResponse | None, gt: dict) -> float:
    pred = parsed_res.categories if (is_parsed and parsed_res and parsed_res.categories) else []
    return calculate_category_score(pred, gt.get("category", ""))


def _cond_must_be_false(is_parsed: bool, parsed_res: SummaryResponse | None, gt: dict) -> float:
    return 1.0 if is_parsed and parsed_res.is_uncertain is False else 0.0


def _cond_must_be_true(is_parsed: bool, parsed_res: SummaryResponse | None, gt: dict) -> float:
    return 1.0 if is_parsed and parsed_res.is_uncertain is True else 0.0


def _normalize_refusal(text: str) -> str:
    return text.strip().rstrip(" .!?。…·").strip()


def _cond_matches_refusal_template(is_parsed: bool, parsed_res: SummaryResponse | None, gt: dict) -> float:
    if not is_parsed or not parsed_res or len(parsed_res.summary_bullets) != 1:
        return 0.0
    return (
        1.0
        if _normalize_refusal(parsed_res.summary_bullets[0]) == _normalize_refusal(REFUSAL_TEMPLATE)
        else 0.0
    )


CONDITION_HANDLERS = {
    "category_f1_score": _cond_category,
    "must_be_false": _cond_must_be_false,
    "must_be_true": _cond_must_be_true,
    "matches_refusal_template": _cond_matches_refusal_template,
}


def evaluate_single_run(
    raw_response: str,
    doc_id: str,
    doc_info: dict,
    rubrics: dict,
    human_eval_row: dict | None,
) -> dict:
    """data/rubrics.json의 중첩(scoring_logic -> {weight, condition|sub_weights}) 구조를 그대로 순회하며 채점.
    그룹별 if/elif 분기 없이, scoring_logic에 실제로 존재하는 항목만 채점하므로 DOC-10처럼
    summary_bullets/category/tool_calls가 아예 없는 그룹도 자연히 처리된다."""
    type_group, rubric_rule = get_rubric_group(doc_id, rubrics)
    scoring_logic = rubric_rule.get("scoring_logic", {})
    gt = doc_info.get("ground_truth", {})

    try:
        parsed_res: SummaryResponse | None = SummaryResponse.model_validate_json(raw_response)
        is_parsed = True
    except Exception:
        parsed_res = None
        is_parsed = False

    weighted: dict[str, float | None] = {}
    breakdown: dict[str, dict] = {}
    top_level = {
        "category_score": "",
        "rouge_1_2_mean": "",
        "keyword_match_recall": "",
        "human_eval_score": "",
        "human_eval_available": "",
    }
    unscored = False

    for field, node in scoring_logic.items():
        weight = node["weight"]

        if field == "summary_bullets":
            raw, detail = score_summary_bullets(
                is_parsed, parsed_res, gt, node["sub_weights"], human_eval_row
            )
            top_level["keyword_match_recall"] = detail["keyword_match_recall"]
            top_level["rouge_1_2_mean"] = detail["rouge_1_2_mean"]
            top_level["human_eval_score"] = detail["human_eval_score"] if detail["human_eval_score"] is not None else ""
            top_level["human_eval_available"] = detail["human_eval_available"]
            if raw is None:
                unscored = True
                weighted[field] = None
            else:
                weighted[field] = round(raw * weight, 4)
            breakdown[field] = {"raw": raw, "weight": weight, "weighted": weighted[field], **detail}
            continue

        if field == "tool_calls":
            raw, detail = score_tool_calls(is_parsed, parsed_res, gt, node["sub_weights"])
            weighted[field] = round(raw * weight, 4)
            breakdown[field] = {"raw": raw, "weight": weight, "weighted": weighted[field], **detail}
            continue

        handler = CONDITION_HANDLERS[node["condition"]]
        raw = handler(is_parsed, parsed_res, gt)
        if field == "category":
            top_level["category_score"] = raw
        weighted[field] = round(raw * weight, 4)
        breakdown[field] = {"raw": raw, "weight": weight, "weighted": weighted[field]}

    total_score = None if unscored else round(sum(v for v in weighted.values() if v is not None), 4)

    return {
        "type_group": type_group,
        "total_score": total_score,
        "category_score": top_level["category_score"],
        "rouge_1_2_mean": top_level["rouge_1_2_mean"],
        "keyword_match_recall": top_level["keyword_match_recall"],
        "human_eval_score": top_level["human_eval_score"],
        "human_eval_available": top_level["human_eval_available"],
        "detail_scores": json.dumps(breakdown, ensure_ascii=False),
        "is_parsed": is_parsed,
    }


def evaluate_and_report(
    benchmark_csv_path: str,
    evaluation_csv_path: str,
    rubrics: dict,
    doc_map: dict,
    human_eval_map: dict,
    report_title: str,
) -> None:
    if not os.path.exists(benchmark_csv_path):
        print(f"❌ 평가 대상 파일이 없습니다: {benchmark_csv_path}")
        return

    evaluated_records = []
    print(f"📊 [{report_title}] Ground Truth 기반 정량 채점 + 인간 검증(human_eval) 반영 채점 중...")

    with open(benchmark_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "WARMUP" in row.get("run_type", "").upper():
                continue

            doc_id = row.get("doc_id", "")
            doc_info = doc_map.get(doc_id, {})
            model_name = row.get("model_name", "")
            rep_id = RUN_TYPE_TO_REP_ID.get(row.get("run_type", ""))
            # 컬럼이 없는 과거 raw_benchmark.csv와의 호환을 위해 기본값은 성공(True)으로 간주
            call_success = row.get("call_success", "True").strip().lower() != "false"

            record = {**row, "rep_id": rep_id}

            if not call_success:
                # 호출 자체가 실패한 런: 품질 점수를 0으로 채우지 않고 채점에서 제외
                type_group, _ = get_rubric_group(doc_id, rubrics)
                record["type_group"] = type_group
                record["category_score"] = ""
                record["rouge_1_2_mean"] = ""
                record["keyword_match_recall"] = ""
                record["human_eval_score"] = ""
                record["human_eval_available"] = ""
                record["quality_score"] = ""
                record["score_breakdown"] = ""
                record["pydantic_valid"] = False
            else:
                raw_response = row.get("raw_response", "")
                human_eval_row = human_eval_map.get((model_name, doc_id, rep_id))
                eval_res = evaluate_single_run(raw_response, doc_id, doc_info, rubrics, human_eval_row)
                record["type_group"] = eval_res["type_group"]
                record["category_score"] = eval_res["category_score"]
                record["rouge_1_2_mean"] = eval_res["rouge_1_2_mean"]
                record["keyword_match_recall"] = eval_res["keyword_match_recall"]
                record["human_eval_score"] = eval_res["human_eval_score"]
                record["human_eval_available"] = eval_res["human_eval_available"]
                # human_eval이 아직 없으면 total_score=None -> 공란 처리 후 집계에서 제외
                record["quality_score"] = eval_res["total_score"] if eval_res["total_score"] is not None else ""
                record["score_breakdown"] = eval_res["detail_scores"]
                record["pydantic_valid"] = eval_res["is_parsed"]

            evaluated_records.append(record)

    # 평가 결과 CSV 저장
    Path(evaluation_csv_path).parent.mkdir(parents=True, exist_ok=True)
    if evaluated_records:
        fieldnames = list(evaluated_records[0].keys())
        with open(evaluation_csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(evaluated_records)

        print(f"✅ 정량 평가 완료! 저장 경로: {evaluation_csv_path}")

        # 리포트 출력
        print("\n==========================================")
        print(f"🏆 [{report_title}] Ground Truth 기반 모델 종합 리포트")
        print("==========================================")

        model_stats = {}
        for rec in evaluated_records:
            m = rec["model_name"]
            if m not in model_stats:
                model_stats[m] = {
                    "attempts": 0,
                    "successes": 0,
                    "n_quality": 0,
                    "total_score": 0.0,
                    "n_cat": 0,
                    "total_cat_score": 0.0,
                    "n_rouge": 0,
                    "total_rouge": 0.0,
                    "pydantic_pass": 0,
                    "n_tps": 0,
                    "total_tps": 0.0,
                    "n_sec": 0,
                    "total_sec": 0.0,
                    "human_eval_needed": 0,
                    "human_eval_done": 0,
                    "fail_samples": [],
                }
            s = model_stats[m]
            s["attempts"] += 1

            call_success = str(rec.get("call_success", "True")).strip().lower() != "false"
            if not call_success:
                if len(s["fail_samples"]) < 3:
                    s["fail_samples"].append(
                        f"{rec.get('doc_id', '?')}: {rec.get('fail_reason', '')}"
                    )
                continue
            s["successes"] += 1

            # human_eval 채점 진행률: summary_bullets 채점 대상(=DOC-10 제외) 런만 집계
            if rec.get("type_group") != "OUT_OF_BOUNDS":
                s["human_eval_needed"] += 1
                if str(rec.get("human_eval_available", "")).strip().lower() == "true":
                    s["human_eval_done"] += 1

            # 카테고리/ROUGE는 quality_score(=human_eval 포함 종합 점수)와 무관하게
            # 값이 있는 행 기준으로 따로 집계한다. DOC-10처럼 항목 자체가 없는 행은
            # 분모에서 빠지고, human_eval이 아직 없어 quality_score가 빈 행도
            # category_score/rouge_1_2_mean은 이미 계산돼 있으므로 포함한다.
            if rec.get("category_score", "") != "":
                s["n_cat"] += 1
                s["total_cat_score"] += float(rec["category_score"])
            if rec.get("rouge_1_2_mean", "") != "":
                s["n_rouge"] += 1
                s["total_rouge"] += float(rec["rouge_1_2_mean"])

            if rec.get("quality_score", "") != "":
                s["n_quality"] += 1
                s["total_score"] += float(rec["quality_score"])
                if str(rec["pydantic_valid"]).lower() == "true":
                    s["pydantic_pass"] += 1

            elapsed_raw = rec.get("elapsed_sec", "")
            if elapsed_raw != "":
                s["n_sec"] += 1
                s["total_sec"] += float(elapsed_raw)

            tps_raw = rec.get("eval_tps", "")
            if tps_raw != "":
                s["n_tps"] += 1
                s["total_tps"] += float(tps_raw)
            elif rec.get("fail_reason"):
                if len(s["fail_samples"]) < 3:
                    s["fail_samples"].append(
                        f"{rec.get('doc_id', '?')}: {rec.get('fail_reason', '')}"
                    )

        for model, s in model_stats.items():
            nq = s["n_quality"]
            n_cat = s["n_cat"]
            n_rouge = s["n_rouge"]
            avg_score = round(s["total_score"] / nq, 4) if nq else None
            avg_cat_score = round(s["total_cat_score"] / n_cat, 4) if n_cat else None
            avg_rouge = round(s["total_rouge"] / n_rouge, 4) if n_rouge else None
            pydantic_rate = round((s["pydantic_pass"] / nq) * 100, 2) if nq else None
            avg_tps = round(s["total_tps"] / s["n_tps"], 2) if s["n_tps"] else None
            avg_sec = round(s["total_sec"] / s["n_sec"], 2) if s["n_sec"] else None

            print(f"\n📌 모델명: {model}")
            print(f"  - 호출 성공 수 / 전체 시도 수: {s['successes']}/{s['attempts']}")
            print(f"  - 평균 카테고리 점수: {avg_cat_score} (n={n_cat})")
            print(f"  - 요약문 ROUGE-1,2 Mean: {avg_rouge} (n={n_rouge})")
            print(
                f"  - 종합 품질 점수 (Quality Score): {avg_score} / 1.0000 (n={nq}, human_eval 미채점 런은 집계 제외)"
            )
            print(
                f"  - Pydantic 스키마 준수율: {pydantic_rate}% ({s['pydantic_pass']}/{nq})"
            )
            print(f"  - human_eval 채점 완료: {s['human_eval_done']}/{s['human_eval_needed']}")
            print(f"  - 평균 생성 속도 (TPS): {avg_tps} tokens/sec (n={s['n_tps']})")
            print(f"  - 평균 응답 시간 (Latency): {avg_sec} 초 (n={s['n_sec']})")
            if s["fail_samples"]:
                print("  - 대표 실패/미측정 사례:")
                for sample in s["fail_samples"]:
                    print(f"      · {sample}")


def main():
    rubrics = load_json(RUBRICS_PATH)
    documents_list = load_json(DOCUMENTS_PATH)
    doc_map = {doc["doc_id"]: doc for doc in documents_list}
    human_eval_map = load_human_eval_map(HUMAN_EVAL_PATH)

    evaluate_and_report(
        BENCHMARK_CSV_PATH, EVALUATION_CSV_PATH, rubrics, doc_map, human_eval_map, "로컬"
    )

    if os.path.exists(CLOUD_BENCHMARK_CSV_PATH):
        print()
        evaluate_and_report(
            CLOUD_BENCHMARK_CSV_PATH,
            CLOUD_EVALUATION_CSV_PATH,
            rubrics,
            doc_map,
            human_eval_map,
            "Cloud",
        )


if __name__ == "__main__":
    main()
