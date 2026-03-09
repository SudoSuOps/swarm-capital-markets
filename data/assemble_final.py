#!/usr/bin/env python3
"""SwarmCapitalMarkets Final Assembly Pipeline
================================================

5-Pool Assembly → Dedup → Weighted Blend → Contrastive Rebalance → Eval Holdout → 2x Shuffle → Audit

Blend weights (5 pools):
  Diversified tasks      60%  (cook streams minus macro)
  RPA trajectories       25%  (multi-trajectory reasoning)
  Macro causality         8%  (temporal evolution + cross-stream)
  Golden conceptual       4%  (hand-crafted knowledge pairs)
  Platinum mutations      3%  (hedge-fund grade variants)

Contrastive rebalancing by difficulty tier:
  Bronze   → 20%   (upsample — forces basic CRE reasoning)
  Silver   → 20%   (upsample — moderate complexity)
  Gold     → 15%   (upsample — advanced multi-step)
  High     → 30%   (downsample from ~47% — deep analysis)
  Platinum → 15%   (upsample — edge-case decision intelligence)

Start-phrase threshold: top start phrase < 4%
Eval holdout: 500 pairs stratified by difficulty, never trained on.

Usage:
    python3 -m data.swarmcre_dataset.assemble_final
    python3 -m data.swarmcre_dataset.assemble_final --dry-run
    python3 -m data.swarmcre_dataset.assemble_final --no-rebalance
    python3 -m data.swarmcre_dataset.assemble_final --eval-size 1000
"""

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════════════

COOK_DIR = Path("/home/swarm/swarm_cooks/cre_capital")

# Diversified task streams (excludes macro_causality — separate pool)
STREAM_FILES = [
    COOK_DIR / "stream_debt_maturity.jsonl",
    COOK_DIR / "stream_cmbs_distress.jsonl",
    COOK_DIR / "stream_rate_advisory.jsonl",
    COOK_DIR / "stream_equity_advisory.jsonl",
    COOK_DIR / "stream_valuation_advisory.jsonl",
    COOK_DIR / "stream_deal_origination.jsonl",
]
MACRO_FILE = COOK_DIR / "stream_macro_causality.jsonl"
DEAL_GRAPH_FILE = COOK_DIR / "stream_deal_graph.jsonl"
RPA_FILE = COOK_DIR / "rpa_pairs.jsonl"
GOLDEN_FILE = COOK_DIR / "golden_pairs.jsonl"
MUTATION_FILE = COOK_DIR / "platinum_mutations.jsonl"
OUTPUT_TRAIN = COOK_DIR / "swarmcapitalmarkets_train.jsonl"
OUTPUT_EVAL = COOK_DIR / "swarmcapitalmarkets_eval.jsonl"
OUTPUT_MANIFEST = COOK_DIR / "assembly_manifest.json"

# 5-pool blend weights
W_DIVERSIFIED = 0.60  # Core task streams
W_RPA = 0.25          # Multi-trajectory reasoning
W_MACRO = 0.08        # Temporal evolution + cross-stream
W_GOLDEN = 0.04       # Hand-crafted conceptual pairs
W_MUTATION = 0.03     # Platinum mutation variants

# Contrastive rebalancing weights by difficulty tier
TIER_WEIGHTS = {
    "bronze": 0.20,
    "silver": 0.20,
    "gold": 0.15,
    "high": 0.30,
    "platinum": 0.15,
}

# Map medium → high for rebalancing (medium is functionally high-adjacent)
TIER_NORMALIZE = {
    "medium": "high",
    "unknown": "high",
}

EVAL_SIZE = 500


# ═══════════════════════════════════════════════════════════════════════════════
# LOAD
# ═══════════════════════════════════════════════════════════════════════════════

def load_jsonl(path: Path, source_tag: str) -> list[dict]:
    if not path.exists():
        print(f"  SKIP {path.name} — not found")
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rec.setdefault("metadata", {})["blend_source"] = source_tag
                records.append(rec)
            except json.JSONDecodeError:
                continue
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# DEDUP
# ═══════════════════════════════════════════════════════════════════════════════

def fingerprint(rec: dict) -> str:
    for m in rec.get("messages", []):
        if m.get("role") == "assistant":
            text = re.sub(r'\s+', ' ', m["content"][:500]).strip().lower()
            return hashlib.md5(text.encode()).hexdigest()
    return hashlib.md5(json.dumps(rec).encode()).hexdigest()


