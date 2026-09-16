import json
import platform
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version
from pathlib import Path

from ollama import Client
from configs.schemas import RESPONSE_SCHEMA, BENCHMARK_TOOLS
from configs.prompt_templates import (
    BENCHMARK_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

MODEL = "qwen2.5:7b"
ENV_CHECK_OUTPUT_PATH = "results/env_check.json"

# 1. 문서 데이터 로드
with open("data/documents.json", "r", encoding="utf-8") as f:
    documents = json.load(f)

target_doc = documents[1]

# 2. 유저 프롬프트 조립
user_content = USER_PROMPT_TEMPLATE.format(
    title=target_doc["title"],
    content=target_doc["content"],
    question=target_doc["question"]
)

# 3. Ollama 추론 호출
client = Client(host="http://127.0.0.1:11434", timeout=180)
print(f"[{target_doc['doc_id']}] '{target_doc['title']}' 문서 요약 요청 중...\n")

try:
    ps_before = client.ps()
    loaded_before = [m.model for m in ps_before.models]

    start_time = time.perf_counter()
    response = client.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": BENCHMARK_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        format=RESPONSE_SCHEMA,
        stream=False,
        options={"temperature": 0, "num_predict": 512},
    )
    elapsed_sec = time.perf_counter() - start_time

    print("[Ollama 요약 답변 (JSON)]")
    print(response.message.content)

    result = {
        "check_success": True,
        "error": None,
        "doc_id": target_doc["doc_id"],
        "model": MODEL,
        "elapsed_sec": round(elapsed_sec, 4),
        "was_model_preloaded": MODEL in loaded_before,
        "response": response.message.content,
    }
except Exception as e:
    result = {
        "check_success": False,
        "error": str(e),
        "doc_id": target_doc["doc_id"],
        "model": MODEL,
        "elapsed_sec": None,
        "was_model_preloaded": None,
        "response": None,
    }
    print(f"[오류] Python 호출 실패: {e}")

# STEP4: 실행 환경 확인 결과를 다시 읽을 수 있는 JSON 파일로 저장
env_record = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "python_version": sys.version.split()[0],
    "platform": platform.platform(),
    "ollama_client_host": "http://127.0.0.1:11434",
    "ollama_package_version": pkg_version("ollama"),
    "check_result": result,
}

Path(ENV_CHECK_OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
with open(ENV_CHECK_OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(env_record, f, ensure_ascii=False, indent=2)

print(f"\n✅ 실행 환경 확인 결과 저장 완료: {ENV_CHECK_OUTPUT_PATH}")