#!/usr/bin/env python3
"""Merge LoRA adapter into base model for vLLM deployment.

Usage:
    python3 merge_swarmcapitalmarkets_27b.py
"""

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE_MODEL = "Qwen/Qwen3.5-27B"
LORA_DIR = "/data2/swarmcapitalmarkets/swarmcapitalmarkets-27b-lora"
MERGED_DIR = "/data2/swarmcapitalmarkets/merged"


def main():
    print("Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)

    print("Loading base model in bf16 (CPU)...")
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        trust_remote_code=True,
    )

    print(f"Loading LoRA adapter from {LORA_DIR}...")
    model = PeftModel.from_pretrained(base_model, LORA_DIR)

    print("Merging adapter into base model...")
    merged_model = model.merge_and_unload()

    print(f"Saving merged model to {MERGED_DIR}...")
    merged_model.save_pretrained(MERGED_DIR, safe_serialization=True)
    tokenizer.save_pretrained(MERGED_DIR)

    print("Done. Deploy with:")
    print(f"  vllm serve {MERGED_DIR} --language-model-only --port 8082")


if __name__ == "__main__":
    main()
