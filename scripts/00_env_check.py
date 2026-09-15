import json
from ollama import Client
from configs.schemas import RESPONSE_SCHEMA, BENCHMARK_TOOLS
from configs.prompt_templates import (
    BENCHMARK_SYSTEM_PROMPT, 
    USER_PROMPT_TEMPLATE, 
)

MODEL = "qwen2.5:7b"

# 1. 문서 데이터 로드
with open("data/documents.json", "r", encoding="utf-8") as f:
    documents = json.load(f)

target_doc = documents[1]

# 2. 유저 프롬프트 조립
user_content = USER_PROMPT_TEMPLATE.format(
    title=target_doc["title"],
    category=target_doc["category"],
    content=target_doc["content"],
    question=target_doc["question"]
)

# 3. Ollama 추론 호출
client = Client(host="http://127.0.0.1:11434", timeout=180)
print(f"[{target_doc['doc_id']}] '{target_doc['title']}' 문서 요약 요청 중...\n")

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

print("[Ollama 요약 답변 (JSON)]")
print(response.message.content)