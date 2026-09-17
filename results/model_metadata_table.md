| 조사 항목 | qwen2.5:7b | llama3.1:8b |
| :--- | :--- | :--- |
| **Model Name** | Qwen/Qwen2.5-7B-Instruct | meta-llama/Llama-3.1-8B-Instruct |
| **SHA** | a09a35458c702b33eeacc393d103063234e8bc28 | 0e9e39f249a16976918f6564b8830bc894c89659 |
| **Pipeline_tag** | text-generation | text-generation |
| **Library_name** | transformers | transformers |
| **License** | apache-2.0 | llama3.1 |
| **License_link** | https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE | [Link](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct/blob/main/LICENSE) |
| **Language** | en (공식 모델 카드에 한국어 포함 29개 이상 언어 지원 명시) | en, de, fr, it, pt, hi, es, th (공식 모델 카드에 8개 언어보다 더 많은 언어로 학습되었으며, 그 외 언어로 파인튜닝 가능함을 명시) |
| **Architecture** | qwen2 | llama |
| **Parameter Size** | 7.6B | 8.0B |
| **Quantization** | Q4_K_M | Q4_K_M |
| **Context Length** | 32768 | 131072 |
| **Tokenizer** | GPT2 (pre-tokenizer: qwen2) | GPT2 (pre-tokenizer: llama-bpe) |
| **Vocab Size** | 152064 | 128256 |
| **Benchmark** | IFEval 75.85% / MMLU-PRO 36.52% | IFEval 49.22% / MMLU-PRO 31.09% |
| **GPU** | NVIDIA GeForce RTX 5060 Laptop GPU | NVIDIA GeForce RTX 5060 Laptop GPU |
| **gpu_vram_total_gb** | 7.96 GB | 7.96 GB |
| **offload_status** | 100% GPU | 100% GPU |
| **vram_usage** | 4528.1 MiB | 5027.5 MiB |