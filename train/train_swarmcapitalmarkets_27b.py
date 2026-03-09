#!/usr/bin/env python3
"""SwarmCapitalMarkets-27B — bf16 LoRA Training (Unsloth)
=========================================================

bf16 LoRA fine-tune of Qwen3.5-27B on 45K capital markets pairs.
Follows proven SwarmCurator-27B-v1 playbook (same model, same GPU, loss 0.477).

Hardware: RTX PRO 6000 Blackwell (96GB, 350W cap) — GPU 1 on swarmrails
Base model: Qwen/Qwen3.5-27B

Key decisions:
  - bf16 LoRA (NOT QLoRA) — Qwen3.5 has higher quantization error
  - enable_thinking=False — our data has direct responses, no <think> blocks
  - Unsloth — handles GDN attention + gradient checkpointing optimally
  - packing=True — 6x speedup (proven on Curator-27B same model/GPU)
  - 0.6 epoch cap + early stopping patience=3

VRAM budget (96GB):
    27B bf16 model:      ~54 GB
    LoRA adapters:       ~0.5 GB
    Optimizer states:    ~1 GB
    Activations (GC):    ~20 GB (unsloth gradient checkpointing)
    Batch=2 headroom:    ~20 GB
    Total:               ~96 GB

Usage:
    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 python3 train_swarmcapitalmarkets_27b.py --smoke-test
    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 python3 train_swarmcapitalmarkets_27b.py --pilot
    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 python3 train_swarmcapitalmarkets_27b.py
"""

from __future__ import annotations

import argparse
import os
import time

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

MODEL_NAME = "Qwen/Qwen3.5-27B"
TRAIN_FILE = "/data2/swarmcapitalmarkets/swarmcapitalmarkets_train.jsonl"
EVAL_FILE = "/data2/swarmcapitalmarkets/swarmcapitalmarkets_eval.jsonl"
OUTPUT_DIR = "/data2/swarmcapitalmarkets/swarmcapitalmarkets-27b-lora"
MERGED_DIR = "/data2/swarmcapitalmarkets/merged"

# Hyperparameters (proven on SwarmCurator-27B-v1)
LORA_R = 64
LORA_ALPHA = 32
LORA_DROPOUT = 0.0
LEARNING_RATE = 2e-5
MAX_EPOCH_FRACTION = 0.6     # Never full epoch
BATCH_SIZE = 2               # Tight on 96GB with bf16 — fallback to 1 if OOM
GRAD_ACCUM = 16              # Effective batch = 32
MAX_SEQ_LEN = 4096
WARMUP_RATIO = 0.05
WEIGHT_DECAY = 0.01

