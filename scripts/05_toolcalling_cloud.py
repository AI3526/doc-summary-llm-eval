"""scripts/04_toolcalling_local.py의 native tool calling 파이프라인(라우팅 게이트 -> Turn A(tool 호출) -> Turn B(구조화 요약))을
OpenAI Responses API(gpt-5.6-luna)로 그대로 재현하는 실험 스크립트. (개인적 탐구용 — 과제 채점 파이프라인과는 무관)

Ollama와 프로토콜 자체는 동일(모델이 tool_call만 반환 -> 로컬에서 직접 실행 -> 결과를 다시 넣어 재호출)하지만
API 형식이 달라서 아래 두 가지를 변환/처리한다:
  1. tool 스키마: Chat Completions 스타일({"type":"function","function":{...}})을
     Responses API가 요구하는 평평한 형식({"type":"function","name":...})으로 변환.
  2. tool 실행 결과 반환: Ollama는 {"role":"tool","tool_name":...}면 되지만, Responses API는
     call_id로 매칭하는 {"type":"function_call_output","call_id":...,"output":...} 아이템이 필요하고,
     arguments도 이미 파싱된 dict가 아니라 JSON 문자열로 온다.

DOC-07 1건 테스트 결과 비용이 $0.000884 수준으로 확인되어(문서당 3~4천 prompt 토큰 기준),
TEST_DOC_IDS를 문서 10개 전체로 확장해 실행한다.
"""
import csv
import importlib.util
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from configs.schemas import (
    BENCHMARK_TOOLS,
    FINAL_ANSWER_JSON_SCHEMA,
    TOOL_NEED_JSON_SCHEMA,
    FinalAnswerSchema,
    SummaryResponse,
    ToolNeedSchema,
)
from configs.prompt_templates import TOOL_ROUTING_SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT

# build_user_prompt/execute_tool은 스키마·프롬프트와 달리 configs/로 옮기지 않은 04 고유 구현이라,
# 파일명이 숫자로 시작해 `import`가 불가능한 scripts/04_toolcalling_local.py를 importlib로 로드한다.
_SPEC = importlib.util.spec_from_file_location("toolcalling_local", "scripts/04_toolcalling_local.py")
toolcalling_local = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(toolcalling_local)

build_user_prompt = toolcalling_local.build_user_prompt
execute_tool = toolcalling_local.execute_tool

load_dotenv()

CLOUD_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEST_DOC_IDS = [f"DOC-{i:02d}" for i in range(1, 11)]
OUTPUT_CSV_PATH = "results/toolcalling_cloud_results.csv"

# scripts/02_run_benchmark.py와 동일한 gpt-5.6-luna 단가 (Prices per 1M tokens, short-context 기준)
CLOUD_INPUT_PRICE_PER_1M = 0.20
CLOUD_CACHED_INPUT_PRICE_PER_1M = 0.02
CLOUD_OUTPUT_PRICE_PER_1M = 1.20


def _to_responses_tool(chat_style_tool: dict) -> dict:
    """configs/schemas.py의 BENCHMARK_TOOLS(Chat Completions/Ollama 스타일)를
    Responses API가 요구하는 평평한 tool 스키마로 변환."""
    fn = chat_style_tool["function"]
    return {
        "type": "function",
        "name": fn["name"],
        "description": fn.get("description", ""),
        "parameters": fn["parameters"],
    }


CLOUD_TOOLS = [_to_responses_tool(t) for t in BENCHMARK_TOOLS]


def _usage_meta(response) -> dict:
    usage = response.usage
    prompt_tokens = usage.input_tokens if usage else 0
    completion_tokens = usage.output_tokens if usage else 0
    cached_tokens = (
        usage.input_tokens_details.cached_tokens
        if usage and usage.input_tokens_details and usage.input_tokens_details.cached_tokens
        else 0
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
    }


def check_if_tool_needed_cloud(client: OpenAI, model_name: str, doc: dict):
    response = client.responses.create(
        model=model_name,
        instructions=TOOL_ROUTING_SYSTEM_PROMPT,
        input=f"[사용자 질문]\n{doc['question']}",
        text={
            "format": {
                "type": "json_schema",
                "name": "ToolNeedSchema",
                "schema": TOOL_NEED_JSON_SCHEMA,
                "strict": False,
            }
        },
        reasoning={"effort": "none"},
        tools=[],
        tool_choice="none",
        store=False,
    )
    tool_needed = ToolNeedSchema.model_validate_json(response.output_text).tool_needed
    return tool_needed, response


