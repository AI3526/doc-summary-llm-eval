import csv
import json
import time
from pathlib import Path

import ollama

from configs.schemas import (
    BENCHMARK_TOOLS,
    FINAL_ANSWER_JSON_SCHEMA,
    TOOL_NEED_JSON_SCHEMA,
    FinalAnswerSchema,
    SummaryResponse,
    ToolNeedSchema,
)
from configs.prompt_templates import TOOL_ROUTING_SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT

TEMPERATURE = 0  # 실험 4/5에서 확인: temperature 미고정 시 특히 llama의 native tool calling이 불안정함
MODELS = ["qwen2.5:7b", "llama3.1:8b"]
OUTPUT_CSV_PATH = "results/toolcalling_results.csv"


def build_user_prompt(doc: dict) -> str:
    return f"""[사내 문서 정보]
- 문서 제목: {doc['title']}

[문서 내용]
{doc['content']}

[사용자 질문]
{doc['question']}
"""


# === 1. 툴(Tool) 실제/Mock 구현 ===
def add_calendar_event(title: str = "", start_time: str = "", end_time: str = "", description: str = "") -> str:
    """구글 캘린더에 일정을 등록합니다."""
    return f"일정 '{title}'이(가) {start_time}~{end_time}에 성공적으로 등록되었습니다. (Event ID: #9999)"


def send_slack_notification(channel: str = "", title: str = "", urgency: str = "") -> str:
    """슬랙 채널에 알림 메시지를 보냅니다."""
    return f"슬랙 {channel} 채널로 '{title}'({urgency}) 알림 발송 완료"


def create_jira_ticket(project_key: str = "", issue_type: str = "", summary: str = "", priority: str = "") -> str:
    """Jira에 이슈 티켓을 생성합니다."""
    return f"{project_key} 프로젝트에 '{summary}' 티켓 생성 완료 (우선순위: {priority})"


# tool_name -> 실제 실행 함수 매핑 (BENCHMARK_TOOLS의 함수명과 반드시 일치해야 함)
TOOL_DISPATCH = {
    "create_calendar_event": add_calendar_event,
    "send_slack_notification": send_slack_notification,
    "create_jira_ticket": create_jira_ticket,
}


def execute_tool(tool_name: str, arguments: dict) -> str:
    handler = TOOL_DISPATCH.get(tool_name)
    if handler is None:
        return f"[오류] 알 수 없는 도구입니다: {tool_name}"
    try:
        return handler(**arguments)
    except TypeError as e:
        return f"[오류] 인자가 맞지 않습니다: {e}"


def _extract_call_meta(response) -> dict:
    """Ollama 응답 1건에서 타이밍/토큰 메타데이터를 뽑아낸다 (02_run_benchmark.py와 동일한 필드명 사용)."""
    return {
        "prompt_tokens": response.get("prompt_eval_count", 0) or 0,
        "completion_tokens": response.get("eval_count", 0) or 0,
        "eval_duration_ns": response.get("eval_duration", 0) or 0,
        "load_duration_ns": response.get("load_duration", 0) or 0,
    }


def get_vram_mib(client: ollama.Client, model_name: str):
    """client.ps() 1회 조회로 현재 적재된 모델의 VRAM 사용량(MiB)을 가져온다."""
    ps_info = client.ps()
    m = next((m for m in ps_info.models if m.model == model_name), None)
    if m is None or not m.size_vram:
        return "N/A"
    return round(m.size_vram / (1024 * 1024), 2)


def check_if_tool_needed(client: ollama.Client, model_name: str, doc: dict) -> tuple[bool, dict]:
    """질문 텍스트만 보고 tool 호출이 필요한 상황인지 가볍게 먼저 판단한다 (실험 6의 '불필요한 tool 과다 호출' 대응)."""
    response = client.chat(
        model=model_name,
        messages=[
            {"role": "system", "content": TOOL_ROUTING_SYSTEM_PROMPT},
            {"role": "user", "content": f"[사용자 질문]\n{doc['question']}"},
        ],
        format=TOOL_NEED_JSON_SCHEMA,
        options={"temperature": TEMPERATURE},
    )
    tool_needed = ToolNeedSchema.model_validate_json(response.message.content).tool_needed
    return tool_needed, _extract_call_meta(response)


