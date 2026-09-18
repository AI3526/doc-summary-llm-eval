"""results/toolcalling_results.csv(scripts/04_toolcalling_local.py 산출물)를 scripts/03_evaluation.py의
채점 로직으로 그대로 채점하는 스크립트. (개인적 네이티브 툴 콜링 실험용 — 과제 채점 파이프라인과는 무관)

파일명이 숫자로 시작하는 scripts/03_evaluation.py는 `import`가 불가능해 importlib로 로드한다.
human_eval.csv에는 이 실험의 (model, doc_id, rep_id) 조합이 없으므로 summary_bullets의
human_eval 서브항목은 항상 미채점 처리되고, quality_score(종합 점수)는 공란이 된다.
대신 human_eval에 의존하지 않는 tool_calls 점수(신규 Counter 기반 F1 — 중복/과다 호출 감점 포함)와
category_score, is_uncertain 조건 점수는 그대로 계산되어 출력된다.
"""
import csv
import importlib.util
import json
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("evaluation_lib", "scripts/03_evaluation.py")
evaluation_lib = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluation_lib)

LOCAL_INPUT_CSV_PATH = "results/toolcalling_results.csv"
LOCAL_OUTPUT_CSV_PATH = "results/toolcalling_evaluation.csv"
CLOUD_INPUT_CSV_PATH = "results/toolcalling_cloud_results.csv"
CLOUD_OUTPUT_CSV_PATH = "results/toolcalling_cloud_evaluation.csv"
DOCUMENTS_PATH = "data/documents.json"
RUBRICS_PATH = "data/rubrics.json"


def _avg(rows: list[dict], field: str) -> tuple[float | None, int]:
    vals = [float(r[field]) for r in rows if r.get(field, "") != ""]
    return (round(sum(vals) / len(vals), 4), len(vals)) if vals else (None, 0)


def run_evaluation(input_csv_path: str, output_csv_path: str, report_title: str) -> None:
    if not Path(input_csv_path).exists():
        print(f"❌ 평가 대상 파일이 없습니다: {input_csv_path}")
        return

    rubrics = evaluation_lib.load_json(RUBRICS_PATH)
    documents_list = evaluation_lib.load_json(DOCUMENTS_PATH)
    doc_map = {doc["doc_id"]: doc for doc in documents_list}

    with open(input_csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    evaluated_records = []
    for row in rows:
        doc_id = row["doc_id"]
        doc_info = doc_map.get(doc_id, {})
        call_success = row.get("call_success", "True").strip().lower() != "false"

        record = dict(row)
        if not call_success:
            type_group, _ = evaluation_lib.get_rubric_group(doc_id, rubrics)
            record.update({
                "type_group": type_group,
                "category_score": "",
                "tool_calls_score": "",
                "tool_name_score": "",
                "tool_args_ratio": "",
                "is_uncertain_score": "",
                "quality_score": "",
                "score_breakdown": "",
            })
            evaluated_records.append(record)
            continue

        # human_eval 없이 채점 (summary_bullets의 human_eval 서브항목은 항상 unscored)
        eval_res = evaluation_lib.evaluate_single_run(
            row["raw_response"], doc_id, doc_info, rubrics, human_eval_row=None
        )
        breakdown = json.loads(eval_res["detail_scores"])
        tool_detail = breakdown.get("tool_calls", {})
        # rubrics.json에서 is_uncertain 조건 필드명이 그룹마다 다름
        # (DOC-04_to_08: is_uncertain_check, DOC-09: is_uncertain_detected)
        uncertain_key = next((k for k in breakdown if k.startswith("is_uncertain")), None)
        uncertain_raw = breakdown.get(uncertain_key, {}).get("raw", "") if uncertain_key else ""

        record.update({
            "type_group": eval_res["type_group"],
            "category_score": eval_res["category_score"],
            "tool_calls_score": tool_detail.get("raw", ""),
            "tool_name_score": tool_detail.get("tool_name_score", ""),
            "tool_args_ratio": tool_detail.get("tool_args_ratio", ""),
            "is_uncertain_score": uncertain_raw,
            "quality_score": eval_res["total_score"] if eval_res["total_score"] is not None else "",
            "score_breakdown": eval_res["detail_scores"],
        })
        evaluated_records.append(record)

    Path(output_csv_path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(evaluated_records[0].keys())
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(evaluated_records)
    print(f"✅ 채점 완료: {output_csv_path} ({len(evaluated_records)}건)")

    print(f"\n=== [{report_title}] 모델별 채점 요약 ===")
    for model_name in sorted({r["model_name"] for r in evaluated_records}):
        all_rows = [r for r in evaluated_records if r["model_name"] == model_name]
        tool_rows = [r for r in all_rows if r["tool_calls_score"] != ""]  # DOC-04~08 (tool_calls 항목이 있는 그룹)

        print(f"\n📌 {model_name}")

        avg_tool, n_tool = _avg(tool_rows, "tool_calls_score")
        avg_name, _ = _avg(tool_rows, "tool_name_score")
        if avg_tool is not None:
            print(f"  - tool_calls 종합 점수: {avg_tool} (n={n_tool}, DOC-04~08 대상)")
            print(f"  - tool_name F1 점수(중복/과다 호출 감점 포함): {avg_name}")

        avg_cat, n_cat = _avg(all_rows, "category_score")
        if avg_cat is not None:
            print(f"  - category_score: {avg_cat} (n={n_cat})")

        avg_uncertain, n_uncertain = _avg(all_rows, "is_uncertain_score")
        if avg_uncertain is not None:
            print(f"  - is_uncertain 정답률: {avg_uncertain} (n={n_uncertain}, DOC-04~09 대상)")

        low_name_rows = [r for r in tool_rows if r["tool_name_score"] != "" and float(r["tool_name_score"]) < 1.0]
        if low_name_rows:
            print("  - tool_name_score < 1.0 (중복/누락/과다 호출) 문서:")
            for r in low_name_rows:
                print(f"      · {r['doc_id']}: tool_name_score={r['tool_name_score']}")

        wrong_uncertain_rows = [
            r for r in all_rows
            if r["is_uncertain_score"] != "" and float(r["is_uncertain_score"]) != 1.0
        ]
        if wrong_uncertain_rows:
            print("  - is_uncertain 오답 문서:")
            for r in wrong_uncertain_rows:
                print(f"      · {r['doc_id']}: is_uncertain_score={r['is_uncertain_score']}")


def main():
    run_local = "--cloud-only" not in sys.argv
    run_cloud = "--local-only" not in sys.argv

    if run_local:
        run_evaluation(LOCAL_INPUT_CSV_PATH, LOCAL_OUTPUT_CSV_PATH, "로컬(qwen/llama)")
    if run_cloud:
        if run_local:
            print()
        run_evaluation(CLOUD_INPUT_CSV_PATH, CLOUD_OUTPUT_CSV_PATH, "Cloud(gpt-5.6-luna)")


if __name__ == "__main__":
    main()
