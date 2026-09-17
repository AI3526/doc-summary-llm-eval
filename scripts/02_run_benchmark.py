import csv
import json
import os
import sys
import time
from pathlib import Path

import ollama
from dotenv import load_dotenv
from openai import OpenAI

from configs.prompt_templates import (
    BENCHMARK_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)
from configs.schemas import SummaryResponse, RESPONSE_SCHEMA

load_dotenv()

# 1. 파일 경로 및 실험 설정
DOCUMENTS_PATH = "data/documents.json"
OUTPUT_CSV_PATH = "results/raw_benchmark.csv"
CLOUD_OUTPUT_CSV_PATH = "results/raw_benchmark_cloud.csv"

LOCAL_MODELS = ["qwen2.5:7b", "llama3.1:8b"]
CLOUD_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CLOUD_DOC_IDS = ["DOC-01", "DOC-04", "DOC-07", "DOC-09", "DOC-10"]

# gpt-5.6-luna 단가 (Prices per 1M tokens, short-context 기준 — 이 벤치마크의 문서/질문은
# 짧은 문서(약 500~1200자) + 시스템 프롬프트 수준이라 long-context 구간에 해당하지 않는다)
CLOUD_INPUT_PRICE_PER_1M = 0.20
CLOUD_CACHED_INPUT_PRICE_PER_1M = 0.02
CLOUD_OUTPUT_PRICE_PER_1M = 1.20


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


GENERATION_OPTIONS = {"temperature": 0, "num_predict": 512, "seed": 42}


def get_runtime_load_state(client: ollama.Client, model_name: str) -> dict:
    """README STEP6 '실행 조건'(digest, quantization_level, 실제 context_length, CPU/GPU 적재 상태)을
    client.ps() 한 번으로 전부 조회한다."""
    ps_info = client.ps()
    m = next((m for m in ps_info.models if m.model == model_name), None)
    if m is None:
        return {
            "digest": "N/A",
            "quantization_level": "N/A",
            "context_length": "N/A",
            "vram_mib": "N/A",
            "gpu_offload_status": "N/A (미적재)",
        }

    size_vram = m.size_vram or 0
    size_total = m.size or 0
    vram_mib = round(size_vram / (1024 * 1024), 2) if size_vram else "N/A"

    if size_total > 0:
        offload_pct = round(size_vram / size_total * 100)
        gpu_offload_status = "100% GPU" if offload_pct >= 100 else f"{offload_pct}% GPU"
    else:
        gpu_offload_status = "N/A"

    return {
        "digest": m.digest or "N/A",
        "quantization_level": (m.details.quantization_level if m.details else None) or "N/A",
        "context_length": m.context_length if m.context_length is not None else "N/A",
        "vram_mib": vram_mib,
        "gpu_offload_status": gpu_offload_status,
    }


def run_ollama_experiment(
    client: ollama.Client, model_name: str, doc: dict, is_warmup: bool = False
) -> dict:
    run_type = "WARMUP" if is_warmup else "MAIN"

    # prompt_templates.py의 템플릿 적용
    user_content = USER_PROMPT_TEMPLATE.format(
        title=doc.get("title", ""),
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
            options=GENERATION_OPTIONS,
        )
        elapsed_sec = time.perf_counter() - start_time

        # 시간 및 TPS 계산
        eval_duration_ns = response.get("eval_duration", 0)
        eval_count = response.get("eval_count", 0)
        load_duration_ns = response.get("load_duration", 0)

        load_sec = round(load_duration_ns / 1e9, 4) if load_duration_ns else 0.0

        tps_fail_reason = ""
        if not eval_count:
            eval_tps = ""
            tps_fail_reason = "eval_count=0 (토큰 생성 0개): TPS 계산 불가"
        elif not eval_duration_ns or eval_duration_ns <= 0:
            eval_tps = ""
            tps_fail_reason = "eval_duration<=0 또는 누락: TPS 계산 불가"
        else:
            eval_tps = round(eval_count / (eval_duration_ns / 1e9), 2)

        # 실행 조건 일괄 조회 (VRAM 포함, client.ps() 1회)
        runtime_state = get_runtime_load_state(client, model_name)

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
            "vram_mib": runtime_state["vram_mib"],
            "prompt_tokens": response.get("prompt_eval_count", 0),
            "completion_tokens": eval_count,
            "est_cost_usd": 0.0,
            "pydantic_valid": pydantic_valid,
            "digest": runtime_state["digest"],
            "quantization_level": runtime_state["quantization_level"],
            "context_length": runtime_state["context_length"],
            "generation_options": json.dumps(GENERATION_OPTIONS),
            "gpu_offload_status": runtime_state["gpu_offload_status"],
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
            "digest": "",
            "quantization_level": "",
            "context_length": "",
            "generation_options": json.dumps(GENERATION_OPTIONS),
            "gpu_offload_status": "",
            "raw_response": "",
        }


