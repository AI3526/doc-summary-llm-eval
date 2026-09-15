import json
import ollama
from pathlib import Path

# API에서 자동으로 가져오기 어려운 항목 수동 정리
MANUAL_METADATA = {
    "qwen2.5:7b": {
        "Tokenizer": "BPE (Qwen Tokenizer, Vocab ~151k)",
        "Chat Template": "ChatML (<|im_start|>, <|im_end|>)",
        "Language": "다국어 (한국어, 영어, 중국어 등)",
        "Benchmark": "MMLU ~74.2, HumanEval ~75.6 (한국어 가독성 우수)",
        "Hugging Face Link": "[Qwen/Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)"
    },
    "llama3.1:8b": {
        "Tokenizer": "Tiktoken/BPE (Llama-3 Tokenizer, Vocab ~128k)",
        "Chat Template": "Llama-3 Header (<|start_header_id|>, <|eot_id|>)",
        "Language": "다국어 (영어 주력, 한국어 공식 지원)",
        "Benchmark": "MMLU ~68.1, HumanEval ~72.0",
        "Hugging Face Link": "[meta-llama/Llama-3.1-8B-Instruct](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct)"
    }
}

def extract_and_merge_metadata(models: list[str]) -> dict:
    client = ollama.Client()
    ps_response = client.ps()
    
    # 모델별 VRAM 조회
    vram_map = {m.model: m.size_vram for m in ps_response.models}
    
    # 모델명을 Key로 하는 결과 딕셔너리
    combined_results = {}

    for model in models:
        try:
            info = client.show(model)
            details = info.get("details", {})
            model_info = info.get("modelinfo", {}) or {}

            # Context length 추출 (모델이 지원하는 최대 컨텍스트, GGUF 메타데이터 기준)
            context_length = next(
                (v for k, v in model_info.items() if "context_length" in k),
                "확인 불가 (modelinfo에 context_length 키 없음)"
            )

            # VRAM (Byte -> MiB)
            vram_bytes = vram_map.get(model, 0)
            vram_mib = f"{round(vram_bytes / (1024 * 1024), 2)} MiB" if vram_bytes else "N/A (미적재)"

            # 라이선스 첫 줄만 간결하게 정제
            raw_license = info.get("license", "N/A").strip().split("\n")[0][:60]

            # 자동 수집 메타데이터
            auto_data = {
                "Model Name": model,
                "Architecture": details.get("family", "N/A"),
                "Parameter Size": details.get("parameter_size", "N/A"),
                "Quantization": details.get("quantization_level", "N/A"),
                "Context Length": context_length,
                "License": raw_license,
                "VRAM Usage": vram_mib
            }

            # 수동 데이터와 병합
            manual_data = MANUAL_METADATA.get(model, {})
            combined_results[model] = {**auto_data, **manual_data}

        except Exception as e:
            print(f"[{model}] 메타데이터 추출 중 오류 발생: {e}")

    return combined_results


def save_as_vertical_markdown_table(data_dict: dict, output_filepath: str):
    """
    세로 형식 (항목이 Row, 모델이 Column) 마크다운 표 생성 및 저장
    """
    if not data_dict:
        return

    models = list(data_dict.keys())
    # 첫 번째 모델 데이터의 키들을 항목(Row) 목록으로 사용
    feature_keys = list(data_dict[models[0]].keys())

    # 헤더 생성: | 조사 항목 | qwen2.5:7b | llama3.1:8b |
    md_lines = []
    header_line = "| 조사 항목 | " + " | ".join(models) + " |"
    separator_line = "| --- | " + " | ".join(["---"] * len(models)) + " |"
    
    md_lines.append(header_line)
    md_lines.append(separator_line)

    # 행 단위로 항목 값 채우기
    for key in feature_keys:
        row_str = f"| **{key}** | "
        values = [str(data_dict[m].get(key, "N/A")) for m in models]
        row_str += " | ".join(values) + " |"
        md_lines.append(row_str)

    md_table = "\n".join(md_lines)

    # 저장
    Path(output_filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(output_filepath, "w", encoding="utf-8") as f:
        f.write(md_table)

    print(f"✅ 세로형 메타데이터 표 저장 완료: {output_filepath}\n")
    print(md_table)


if __name__ == "__main__":
    target_models = ["qwen2.5:7b", "llama3.1:8b"]
    
    # 메타데이터 추출 및 병합
    merged_data = extract_and_merge_metadata(target_models)

    # JSON 저장 (raw 데이터용)
    with open("results/metadata.json", "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)

    # 세로형 Markdown 표 저장 (제출/README용)
    save_as_vertical_markdown_table(merged_data, "results/model_metadata_table.md")