def process_document_cloud(client: OpenAI, model_name: str, doc: dict) -> dict:
    """Ollama용 process_document()와 동일한 구조(라우팅 -> Turn A -> Turn B)를 Responses API로 구현."""
    start_time = time.perf_counter()
    try:
        usage_list = []

        tool_needed, routing_resp = check_if_tool_needed_cloud(client, model_name, doc)
        usage_list.append(_usage_meta(routing_resp))

        conversation_input = [{"role": "user", "content": build_user_prompt(doc)}]

        fired_tool_calls = []
        if tool_needed:
            # === [Turn A] tools를 실제로 넘겨서 모델에게 tool 호출 판단 요청 ===
            turn_a_resp = client.responses.create(
                model=model_name,
                instructions=TOOL_SYSTEM_PROMPT,
                input=conversation_input,
                tools=CLOUD_TOOLS,
                reasoning={"effort": "none"},
                store=False,
            )
            usage_list.append(_usage_meta(turn_a_resp))

            function_calls = [item for item in turn_a_resp.output if item.type == "function_call"]
            # 다음 턴에 이번 응답의 output(함수 호출 아이템 포함)을 그대로 이어붙여야 모델이 문맥을 유지함
            # by_alias=True(예: async_ -> async), exclude_none=True 없이 그대로 dump하면
            # API가 모르는 내부 필드(async_ 등)까지 같이 보내져 400 에러가 남
            conversation_input += [
                item.model_dump(by_alias=True, exclude_none=True) for item in turn_a_resp.output
            ]

            for call in function_calls:
                args = json.loads(call.arguments)
                tool_result = execute_tool(call.name, args)
                fired_tool_calls.append({"tool_name": call.name, "arguments": args})
                conversation_input.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": tool_result,
                })
        # tool_needed=False면 tools 자체를 넘기지 않으므로 Turn A를 아예 건너뛴다

        # === [Turn B] 툴 실행 결과를 반영해 요약+카테고리+is_uncertain을 구조화 출력으로 요청 ===
        conversation_input.append({
            "role": "user",
            "content": "위 문서와 처리 결과를 바탕으로 요약(summary_bullets), 카테고리(categories), 불확실성 여부(is_uncertain)를 JSON으로 정리해줘.",
        })
        turn_b_resp = client.responses.create(
            model=model_name,
            instructions=TOOL_SYSTEM_PROMPT,
            input=conversation_input,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "FinalAnswerSchema",
                    "schema": FINAL_ANSWER_JSON_SCHEMA,
                    "strict": False,
                }
            },
            reasoning={"effort": "none"},
            tools=[],
            tool_choice="none",
            store=False,
        )
        usage_list.append(_usage_meta(turn_b_resp))
        parsed = FinalAnswerSchema.model_validate_json(turn_b_resp.output_text)

        elapsed_sec = time.perf_counter() - start_time

        # 턴A(tool_calls) + 턴B(요약/카테고리/is_uncertain)를 하나의 SummaryResponse 형태로 재구성
        raw_response_dict = {
            "summary_bullets": parsed.summary_bullets,
            "categories": parsed.categories,
            "is_uncertain": parsed.is_uncertain,
            "tool_calls": fired_tool_calls,
        }
        raw_response = json.dumps(raw_response_dict, ensure_ascii=False)
        try:
            SummaryResponse.model_validate_json(raw_response)
            pydantic_valid = True
        except Exception:
            pydantic_valid = False

        prompt_tokens = sum(u["prompt_tokens"] for u in usage_list)
        completion_tokens = sum(u["completion_tokens"] for u in usage_list)
        cached_tokens = sum(u["cached_tokens"] for u in usage_list)
        billed_input_tokens = prompt_tokens - cached_tokens

        eval_tps = round(completion_tokens / elapsed_sec, 2) if elapsed_sec > 0 else 0.0
        est_cost_usd = round(
            (billed_input_tokens * CLOUD_INPUT_PRICE_PER_1M / 1_000_000)
            + (cached_tokens * CLOUD_CACHED_INPUT_PRICE_PER_1M / 1_000_000)
            + (completion_tokens * CLOUD_OUTPUT_PRICE_PER_1M / 1_000_000),
            6,
        )

        return {
            "model_name": model_name,
            "doc_id": doc["doc_id"],
            "call_success": True,
            "fail_reason": "",
            "elapsed_sec": round(elapsed_sec, 4),
            "load_sec": 0.0,
            "eval_tps": eval_tps,
            "vram_mib": "N/A (Cloud)",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "est_cost_usd": est_cost_usd,
            "pydantic_valid": pydantic_valid,
            "tool_needed": tool_needed,
            "num_tool_calls": len(fired_tool_calls),
            "raw_response": raw_response,
        }
    except Exception as e:
        return {
            "model_name": model_name,
            "doc_id": doc["doc_id"],
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
            "tool_needed": "",
            "num_tool_calls": "",
            "raw_response": "",
        }


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
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ OPENAI_API_KEY가 설정되지 않아 종료합니다.")
        return
    client = OpenAI(api_key=api_key)

    with open("data/documents.json", "r", encoding="utf-8") as f:
        docs = json.load(f)
    doc_map = {d["doc_id"]: d for d in docs}

    records = []
    for doc_id in TEST_DOC_IDS:
        doc = doc_map[doc_id]
        print(f"\n{'=' * 60}\n{doc_id} ({CLOUD_MODEL})\n{'=' * 60}")
        result = process_document_cloud(client, CLOUD_MODEL, doc)
        records.append(result)

        if not result["call_success"]:
            print(f"[실패] {result['fail_reason']}")
            continue

        gt = doc["ground_truth"]
        expected_tool_names = sorted(t["tool_name"] for t in gt.get("expected_tool_calls", []))
        print(
            f"[라우팅] tool_needed={result['tool_needed']} (기대={bool(expected_tool_names)}, "
            f"일치={result['tool_needed'] == bool(expected_tool_names)})"
        )
        print(f"[Turn A: tool_calls] {result['num_tool_calls']}건 (기대={expected_tool_names or '없음'})")
        print(
            f"[성능] elapsed={result['elapsed_sec']}s, tokens(prompt/completion)="
            f"{result['prompt_tokens']}/{result['completion_tokens']}, "
            f"eval_tps={result['eval_tps']}, est_cost_usd=${result['est_cost_usd']}"
        )
        print(f"[raw_response] {result['raw_response']}")

    write_csv(OUTPUT_CSV_PATH, records)
    print(f"\n✅ 저장 완료: {OUTPUT_CSV_PATH} ({len(records)}건)")


if __name__ == "__main__":
    main()