# ==========================================
# Cloud API 실험 함수
# ==========================================
def run_cloud_experiment(client: OpenAI, model_name: str, doc: dict) -> dict:
    user_content = USER_PROMPT_TEMPLATE.format(
        title=doc.get("title", ""),
        content=doc.get("content", ""),
        question=doc.get("question", ""),
    )

    start_time = time.perf_counter()
    try:
        # ToolCallRequest.arguments가 자유 형식 dict라 OpenAI strict 모드가 요구하는
        # "모든 object에 additionalProperties: false" 조건을 못 맞춰서 strict=False로 호출한다.
        response = client.responses.create(
            model=model_name,
            instructions=BENCHMARK_SYSTEM_PROMPT,
            input=user_content,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "SummaryResponse",
                    "schema": RESPONSE_SCHEMA,
                    "strict": False,
                }
            },
            reasoning={"effort": "none"},
            max_output_tokens=512,
            tools=[],
            tool_choice="none",
            store=False,
        )
        elapsed_sec = time.perf_counter() - start_time

        usage = response.usage
        prompt_tokens = usage.input_tokens if usage else 0
        completion_tokens = usage.output_tokens if usage else 0
        cached_tokens = (
            usage.input_tokens_details.cached_tokens
            if usage and usage.input_tokens_details and usage.input_tokens_details.cached_tokens
            else 0
        )
        billed_input_tokens = prompt_tokens - cached_tokens

        eval_tps = round(completion_tokens / elapsed_sec, 2) if elapsed_sec > 0 else 0.0

        est_cost_usd = round(
            (billed_input_tokens * CLOUD_INPUT_PRICE_PER_1M / 1_000_000)
            + (cached_tokens * CLOUD_CACHED_INPUT_PRICE_PER_1M / 1_000_000)
            + (completion_tokens * CLOUD_OUTPUT_PRICE_PER_1M / 1_000_000),
            6,
        )

        raw_response = response.output_text or ""
        # 출력이 max_output_tokens 등으로 중간에 끊긴 경우 호출 자체는 성공이지만 참고용으로 남김
        status_note = "" if response.status == "completed" else f"non_completed_status:{response.status}"

        return {
            "model_name": model_name,
            "doc_id": doc["doc_id"],
            "run_type": "CLOUD_MAIN",
            "call_success": True,
            "fail_reason": status_note,
            "elapsed_sec": round(elapsed_sec, 4),
            "load_sec": 0.0,
            "eval_tps": eval_tps,
            "vram_mib": "N/A (Cloud)",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "est_cost_usd": est_cost_usd,
            "pydantic_valid": validate_pydantic_response(raw_response),
            "raw_response": raw_response.replace("\n", " "),
        }
    except Exception as e:
        return {
            "model_name": model_name,
            "doc_id": doc["doc_id"],
            "run_type": "CLOUD_MAIN",
            "call_success": False,
            "fail_reason": f"API_ERROR: {str(e)}",
            "elapsed_sec": "",
            "load_sec": "",
            "eval_tps": "",
            "vram_mib": "N/A (Cloud)",
            "prompt_tokens": "",
            "completion_tokens": "",
            "est_cost_usd": "",
            "pydantic_valid": False,
            "raw_response": "",
        }


def run_local_experiments(documents: list[dict]) -> list[dict]:
    """로컬 모델 실험 (2개 모델 x 10개 문서 x 2회 = 40회 + 워밍업 2회)"""
    ollama_client = ollama.Client(host="http://127.0.0.1:11434", timeout=180)
    results = []
    run_counter = 1

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

    return results


def run_cloud_experiments(documents: list[dict]) -> list[dict]:
    """Cloud API 비교 실험 (README STEP7: 공통 질문 5개, 각 1회)"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ OPENAI_API_KEY가 설정되지 않아 Cloud 실험을 건너뜁니다.")
        return []

    openai_client = OpenAI(api_key=api_key)
    cloud_docs = [d for d in documents if d["doc_id"] in CLOUD_DOC_IDS]
    results = []

    print(f"\n☁️ Cloud API({CLOUD_MODEL}) 비교 실험 시작...")
    for i, doc in enumerate(cloud_docs, start=1):
        res = run_cloud_experiment(openai_client, CLOUD_MODEL, doc)
        res["run_id"] = f"cloud_run_{i:03d}"
        results.append(res)
        print(
            f"  - [{res['run_id']}] Cloud {doc['doc_id']} 완료 ({res['elapsed_sec']}s, call_success={res['call_success']})"
        )

    return results


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    documents = load_documents()

    # documents = [d for d in documents if d['doc_id'] in ['DOC-1', 'DOC-04', 'DOC-9']]

    run_local = "--cloud-only" not in sys.argv
    run_cloud = "--local-only" not in sys.argv

    if run_local:
        local_results = run_local_experiments(documents)
        write_csv(OUTPUT_CSV_PATH, local_results)
        print(f"\n✅ 로컬 메인 실험 완료! 저장 경로: {OUTPUT_CSV_PATH}")
        print(f"총 레코드 수: {len(local_results)}건 (워밍업 포함)")

    if run_cloud:
        cloud_results = run_cloud_experiments(documents)
        write_csv(CLOUD_OUTPUT_CSV_PATH, cloud_results)
        if cloud_results:
            print(f"\n✅ Cloud 비교 실험 완료! 저장 경로: {CLOUD_OUTPUT_CSV_PATH}")
            print(f"총 레코드 수: {len(cloud_results)}건")


if __name__ == "__main__":
    main()