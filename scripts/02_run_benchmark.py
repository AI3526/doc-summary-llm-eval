import csv
import json
import os
import time
from pathlib import Path

import ollama
# from openai import OpenAI  # 클라우드 테스트 시 주석 해제

from configs.prompt_templates import (
    BENCHMARK_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)
from configs.schemas import SummaryResponse, RESPONSE_SCHEMA

# 1. 파일 경로 및 실험 설정
DOCUMENTS_PATH = "data/documents.json"
OUTPUT_CSV_PATH = "results/raw_benchmark.csv"

LOCAL_MODELS = ["qwen2.5:7b", "llama3.1:8b"]
# CLOUD_MODEL = "gpt-4o-mini"
# CLOUD_DOC_IDS = ["DOC-01", "DOC-04", "DOC-07", "DOC-09", "DOC-10"]


def load_documents() -> list[dict]:
    with open(DOCUMENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_pydantic_response(raw_text: str) -> bool:
    """Ollama 응답 텍스트가 SummaryResponse Pydantic 스키마에 완벽히 부합하는지 검증"""
    try:
        SummaryResponse.model_validate_json(raw_text)
        return True
    except Exception:
        return False


def run_ollama_experiment(
    client: ollama.Client, model_name: str, doc: dict, is_warmup: bool = False
) -> dict:
    run_type = "WARMUP" if is_warmup else "MAIN"

    # prompt_templates.py의 템플릿 적용
    user_content = USER_PROMPT_TEMPLATE.format(
        title=doc.get("title", ""),
        category=doc.get("category", ""),
        content=doc.get("content", ""),
        question=doc.get("question", ""),
    )

    start_time = time.perf_counter()
    try:
        # 00_env_check.py 방식 그대로 format=RESPONSE_SCHEMA 적용
        response = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": BENCHMARK_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            format=RESPONSE_SCHEMA,
            stream=False,
            options={"temperature": 0, "num_predict": 512},
        )
        elapsed_sec = time.perf_counter() - start_time

        # 시간 및 TPS 계산
        eval_duration_ns = response.get("eval_duration", 0)
        eval_count = response.get("eval_count", 0)
        load_duration_ns = response.get("load_duration", 0)

        load_sec = round(load_duration_ns / 1e9, 4) if load_duration_ns else 0.0

        tps_fail_reason = ""
        if eval_duration_ns and eval_duration_ns > 0 and eval_count:
            eval_tps = round(eval_count / (eval_duration_ns / 1e9), 2)
        else:
            eval_tps = ""
            tps_fail_reason = "eval_duration<=0 또는 eval_count 없음: TPS 계산 불가"

        # VRAM 점유량 실시간 측정 (client.ps())
        ps_info = client.ps()
        vram_bytes = next(
            (m.size_vram for m in ps_info.models if m.model == model_name), 0
        )
        vram_mib = (
            round(vram_bytes / (1024 * 1024), 2) if vram_bytes else "N/A"
        )

        raw_response = response["message"]["content"]
        # Pydantic 엄격 검증 수행
        pydantic_valid = validate_pydantic_response(raw_response)

        return {
            "model_name": model_name,
            "doc_id": doc["doc_id"],
            "run_type": run_type,
            "call_success": True,
            "fail_reason": tps_fail_reason,
            "elapsed_sec": round(elapsed_sec, 4),
            "load_sec": load_sec,
            "eval_tps": eval_tps,
            "vram_mib": vram_mib,
            "prompt_tokens": response.get("prompt_eval_count", 0),
            "completion_tokens": eval_count,
            "est_cost_usd": 0.0,
            "pydantic_valid": pydantic_valid,
            "raw_response": raw_response.replace("\n", " "),
        }
    except Exception as e:
        return {
            "model_name": model_name,
            "doc_id": doc["doc_id"],
            "run_type": run_type,
            "call_success": False,
            "fail_reason": f"API_ERROR: {str(e)}",
            "elapsed_sec": "",
            "load_sec": "",
            "eval_tps": "",
            "vram_mib": "",
            "prompt_tokens": "",
            "completion_tokens": "",
            "est_cost_usd": "",
            "pydantic_valid": False,
            "raw_response": "",
        }


# ==========================================
# Cloud API 실험 함수 (필요 시 주석 해제)
# ==========================================
# def run_cloud_experiment(client: OpenAI, model_name: str, doc: dict) -> dict:
#     user_content = USER_PROMPT_TEMPLATE.format(
#         title=doc.get("title", ""),
#         category=doc.get("category", ""),
#         content=doc.get("content", ""),
#         question=doc.get("question", "")
#     )
#     start_time = time.perf_counter()
#     try:
#         response = client.chat.completions.create(
#             model=model_name,
#             messages=[
#                 {"role": "system", "content": BENCHMARK_SYSTEM_PROMPT},
#                 {"role": "user", "content": user_content}
#             ],
#             response_format={"type": "json_object"},
#             temperature=0
#         )
#         elapsed_sec = time.perf_counter() - start_time
#         prompt_tokens = response.usage.prompt_tokens if response.usage else 0
#         completion_tokens = response.usage.completion_tokens if response.usage else 0
#         raw_response = response.choices[0].message.content or ""

