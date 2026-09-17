import json
import platform
import subprocess
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

# ==========================================
# 0. 기본 설정 및 상수 지정
# ==========================================
MODELS_TO_TEST = ["qwen2.5:7b", "llama3.1:8b"]
ENV_CHECK_OUTPUT_PATH = "results/env_check.json"
OLLAMA_HOST = "http://127.0.0.1:11434"

# ==========================================
# 1. 헬퍼 함수: GPU 정보 및 CLI 대화 자동 검증
# ==========================================
def get_hardware_gpu_info():
    """nvidia-smi로 GPU 명칭 및 총 VRAM 용량을 자동 추출합니다 (torch 설치 불필요)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        name, vram_mib_str = [p.strip() for p in result.stdout.strip().splitlines()[0].split(",")]
        vram_total_gb = round(float(vram_mib_str) / 1024, 2)
        return {
            "cuda_available": True,
            "gpu_name": name,
            "vram_total_gb": f"{vram_total_gb} GB"
        }
    except Exception:
        return {
            "cuda_available": False,
            "gpu_name": "CPU Only / CUDA Not Detected",
            "vram_total_gb": "0 GB"
        }

def check_cli_success(model_name: str, system_prompt: str, user_content: str) -> dict:
    """
    Ollama CLI(ollama run)가 실제 벤치마크 프롬프트를 받아 
    정상적으로 세 줄 요약 응답을 생성하는지 검증합니다.
    """
    # System 및 User 프롬프트를 CLI 전달용 문자열로 합성
    full_cli_prompt = f"[System Prompt]\n{system_prompt}\n\n[User Prompt]\n{user_content}"
    cmd = ["ollama", "run", model_name, full_cli_prompt]
    
    try:
        start_t = time.perf_counter()
        # 긴 문서 처리를 감안하여 타임아웃을 90초로 설정
        res = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=90, 
            encoding="utf-8"
        )
        elapsed = round(time.perf_counter() - start_t, 4)
        
        stdout_text = res.stdout.strip()
        
        # 1. 프로세스 정상 종료 여부 (Exit Code 0)
        is_zero_exit = (res.returncode == 0)
        
        # 2. 세 줄 요약 기준 최소 글자 수 검증 (60자 이상)
        is_sufficient_length = (len(stdout_text) >= 60)
        
        # 3. 출력 결과 내 에러 문자열 포함 여부 체크
        error_keywords = ["error", "failed", "exception", "traceback"]
        has_error_keyword = any(kw in stdout_text.lower() for kw in error_keywords)
        
        # 종합 성공 여부 판단
        is_success = is_zero_exit and is_sufficient_length and (not has_error_keyword)
        
        return {
            "cli_check_success": is_success,
            "cli_elapsed_sec": elapsed,
            "response_length": len(stdout_text),
            "cli_response_sample": stdout_text[:150] if is_success else None,
            "error_detail": res.stderr.strip() if not is_success else None
        }
    except Exception as e:
        return {
            "cli_check_success": False,
            "cli_elapsed_sec": None,
            "response_length": 0,
            "cli_response_sample": None,
            "error_detail": str(e)
        }

# ==========================================
# 2. 문서 데이터 로드 및 유저 프롬프트 준비
# ==========================================
with open("data/documents.json", "r", encoding="utf-8") as f:
    documents = json.load(f)

target_doc = documents[1]

user_content = USER_PROMPT_TEMPLATE.format(
    title=target_doc["title"],
    content=target_doc["content"],
    question=target_doc["question"]
)

client = Client(host=OLLAMA_HOST, timeout=180)
generation_options = {"temperature": 0, "num_predict": 512}

benchmark_results = []

# ==========================================
# 3. 모델 비교 루프 (Qwen vs Llama)
# ==========================================
print("🚀 [1/2] CLI 대화 검증 및 Python SDK 추론 테스트 시작...")

for model_name in MODELS_TO_TEST:
    print(f"\n--------------------------------------------------")
    print(f"📌 테스트 모델: {model_name}")

    # (1) CLI 대화 자동 검증
    print("  └─ [CLI] 'ollama run' 자동 대화 검증 중...")
    cli_result = check_cli_success(
        model_name=model_name,
        system_prompt=BENCHMARK_SYSTEM_PROMPT,
        user_content=user_content
    )

    # (2) Python SDK 추론 및 실행 시 VRAM 점유량 측정
    print("  └─ [Python API] Ollama 추론 호출 중...")
    try:
        ps_before = client.ps()
        loaded_before = [m.model for m in ps_before.models]

        start_time = time.perf_counter()
        response = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": BENCHMARK_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            format=RESPONSE_SCHEMA,
            stream=False,
            options=generation_options,
        )
        elapsed_sec = time.perf_counter() - start_time

        # 추론 직후 실시간 VRAM 점유량(메모리 점유 크기) 측정
        ps_after = client.ps()
        vram_allocated_gb = None
        for loaded_m in ps_after.models:
            if loaded_m.model == model_name:
                vram_allocated_gb = round(loaded_m.size / (1024 ** 3), 2)
                break

        print(f"  └─ [성공] 소요시간: {elapsed_sec:.2f}초 | VRAM 점유량: {vram_allocated_gb} GB")

        result = {
            "model": model_name,
            "python_api_success": True,
            "error": None,
            "elapsed_sec": round(elapsed_sec, 4),
            "was_model_preloaded": model_name in loaded_before,
            "allocated_vram_gb": f"{vram_allocated_gb} GB" if vram_allocated_gb is not None else "N/A",
            "cli_check": cli_result,
            "response": response.message.content,
        }
    except Exception as e:
        print(f"  └─ [오류] Python 호출 실패: {e}")
        result = {
            "model": model_name,
            "python_api_success": False,
            "error": str(e),
            "elapsed_sec": None,
            "was_model_preloaded": None,
            "allocated_vram_gb": None,
            "cli_check": cli_result,
            "response": None,
        }

    benchmark_results.append(result)

# ==========================================
# 4. 최종 환경 정보 수집 및 JSON 저장
# ==========================================
print("\n📊 [2/2] 실행 환경 및 측정 결과 저장 중...")

env_record = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "hardware_and_environment": {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "ollama_package_version": pkg_version("ollama"),
        "gpu_info": get_hardware_gpu_info(),
    },
    "execution_config": {
        "ollama_client_host": OLLAMA_HOST,
        "timeout": 180,
        "generation_options": generation_options,
        "target_doc_id": target_doc["doc_id"],
    },
    "summary_checks": {
        "all_cli_passed": all(r["cli_check"]["cli_check_success"] for r in benchmark_results),
        "all_python_api_passed": all(r["python_api_success"] for r in benchmark_results),
    },
    "benchmark_results": benchmark_results,
}

Path(ENV_CHECK_OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
with open(ENV_CHECK_OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(env_record, f, ensure_ascii=False, indent=2)

print(f"✅ 결과 저장 완료: {ENV_CHECK_OUTPUT_PATH}")