def process_document(client: ollama.Client, model_name: str, doc: dict) -> dict:
    """한 문서에 대해 [라우팅 게이트] -> (필요시) 턴A(native tool calling) -> 턴B(구조화 요약/분류)를 실행하고,
    02_run_benchmark.py와 같은 형식의 성능 지표 + 재구성된 raw_response를 담은 레코드를 반환한다."""
    start_time = time.perf_counter()
    try:
        call_metas = []

        tool_needed, routing_meta = check_if_tool_needed(client, model_name, doc)
        call_metas.append(routing_meta)

        messages = [
            {"role": "system", "content": TOOL_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(doc)},
        ]

        tool_call_logs = []
        fired_tool_calls = []  # SummaryResponse.tool_calls 형식으로 재구성해 담아둘 리스트
        if tool_needed:
            # === [Turn 1] tools를 실제로 넘겨서 LLM에게 툴 판단 요청 ===
            response = client.chat(
                model=model_name,
                messages=messages,
                tools=BENCHMARK_TOOLS,
                options={"temperature": TEMPERATURE},
            )
            call_metas.append(_extract_call_meta(response))
            messages.append(response.message)

            if response.message.tool_calls:
                for tool_call in response.message.tool_calls:
                    args = dict(tool_call.function.arguments)
                    tool_result = execute_tool(tool_call.function.name, args)
                    tool_call_logs.append(f"{tool_call.function.name}({json.dumps(args, ensure_ascii=False)}) -> {tool_result}")
                    fired_tool_calls.append({"tool_name": tool_call.function.name, "arguments": args})

                    # tool_name을 반드시 명시해야 tool_call이 여러 개일 때 모델이 결과를 올바르게 매칭할 수 있음
                    messages.append({
                        "role": "tool",
                        "content": tool_result,
                        "tool_name": tool_call.function.name,
                    })
        # tool_needed=False면 tools 자체를 넘기지 않으므로 Turn A를 아예 건너뛴다 (물리적으로 tool_calls 불가능)

        # === [Turn 2] (있다면) 툴 실행 결과를 주고 요약+카테고리+is_uncertain을 구조화 출력으로 요청 ===
        messages.append({
            "role": "user",
            "content": "위 문서와 처리 결과를 바탕으로 요약(summary_bullets), 카테고리(categories), 불확실성 여부(is_uncertain)를 JSON으로 정리해줘.",
        })
        final_response = client.chat(
            model=model_name,
            messages=messages,
            format=FINAL_ANSWER_JSON_SCHEMA,
            options={"temperature": TEMPERATURE},
        )
        call_metas.append(_extract_call_meta(final_response))
        parsed = FinalAnswerSchema.model_validate_json(final_response.message.content)

        elapsed_sec = time.perf_counter() - start_time

        # 턴A(tool_calls) + 턴B(요약/카테고리/is_uncertain)를 하나의 SummaryResponse 형태로 재구성
        # -> scripts/03_evaluation.py의 채점 함수(특히 신규 calculate_tool_name_score)를 그대로 재사용하기 위함
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

        prompt_tokens = sum(m["prompt_tokens"] for m in call_metas)
        completion_tokens = sum(m["completion_tokens"] for m in call_metas)
        total_eval_duration_sec = sum(m["eval_duration_ns"] for m in call_metas) / 1e9
        total_load_sec = sum(m["load_duration_ns"] for m in call_metas) / 1e9
        eval_tps = round(completion_tokens / total_eval_duration_sec, 2) if total_eval_duration_sec > 0 else ""

        return {
            "model_name": model_name,
            "doc_id": doc["doc_id"],
            "call_success": True,
            "fail_reason": "",
            "elapsed_sec": round(elapsed_sec, 4),
            "load_sec": round(total_load_sec, 4),
            "eval_tps": eval_tps,
            "vram_mib": get_vram_mib(client, model_name),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "pydantic_valid": pydantic_valid,
            "tool_needed": tool_needed,
            "num_tool_calls": len(fired_tool_calls),
            "tool_call_logs": " | ".join(tool_call_logs),
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
            "vram_mib": "",
            "prompt_tokens": "",
            "completion_tokens": "",
            "pydantic_valid": False,
            "tool_needed": "",
            "num_tool_calls": "",
            "tool_call_logs": "",
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
    client = ollama.Client(host="http://127.0.0.1:11434", timeout=180)

    with open("data/documents.json", "r", encoding="utf-8") as f:
        docs = json.load(f)

    records = []

    for model_name in MODELS:
        print(f"\n{'#' * 60}\n# 모델: {model_name}\n{'#' * 60}")

        for doc in docs:
            gt = doc["ground_truth"]
            expected_tool_names = sorted(t["tool_name"] for t in gt.get("expected_tool_calls", []))

            print(f"\n{'=' * 60}\n{doc['doc_id']}\n{'=' * 60}")
            result = process_document(client, model_name, doc)
            records.append(result)

            if not result["call_success"]:
                print(f"[실패] {result['fail_reason']}")
                continue

            print(
                f"[라우팅] tool_needed={result['tool_needed']} (기대={bool(expected_tool_names)}, "
                f"일치={result['tool_needed'] == bool(expected_tool_names)})"
            )
            print(f"[Turn 1: tool_calls] {result['num_tool_calls']}건 (기대={expected_tool_names or '없음'})")
            if result["tool_call_logs"]:
                for log in result["tool_call_logs"].split(" | "):
                    print(f"  - {log}")

            print(
                f"[성능] elapsed={result['elapsed_sec']}s, load={result['load_sec']}s, "
                f"eval_tps={result['eval_tps']}, vram={result['vram_mib']}MiB, "
                f"tokens(prompt/completion)={result['prompt_tokens']}/{result['completion_tokens']}"
            )
            print(f"[raw_response] {result['raw_response']}")

    write_csv(OUTPUT_CSV_PATH, records)
    print(f"\n✅ 저장 완료: {OUTPUT_CSV_PATH} ({len(records)}건)")


if __name__ == "__main__":
    main()