def dedup(records: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for rec in records:
        fp = fingerprint(rec)
        if fp not in seen:
            seen.add(fp)
            unique.append(rec)
    return unique


# ═══════════════════════════════════════════════════════════════════════════════
# DIFFICULTY TIER EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def get_tier(rec: dict) -> str:
    """Extract normalized difficulty tier from a record."""
    raw = rec.get("difficulty",
                  rec.get("metadata", {}).get("difficulty", "unknown"))
    raw = raw.lower().strip()
    return TIER_NORMALIZE.get(raw, raw)


# ═══════════════════════════════════════════════════════════════════════════════
# BLEND — upsample minority sources to target weights
# ═══════════════════════════════════════════════════════════════════════════════

def blend(diversified: list[dict], rpa: list[dict], macro: list[dict],
          golden: list[dict], mutations: list[dict],
          rng: random.Random) -> list[dict]:
    """5-pool blend with target weights. Diversified is the anchor pool."""
    n_div = len(diversified)
    total_target = int(n_div / W_DIVERSIFIED)
    targets = {
        "diversified": n_div,
        "rpa": int(total_target * W_RPA),
        "macro": int(total_target * W_MACRO),
        "golden": int(total_target * W_GOLDEN),
        "mutation": int(total_target * W_MUTATION),
    }
    pools = {
        "rpa": rpa,
        "macro": macro,
        "golden": golden,
        "mutation": mutations,
    }

    print(f"\n  5-Pool Blend targets (total {total_target:,}):")
    print(f"    Diversified: {n_div:>7,} ({W_DIVERSIFIED*100:.0f}%)")
    for name, target in targets.items():
        if name == "diversified":
            continue
        avail = len(pools[name])
        weight = {"rpa": W_RPA, "macro": W_MACRO, "golden": W_GOLDEN, "mutation": W_MUTATION}[name]
        print(f"    {name:12s}: {target:>7,} target from {avail:,} unique ({weight*100:.0f}%)")

    def upsample(records, target, label):
        if not records or target <= 0:
            return []
        if len(records) >= target:
            rng.shuffle(records)
            return records[:target]
        upsampled = []
        cycles = (target // len(records)) + 1
        for cycle in range(cycles):
            for rec in records:
                variant = json.loads(json.dumps(rec))
                variant.setdefault("metadata", {})["upsample_cycle"] = cycle
                if cycle > 0:
                    variant["id"] = f"{variant.get('id', label)}-up{cycle}"
                upsampled.append(variant)
        return upsampled[:target]

    result = list(diversified)
    for name, pool in pools.items():
        blended = upsample(pool, targets[name], name)
        print(f"    {name:12s}: {len(blended):,} blended (from {len(pool):,} unique)")
        result.extend(blended)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRASTIVE REBALANCING — resample by difficulty tier
# ═══════════════════════════════════════════════════════════════════════════════

def contrastive_rebalance(records: list[dict], rng: random.Random) -> list[dict]:
    """Rebalance dataset by difficulty tier weights.
    Upsamples minority tiers, downsamples majority tiers.
    Shuffles within each band first, then combines."""

    # Bucket by tier
    buckets = {}
    for rec in records:
        tier = get_tier(rec)
        buckets.setdefault(tier, []).append(rec)

    print(f"\n  Raw tier distribution:")
    for tier in TIER_WEIGHTS:
        n = len(buckets.get(tier, []))
        pct = n / max(len(records), 1) * 100
        target_pct = TIER_WEIGHTS[tier] * 100
        print(f"    {tier:12s} {n:>6,} ({pct:>5.1f}%) → target {target_pct:.0f}%")

    # Merge any tiers not in TIER_WEIGHTS into "high"
    for tier in list(buckets.keys()):
        if tier not in TIER_WEIGHTS:
            buckets.setdefault("high", []).extend(buckets.pop(tier))
            print(f"    (merged '{tier}' → 'high')")

    # Calculate target sizes
    # Use total of available records as base — don't inflate beyond what we have
    total_available = len(records)
    rebalanced = []

    for tier, weight in TIER_WEIGHTS.items():
        target_n = int(total_available * weight)
        pool = buckets.get(tier, [])

        if not pool:
            print(f"    {tier}: EMPTY — skipping (would need {target_n:,})")
            continue

        # Shuffle within band first
        rng.shuffle(pool)

        if len(pool) >= target_n:
            # Downsample — take a random subset
            selected = pool[:target_n]
        else:
            # Upsample — cycle through with unique markers
            selected = []
            cycles = (target_n // len(pool)) + 1
            for cycle in range(cycles):
                for rec in pool:
                    if len(selected) >= target_n:
                        break
                    if cycle == 0:
                        selected.append(rec)
                    else:
                        variant = json.loads(json.dumps(rec))
                        variant.setdefault("metadata", {})["rebalance_cycle"] = cycle
                        variant["id"] = f"{variant.get('id', tier)}-rb{cycle}"
                        selected.append(variant)

        rebalanced.extend(selected)

    print(f"\n  Rebalanced tier distribution:")
    tier_counts = Counter(get_tier(r) for r in rebalanced)
    for tier in TIER_WEIGHTS:
        n = tier_counts.get(tier, 0)
        pct = n / max(len(rebalanced), 1) * 100
        print(f"    {tier:12s} {n:>6,} ({pct:>5.1f}%)")

    return rebalanced


# ═══════════════════════════════════════════════════════════════════════════════
# EVAL HOLDOUT — stratified by difficulty, task type, stream
# ═══════════════════════════════════════════════════════════════════════════════

def eval_holdout(records: list[dict], eval_size: int,
                 rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Hold out eval_size pairs, stratified by difficulty tier.
    Returns (train_set, eval_set)."""

    # Bucket by tier
    buckets = {}
    for rec in records:
        tier = get_tier(rec)
        buckets.setdefault(tier, []).append(rec)

    eval_set = []
    train_set = []

    # Proportional stratified sampling from each tier
    for tier, pool in buckets.items():
        rng.shuffle(pool)
        # Proportional eval allocation
        tier_eval_n = max(1, int(eval_size * len(pool) / len(records)))
        tier_eval_n = min(tier_eval_n, len(pool) // 2)  # never take more than half

        eval_set.extend(pool[:tier_eval_n])
        train_set.extend(pool[tier_eval_n:])

    # Trim eval to exact size
    rng.shuffle(eval_set)
    if len(eval_set) > eval_size:
        # Move excess back to train
        train_set.extend(eval_set[eval_size:])
        eval_set = eval_set[:eval_size]
    elif len(eval_set) < eval_size:
        # Pull more from train
        deficit = eval_size - len(eval_set)
        rng.shuffle(train_set)
        eval_set.extend(train_set[:deficit])
        train_set = train_set[deficit:]

    return train_set, eval_set


# ═══════════════════════════════════════════════════════════════════════════════
# START-PHRASE AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def start_phrase_audit(records: list[dict]) -> dict:
    starts_5 = []
    starts_3 = []
    for rec in records:
        for m in rec.get("messages", []):
            if m.get("role") == "assistant":
                tokens = m["content"].split()
                starts_5.append(" ".join(tokens[:5]).lower().strip("*#"))
                starts_3.append(" ".join(tokens[:3]).lower().strip("*#"))
                break

    c5 = Counter(starts_5)
    c3 = Counter(starts_3)
    total = len(starts_5)

    top1_5 = c5.most_common(1)[0][1] / total * 100 if total else 0
    top1_3 = c3.most_common(1)[0][1] / total * 100 if total else 0
    top5_5 = sum(c for _, c in c5.most_common(5)) / total * 100 if total else 0
    top10_5 = sum(c for _, c in c5.most_common(10)) / total * 100 if total else 0

    return {
        "total": total,
        "unique_5token": len(c5),
        "unique_3token": len(c3),
        "diversity_ratio_5": len(c5) / max(total, 1),
        "top1_5token_pct": top1_5,
        "top1_3token_pct": top1_3,
        "top5_5token_pct": top5_5,
        "top10_5token_pct": top10_5,
        "top1_5token_pass": top1_5 < 4.0,
        "top15": c5.most_common(15),
        "top10_3token": c3.most_common(10),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STRUCTURAL AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def structural_audit(records: list[dict]) -> dict:
    formats = Counter()
    streams = Counter()
    task_types = Counter()
    blend_sources = Counter()
    difficulty_tiers = Counter()
    lengths = []

    for rec in records:
        meta = rec.get("metadata", {})
        formats[meta.get("format", "unknown")] += 1
        streams[meta.get("stream", meta.get("source", "unknown"))] += 1
        task_types[meta.get("task_type", "unknown")] += 1
        blend_sources[meta.get("blend_source", "unknown")] += 1
        difficulty_tiers[get_tier(rec)] += 1

        for m in rec.get("messages", []):
            if m.get("role") == "assistant":
                lengths.append(len(m["content"]))
                break

    avg_len = sum(lengths) / max(len(lengths), 1)
    return {
        "formats": dict(formats.most_common()),
        "streams": dict(streams.most_common()),
        "task_types": dict(task_types.most_common(20)),
        "blend_sources": dict(blend_sources.most_common()),
        "difficulty_tiers": dict(difficulty_tiers.most_common()),
        "avg_response_length": int(avg_len),
        "min_response_length": min(lengths) if lengths else 0,
        "max_response_length": max(lengths) if lengths else 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Assemble final SwarmCapitalMarkets training JSONL")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report stats without writing")
    parser.add_argument("--no-upsample", action="store_true",
                        help="Skip source upsampling, use raw counts")
    parser.add_argument("--no-rebalance", action="store_true",
                        help="Skip contrastive difficulty rebalancing")
    parser.add_argument("--eval-size", type=int, default=EVAL_SIZE,
                        help="Eval holdout size (default: 500)")
    parser.add_argument("--seed", type=int, default=2026,
                        help="Shuffle seed")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    print("=" * 70)
    print("  SWARMCAPITALMARKETS — FINAL ASSEMBLY PIPELINE")
    print("  Cook → Dedup → Blend → Rebalance → Eval Holdout → Shuffle → Audit")
    print("=" * 70)

    # ── Stage 1: Load 5 pools ──
    print("\n[1/8] LOADING 5 POOLS")

    # Pool 1: Diversified task streams (60%)
    diversified = []
    for sf in STREAM_FILES:
        recs = load_jsonl(sf, "diversified")
        print(f"  {sf.name}: {len(recs):,} pairs")
        diversified.extend(recs)
    print(f"  Pool 1 — Diversified: {len(diversified):,}")

    # Pool 2: RPA multi-trajectory reasoning (25%)
    rpa = load_jsonl(RPA_FILE, "rpa")
    print(f"  Pool 2 — RPA:         {len(rpa):,}")

    # Pool 3: Macro causality + Deal graphs (8%)
    macro = load_jsonl(MACRO_FILE, "macro")
    deal_graphs = load_jsonl(DEAL_GRAPH_FILE, "macro")  # Same pool as macro
    macro.extend(deal_graphs)
    print(f"  Pool 3 — Macro+Graph: {len(macro):,} (macro={len(macro)-len(deal_graphs):,}, graphs={len(deal_graphs):,})")

    # ── Stage 2: Load golden + mutations ──
    print("\n[2/8] LOADING GOLDEN + MUTATIONS")
    # Pool 4: Golden conceptual (4%)
    golden = load_jsonl(GOLDEN_FILE, "golden")
    print(f"  Pool 4 — Golden:      {len(golden):,}")
    # Pool 5: Platinum mutations (3%)
    mutations = load_jsonl(MUTATION_FILE, "mutation")
    print(f"  Pool 5 — Mutations:   {len(mutations):,}")

    total_raw = len(diversified) + len(rpa) + len(macro) + len(golden) + len(mutations)
    print(f"\n  Total raw: {total_raw:,}")

    # ── Stage 3: Dedup (per pool) ──
    print("\n[3/8] DEDUP")
    pre_dedup = total_raw
    diversified = dedup(diversified)
    rpa = dedup(rpa)
    macro = dedup(macro)
    golden = dedup(golden)
    mutations = dedup(mutations)
    post_dedup = len(diversified) + len(rpa) + len(macro) + len(golden) + len(mutations)
    print(f"  Before: {pre_dedup:,} → After: {post_dedup:,} "
          f"(removed {pre_dedup - post_dedup:,})")

    # ── Stage 4: 5-Pool weighted blend (60/25/8/4/3) ──
    print("\n[4/8] 5-POOL WEIGHTED BLEND")
    if args.no_upsample:
        combined = diversified + rpa + macro + golden + mutations
        print(f"  Raw blend (no upsample): {len(combined):,}")
    else:
        combined = blend(diversified, rpa, macro, golden, mutations, rng)
    print(f"  Combined: {len(combined):,}")

    # ── Stage 5: Contrastive rebalancing ──
    if args.no_rebalance:
        print("\n[5/8] CONTRASTIVE REBALANCE — SKIPPED")
    else:
        print("\n[5/8] CONTRASTIVE REBALANCE BY DIFFICULTY TIER")
        combined = contrastive_rebalance(combined, rng)
    print(f"  Dataset size: {len(combined):,}")

    # ── Stage 6: Eval holdout ──
    print(f"\n[6/8] EVAL HOLDOUT — {args.eval_size} pairs (stratified)")
    train_set, eval_set = eval_holdout(combined, args.eval_size, rng)

    eval_tiers = Counter(get_tier(r) for r in eval_set)
    print(f"  Eval set: {len(eval_set):,} pairs")
    for tier, count in eval_tiers.most_common():
        print(f"    {tier:12s} {count:>4}")
    print(f"  Train set: {len(train_set):,} pairs")

    # ── Stage 7: Global shuffle (within-band then global) ──
    print(f"\n[7/8] GLOBAL SHUFFLE")
    # Shuffle within difficulty bands first
    train_buckets = {}
    for rec in train_set:
        tier = get_tier(rec)
        train_buckets.setdefault(tier, []).append(rec)
    for tier, pool in train_buckets.items():
        rng.shuffle(pool)

    # Recombine and global shuffle
    train_set = []
    for pool in train_buckets.values():
        train_set.extend(pool)
    rng.shuffle(train_set)
    rng.shuffle(eval_set)
    print(f"  Shuffled {len(train_set):,} train + {len(eval_set):,} eval "
          f"(seed={args.seed})")

    # ── Stage 8: Final audit ──
    print(f"\n[8/8] FINAL HEALTH CHECK")

    # Start-phrase audit
    sp = start_phrase_audit(train_set)
    print(f"\n  START-PHRASE ENTROPY (train):")
    print(f"    Total responses:    {sp['total']:,}")
    print(f"    Unique 5-token:     {sp['unique_5token']:,} "
          f"({sp['diversity_ratio_5']:.4f})")
    print(f"    Unique 3-token:     {sp['unique_3token']:,}")
    t1_5_status = "PASS" if sp["top1_5token_pass"] else "FAIL (<4% required)"
    t1_3_status = "PASS" if sp["top1_3token_pct"] < 10 else "WARNING"
    t5_status = "PASS" if sp["top5_5token_pct"] < 15 else "WARNING"
    t10_status = "PASS" if sp["top10_5token_pct"] < 25 else "WARNING"
    print(f"    Top-1 (5-token):    {sp['top1_5token_pct']:.2f}%  {t1_5_status}")
    print(f"    Top-1 (3-token):    {sp['top1_3token_pct']:.2f}%  {t1_3_status}")
    print(f"    Top-5 (5-token):    {sp['top5_5token_pct']:.2f}%  {t5_status}")
    print(f"    Top-10 (5-token):   {sp['top10_5token_pct']:.2f}%  {t10_status}")

    print(f"\n    Top 15 start phrases (5-token):")
    for i, (phrase, count) in enumerate(sp["top15"], 1):
        pct = count / sp["total"] * 100
        print(f"      {i:<3} {count:>5} {pct:>5.2f}%  {phrase[:60]}")

    # Structural audit
    sa = structural_audit(train_set)
    print(f"\n  STRUCTURAL AUDIT (train):")
    print(f"    Avg response length: {sa['avg_response_length']:,} chars")
    print(f"    Min/Max: {sa['min_response_length']:,} / "
          f"{sa['max_response_length']:,}")

    print(f"\n    Blend sources:")
    for src, count in sa["blend_sources"].items():
        pct = count / len(train_set) * 100
        print(f"      {src:15s} {count:>6,} ({pct:.1f}%)")

    print(f"\n    Difficulty tiers:")
    for tier, count in sa["difficulty_tiers"].items():
        pct = count / len(train_set) * 100
        target = TIER_WEIGHTS.get(tier, 0) * 100
        delta = pct - target
        marker = f" (target {target:.0f}%, delta {delta:+.1f}%)"
        print(f"      {tier:12s} {count:>6,} ({pct:.1f}%){marker}")

    print(f"\n    Format distribution:")
    for fmt, count in sa["formats"].items():
        pct = count / len(train_set) * 100
        print(f"      {fmt:30s} {count:>6,} ({pct:.1f}%)")

    print(f"\n    Stream distribution:")
    for stream, count in sa["streams"].items():
        pct = count / len(train_set) * 100
        print(f"      {stream:30s} {count:>6,} ({pct:.1f}%)")

    print(f"\n    Task types (top 20):")
    for tt, count in list(sa["task_types"].items())[:20]:
        pct = count / len(train_set) * 100
        print(f"      {tt:30s} {count:>6,} ({pct:.1f}%)")

    # ── Write ──
    if args.dry_run:
        print(f"\n  DRY RUN — not writing.")
        print(f"  Would produce: {len(train_set):,} train + "
              f"{len(eval_set):,} eval")
    else:
        print(f"\n  WRITING TRAIN: {OUTPUT_TRAIN}")
        with open(OUTPUT_TRAIN, "w") as f:
            for rec in train_set:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        train_mb = OUTPUT_TRAIN.stat().st_size / (1024 * 1024)
        print(f"  Done: {len(train_set):,} pairs, {train_mb:.1f} MB")

        print(f"  WRITING EVAL:  {OUTPUT_EVAL}")
        with open(OUTPUT_EVAL, "w") as f:
            for rec in eval_set:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        eval_mb = OUTPUT_EVAL.stat().st_size / (1024 * 1024)
        print(f"  Done: {len(eval_set):,} pairs, {eval_mb:.1f} MB")

        # Write manifest
        manifest = {
            "version": "v2",
            "assembled_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(),
            "seed": args.seed,
            "train_pairs": len(train_set),
            "eval_pairs": len(eval_set),
            "total_pairs": len(train_set) + len(eval_set),
            "blend_weights": {
                "diversified": W_DIVERSIFIED,
                "rpa": W_RPA,
                "macro": W_MACRO,
                "golden": W_GOLDEN,
                "mutation": W_MUTATION,
            },
            "tier_weights": TIER_WEIGHTS,
            "rebalanced": not args.no_rebalance,
            "start_phrase_audit": {
                "top1_5token_pct": round(sp["top1_5token_pct"], 2),
                "top1_3token_pct": round(sp["top1_3token_pct"], 2),
                "unique_5token": sp["unique_5token"],
                "pass": sp["top1_5token_pass"],
            },
            "difficulty_distribution": sa["difficulty_tiers"],
            "blend_distribution": sa["blend_sources"],
            "format_distribution": sa["formats"],
            "train_file": str(OUTPUT_TRAIN),
            "eval_file": str(OUTPUT_EVAL),
        }
        with open(OUTPUT_MANIFEST, "w") as f:
            json.dump(manifest, f, indent=2)
        print(f"  MANIFEST:      {OUTPUT_MANIFEST}")

    # ── Summary ──
    all_pass = (
        sp["top1_5token_pass"]
        and sp["top1_3token_pct"] < 10
        and sp["top5_5token_pct"] < 15
    )

    print(f"\n{'=' * 70}")
    print(f"  ASSEMBLY {'COMPLETE' if not args.dry_run else 'PREVIEW'}")
    print(f"  Train pairs:    {len(train_set):,}")
    print(f"  Eval pairs:     {len(eval_set):,} (held out — never train)")
    print(f"  Rebalanced:     {'YES' if not args.no_rebalance else 'NO'}")
    print(f"  Diversified:    {sa['blend_sources'].get('diversified', 0):,} (60%)")
    print(f"  RPA:            {sa['blend_sources'].get('rpa', 0):,} (25%)")
    print(f"  Macro:          {sa['blend_sources'].get('macro', 0):,} (8%)")
    print(f"  Golden:         {sa['blend_sources'].get('golden', 0):,} (4%)")
    print(f"  Mutation:       {sa['blend_sources'].get('mutation', 0):,} (3%)")
    print(f"  Start-phrase:   {'ALL PASS' if all_pass else 'CHECK WARNINGS'}")
    if not args.dry_run:
        print(f"  Train output:   {OUTPUT_TRAIN}")
        print(f"  Eval output:    {OUTPUT_EVAL}")
    print(f"{'=' * 70}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
