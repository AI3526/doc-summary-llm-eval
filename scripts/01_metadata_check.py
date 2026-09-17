import json
import subprocess
from pathlib import Path

import ollama
from huggingface_hub import hf_hub_download, model_info

# 허깅페이스 매핑 정보 및 수동 보완 데이터 (요청 항목 반영)
MODEL_CONFIG = {
    "qwen2.5:7b": {
        "hf_repo_id": "Qwen/Qwen2.5-7B-Instruct",
        "language_note": "공식 모델 카드에 한국어 포함 29개 이상 언어 지원 명시",  # GGUF general.languages 자동 수집값에 덧붙일 보완 설명 (직접 작성)
        "Benchmark": "IFEval 75.85% / MMLU-PRO 36.52%",  # 빈칸 (수동 작성용)
    },
    "llama3.1:8b": {
        "hf_repo_id": "meta-llama/Llama-3.1-8B-Instruct",
        "language_note": "공식 모델 카드에 8개 언어보다 더 많은 언어로 학습되었으며, 그 외 언어로 파인튜닝 가능함을 명시",  # GGUF general.languages 자동 수집값에 덧붙일 보완 설명 (직접 작성)
        "Benchmark": "IFEval 49.22% / MMLU-PRO 31.09%",  # 빈칸 (수동 작성용)
    },
}


def get_hardware_gpu_info() -> dict:
    """nvidia-smi로 GPU 명칭 및 총 VRAM 용량을 자동 추출 (00_env_check.py와 동일 방식, torch 불필요)"""
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
        return {"gpu_name": name, "vram_total_gb": f"{vram_total_gb} GB"}
    except Exception:
        return {"gpu_name": "CPU Only / CUDA Not Detected", "vram_total_gb": "0 GB"}


def measure_ollama_runtime_state(client: ollama.Client, model: str) -> dict:
    """모델을 메모리에 적재해 VRAM 점유량/GPU 오프로드 비율을 측정한 뒤 다시 내린다.
    ollama Python 클라이언트만 사용 (CLI 텍스트 파싱 없음): size_vram/size 비율로
    'ollama ps'가 보여주는 오프로드 비율(예: 100% GPU)을 직접 계산한다."""
    print(f"[{model}] 메모리 적재 및 상태 측정 중...")

    offload_status = "N/A (미적재)"
    vram_usage = "N/A"

    try:
        # 워밍업 겸 적재
        client.generate(model=model, prompt="hi", options={"num_predict": 1})

        ps_res = client.ps()
        for m in ps_res.models:
            if m.model == model:
                size_vram = m.size_vram or 0
                size_total = m.size or 0

                vram_usage = f"{round(size_vram / (1024 * 1024), 2)} MiB"

                if size_total > 0:
                    offload_pct = round(size_vram / size_total * 100)
                    offload_status = (
                        "100% GPU" if offload_pct >= 100 else f"{offload_pct}% GPU"
                    )
                break
    except Exception as e:
        print(f"  └ 상태 측정 에러: {e}")

    # 모델 언로드 (keep_alive=0)
    try:
        client.generate(model=model, prompt="", keep_alive=0)
    except Exception:
        pass

    return {"offload_status": offload_status, "vram_usage": vram_usage}


def fetch_huggingface_metadata(hf_repo_id: str) -> dict:
    """Hugging Face Hub 메타데이터 조회 (게이티드 모델도 model_info 메타데이터 조회 자체는
    토큰 없이 가능 — 별도 인증 불필요). 실패 시 전부 N/A로 채워 반환."""
    result = {
        "hf_id": "N/A",
        "sha": "N/A",
        "pipeline_tag": "N/A",
        "library_name": "N/A",
        "license_str": "N/A",
        "license_link": "N/A",
    }
    if not hf_repo_id:
        return result

    try:
        info = model_info(repo_id=hf_repo_id)
        result["hf_id"] = info.id
        result["sha"] = info.sha
        result["pipeline_tag"] = info.pipeline_tag or "N/A"
        result["library_name"] = info.library_name or "transformers"

        card = info.card_data
        result["license_str"] = getattr(card, "license", None) or "N/A"
        result["license_link"] = (
            getattr(card, "license_link", None)
            or f"[Link](https://huggingface.co/{hf_repo_id}/blob/main/LICENSE)"
        )
    except Exception as e:
        print(f"  └ [HF Error] {e}")

    return result