#         return {
#             "model_name": model_name,
#             "doc_id": doc["doc_id"],
#             "run_type": "CLOUD_MAIN",
#             "elapsed_sec": round(elapsed_sec, 4),
#             "load_sec": 0.0,
#             "eval_tps": round(completion_tokens / elapsed_sec, 2) if elapsed_sec > 0 else 0.0,
#             "vram_mib": "N/A (Cloud)",
#             "prompt_tokens": prompt_tokens,
#             "completion_tokens": completion_tokens,
#             "est_cost_usd": round((prompt_tokens * 0.00015 / 1000) + (completion_tokens * 0.00060 / 1000), 6),
#             "pydantic_valid": validate_pydantic_response(raw_response),
#             "raw_response": raw_response.replace("\n", " ")
#         }
#     except Exception as e:
#         return {
#             "model_name": model_name,
#             "doc_id": doc["doc_id"],
#             "run_type": "CLOUD_MAIN",
#             "elapsed_sec": 0.0, "load_sec": 0.0, "eval_tps": 0.0,
#             "vram_mib": "N/A (Cloud)", "prompt_tokens": 0, "completion_tokens": 0,
#             "est_cost_usd": 0.0, "pydantic_valid": False,
#             "raw_response": f"ERROR: {str(e)}"
#         }


def main():
    documents = load_documents()

    # documents = [d for d in documents if d['doc_id'] in ['DOC-1', 'DOC-04', 'DOC-9']]

    # 00_env_check.py와 동일하게 타임아웃 180초 설정
    ollama_client = ollama.Client(host="http://127.0.0.1:11434", timeout=180)

    results = []
    run_counter = 1

    # ==========================================
    # 1. 로컬 모델 실험 (2개 모델 x 10개 문서 x 2회 = 40회 + 워밍업 2회)
    # ==========================================
    print("🚀 로컬 Ollama 벤치마크 실험 시작...")
    for model_name in LOCAL_MODELS:
        print(f"\n---> 모델 준비 중: {model_name}")

        # 워밍업 1회 (DOC-01 사용)
        print(f"[{model_name}] 워밍업 실행 중...")
        warmup_res = run_ollama_experiment(
            ollama_client, model_name, documents[0], is_warmup=True
        )
        warmup_res["run_id"] = f"warmup_{model_name}"
        results.append(warmup_res)

        # 본 실험 2회 반복 (총 20회)
        for repeat in range(1, 3):
            print(f"[{model_name}] 본 실험 반복 {repeat}/2 진행 중...")
            for doc in documents:
                res = run_ollama_experiment(
                    ollama_client, model_name, doc, is_warmup=False
                )
                res["run_id"] = f"run_{run_counter:03d}"
                res["run_type"] = f"MAIN_{repeat}"
                results.append(res)
                print(
                    f"  - [{res['run_id']}] {doc['doc_id']} 완료 ({res['elapsed_sec']}s, TPS: {res['eval_tps']}, PydanticValid: {res['pydantic_valid']})"
                )
                run_counter += 1

    # ==========================================
    # 2. Cloud API 비교 실험 (나중에 주석 해제하여 사용)
    # ==========================================
    # print("\n☁️ Cloud API 비교 실험 시작...")
    # api_key = os.getenv("OPENAI_API_KEY")
    # if api_key:
    #     openai_client = OpenAI(api_key=api_key)
    #     cloud_docs = [d for d in documents if d["doc_id"] in CLOUD_DOC_IDS]
    #     for doc in cloud_docs:
    #         res = run_cloud_experiment(openai_client, CLOUD_MODEL, doc)
    #         res["run_id"] = f"run_{run_counter:03d}"
    #         results.append(res)
    #         print(f"  - [{res['run_id']}] Cloud {doc['doc_id']} 완료 ({res['elapsed_sec']}s)")
    #         run_counter += 1

    # ==========================================
    # 3. CSV 파일 저장
    # ==========================================
    Path(OUTPUT_CSV_PATH).parent.mkdir(parents=True, exist_ok=True)
    if results:
        fieldnames = list(results[0].keys())
        with open(OUTPUT_CSV_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

        print(f"\n✅ 로컬 메인 실험 완료! 저장 경로: {OUTPUT_CSV_PATH}")
        print(f"총 레코드 수: {len(results)}건 (워밍업 포함)")


if __name__ == "__main__":
    main()