# Early stopping
EVAL_STEPS = 200
SAVE_STEPS = 200
EARLY_STOPPING_PATIENCE = 3
EARLY_STOPPING_THRESHOLD = 0.001
MAX_EVAL_SAMPLES = 500


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Train SwarmCapitalMarkets-27B")
    parser.add_argument("--smoke-test", action="store_true",
                        help="500 samples, quick validation")
    parser.add_argument("--pilot", action="store_true",
                        help="5000 samples, medium run")
    parser.add_argument("--max-seq-len", type=int, default=MAX_SEQ_LEN)
    parser.add_argument("--resume", type=str,
                        help="Resume from checkpoint path")
    args = parser.parse_args()

    # Imports here so argparse/help works without GPU
    from unsloth import FastLanguageModel
    from transformers import AutoTokenizer, EarlyStoppingCallback
    from trl import SFTTrainer, SFTConfig
    from datasets import load_dataset
    import torch

    print("=" * 70)
    print("  SWARMCAPITALMARKETS-27B — bf16 LoRA (Unsloth)")
    print(f"  Model:      {MODEL_NAME}")
    print(f"  Method:     bf16 LoRA r={LORA_R} alpha={LORA_ALPHA}")
    print(f"  Batch:      {BATCH_SIZE} x {GRAD_ACCUM} = {BATCH_SIZE * GRAD_ACCUM} effective")
    print(f"  Max Seq:    {args.max_seq_len}")
    print(f"  LR:         {LEARNING_RATE}")
    print(f"  Thinking:   DISABLED")
    if args.smoke_test:
        print(f"  Mode:       SMOKE TEST (500 samples)")
    elif args.pilot:
        print(f"  Mode:       PILOT (5000 samples)")
    else:
        print(f"  Mode:       FULL (all samples)")
    print("=" * 70)

    # ─── Model (bf16, NO quantization) ───
    print("\n[1/5] Loading model (27B bf16, ~54GB)...")
    model, _ = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=args.max_seq_len,
        dtype=torch.bfloat16,
        load_in_4bit=False,  # NO QLoRA for Qwen3.5
    )

    # ─── Tokenizer (AutoTokenizer bypass for Qwen3.5 VL dispatch bug) ───
    print("[2/5] Loading tokenizer (AutoTokenizer bypass)...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.padding_side = "right"

    # ─── LoRA ───
    print("[3/5] Applying LoRA...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        bias="none",
        use_gradient_checkpointing="unsloth",
    )
    model.print_trainable_parameters()

    # ─── Dataset ───
    print("[4/5] Loading dataset...")
    train_dataset = load_dataset("json", data_files=str(TRAIN_FILE), split="train")
    eval_dataset = load_dataset("json", data_files=str(EVAL_FILE), split="train")

    if args.smoke_test:
        train_dataset = train_dataset.select(range(min(500, len(train_dataset))))
        eval_dataset = eval_dataset.select(range(min(50, len(eval_dataset))))
    elif args.pilot:
        train_dataset = train_dataset.select(range(min(5000, len(train_dataset))))
        eval_dataset = eval_dataset.select(range(min(200, len(eval_dataset))))

    def format_chat(example):
        """Format messages with thinking DISABLED."""
        text = tokenizer.apply_chat_template(
            example["messages"],
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        return {"text": text}

    train_dataset = train_dataset.map(
        format_chat,
        remove_columns=train_dataset.column_names,
        desc="Formatting train",
        num_proc=16,
    )
    eval_dataset = eval_dataset.map(
        format_chat,
        remove_columns=eval_dataset.column_names,
        desc="Formatting eval",
        num_proc=4,
    )

    # Cap eval set
    if len(eval_dataset) > MAX_EVAL_SAMPLES:
        print(f"  Capping eval from {len(eval_dataset):,} to {MAX_EVAL_SAMPLES}")
        eval_dataset = eval_dataset.select(range(MAX_EVAL_SAMPLES))

    # Calculate steps with 0.6 epoch cap
    eff_batch = BATCH_SIZE * GRAD_ACCUM
    full_epoch_steps = len(train_dataset) // eff_batch
    max_steps = int(full_epoch_steps * MAX_EPOCH_FRACTION)

    print(f"  Train: {len(train_dataset):,} | Eval: {len(eval_dataset):,}")
    print(f"  Eff batch:     {eff_batch}")
    print(f"  Full epoch:    {full_epoch_steps} steps")
    print(f"  Max steps:     {max_steps} (capped at {MAX_EPOCH_FRACTION} epoch)")

    # ─── Trainer ───
    print("[5/5] Configuring trainer...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    t0 = time.time()

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        args=SFTConfig(
            output_dir=OUTPUT_DIR,
            max_steps=max_steps,
            per_device_train_batch_size=BATCH_SIZE,
            per_device_eval_batch_size=1,
            gradient_accumulation_steps=GRAD_ACCUM,
            learning_rate=LEARNING_RATE,
            lr_scheduler_type="cosine",
            warmup_ratio=WARMUP_RATIO,
            weight_decay=WEIGHT_DECAY,
            bf16=True,
            logging_steps=10,
            eval_strategy="steps",
            eval_steps=EVAL_STEPS,
            save_strategy="steps",
            save_steps=SAVE_STEPS,
            save_total_limit=5,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            report_to="none",
            max_seq_length=args.max_seq_len,
            packing=True,
            dataset_text_field="text",
        ),
        callbacks=[
            EarlyStoppingCallback(
                early_stopping_patience=EARLY_STOPPING_PATIENCE,
                early_stopping_threshold=EARLY_STOPPING_THRESHOLD,
            ),
        ],
    )

    # ─── Train ───
    print("\n" + "=" * 70)
    print("  TRAINING START")
    print("=" * 70)

    if args.resume:
        result = trainer.train(resume_from_checkpoint=args.resume)
    else:
        result = trainer.train()

    elapsed = time.time() - t0

    # ─── Save ───
    print("\n" + "=" * 70)
    print(f"  TRAINING COMPLETE")
    print(f"  Loss:    {result.training_loss:.4f}")
    print(f"  Steps:   {result.global_step}")
    print(f"  Time:    {elapsed/3600:.2f}h ({elapsed/60:.0f}m)")
    print("=" * 70)

    # Save adapter
    trainer.model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"  Adapter saved to: {OUTPUT_DIR}")

    # Merge into base model
    print("\n  Merging adapter into base model...")
    os.makedirs(MERGED_DIR, exist_ok=True)
    model.save_pretrained_merged(
        MERGED_DIR,
        tokenizer,
        save_method="merged_16bit",
    )
    print(f"  Merged model saved to: {MERGED_DIR}")
    print(f"  Deploy: vllm serve {MERGED_DIR} --language-model-only --port 8082")
    print("=" * 70)


if __name__ == "__main__":
    main()