def fetch_hf_vocab_size(hf_repo_id: str) -> "int | str":
    """Ollama GGUF 메타데이터에 vocab_size가 없는 모델(예: qwen2.5)을 위한 폴백.
    HF config.json에서 직접 읽는다 (게이티드가 아닌 리포만 토큰 없이 가능)."""
    if not hf_repo_id:
        return "N/A"
    try:
        config_path = hf_hub_download(repo_id=hf_repo_id, filename="config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("vocab_size", "N/A")
    except Exception as e:
        print(f"  └ [HF config.json Error] {e}")
        return "N/A"


def fetch_ollama_details(model: str, hf_repo_id: str = "") -> dict:
    """Ollama show()로 파라미터 크기/양자화/아키텍처/컨텍스트 길이/지원 언어/토크나이저/vocab_size 조회
    (01_metadata_check.py와 동일 방식). vocab_size가 Ollama 메타데이터에 없으면 HF config.json으로 폴백."""
    try:
        ol_info = ollama.show(model)
        details = ol_info.get("details", {}) or {}
        gguf_info = ol_info.get("modelinfo", {}) or {}
        context_length = next(
            (v for k, v in gguf_info.items() if "context_length" in k),
            "확인 불가 (modelinfo에 context_length 키 없음)",
        )
        languages = gguf_info.get("general.languages", [])

        tokenizer_model = gguf_info.get("tokenizer.ggml.model", "N/A")
        tokenizer_pre = gguf_info.get("tokenizer.ggml.pre", "N/A")
        tokenizer_desc = (
            f"{tokenizer_model.upper()} (pre-tokenizer: {tokenizer_pre})"
            if tokenizer_model != "N/A"
            else "N/A"
        )

        vocab_size = next((v for k, v in gguf_info.items() if k.endswith(".vocab_size")), None)
        if vocab_size is None:
            vocab_size = fetch_hf_vocab_size(hf_repo_id)

        return {
            "param_size": details.get("parameter_size", "N/A"),
            "quant_level": details.get("quantization_level", "N/A"),
            "architecture": details.get("family", "N/A"),
            "context_length": context_length,
            "languages": languages,
            "tokenizer": tokenizer_desc,
            "vocab_size": vocab_size,
        }
    except Exception as e:
        print(f"  └ [Ollama Error] {e}")
        return {
            "param_size": "N/A",
            "quant_level": "N/A",
            "architecture": "N/A",
            "context_length": "N/A",
            "languages": [],
            "tokenizer": "N/A",
            "vocab_size": "N/A",
        }


def extract_and_merge_metadata(models: list[str]) -> dict:
    client = ollama.Client()
    gpu_spec = get_hardware_gpu_info()
    combined_results = {}

    for model in models:
        print(f"\n==================== {model} 메타데이터 추출 ====================")
        cfg = MODEL_CONFIG.get(model, {})

        hf_repo_id = cfg.get("hf_repo_id", "")
        hf_data = fetch_huggingface_metadata(hf_repo_id)
        ollama_data = fetch_ollama_details(model, hf_repo_id)
        runtime_state = measure_ollama_runtime_state(client, model)

        auto_languages = ", ".join(ollama_data["languages"]) if ollama_data["languages"] else "N/A"
        language_note = cfg.get("language_note", "")
        language_field = f"{auto_languages} ({language_note})" if language_note else auto_languages

        combined_results[model] = {
            "Model Name": hf_data["hf_id"],
            "SHA": hf_data["sha"],
            "Pipeline_tag": hf_data["pipeline_tag"],
            "Library_name": hf_data["library_name"],
            "License": hf_data["license_str"],
            "License_link": hf_data["license_link"],
            "Language": language_field,
            "Architecture": ollama_data["architecture"],
            "Parameter Size": ollama_data["param_size"],
            "Quantization": ollama_data["quant_level"],
            "Context Length": ollama_data["context_length"],
            "Tokenizer": ollama_data["tokenizer"],
            "Vocab Size": ollama_data["vocab_size"],
            "Benchmark": cfg.get("Benchmark", ""),  # 빈칸
            "GPU": gpu_spec["gpu_name"],
            "gpu_vram_total_gb": gpu_spec["vram_total_gb"],
            "offload_status": runtime_state["offload_status"],
            "vram_usage": runtime_state["vram_usage"],
        }

    return combined_results


def save_as_vertical_markdown_table(data_dict: dict, output_filepath: str):
    """세로형 마크다운 표 자동 생성 및 저장 함수"""
    if not data_dict:
        return

    models = list(data_dict.keys())
    feature_keys = list(data_dict[models[0]].keys())

    md_lines = []
    header_line = "| 조사 항목 | " + " | ".join(models) + " |"
    separator_line = "| :--- | " + " | ".join([":---"] * len(models)) + " |"

    md_lines.append(header_line)
    md_lines.append(separator_line)

    for key in feature_keys:
        row_str = f"| **{key}** | "
        values = [str(data_dict[m].get(key, "")) for m in models]
        row_str += " | ".join(values) + " |"
        md_lines.append(row_str)

    md_table = "\n".join(md_lines)

    Path(output_filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(output_filepath, "w", encoding="utf-8") as f:
        f.write(md_table)

    print(f"\n✅ 세로형 메타데이터 표 저장 완료: {output_filepath}\n")
    print(md_table)


if __name__ == "__main__":
    target_models = ["qwen2.5:7b", "llama3.1:8b"]

    merged_data = extract_and_merge_metadata(target_models)

    # raw json 저장
    with open("results/metadata.json", "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)

    # 마크다운 표 생성 및 저장
    save_as_vertical_markdown_table(merged_data, "results/model_metadata_table.md")
