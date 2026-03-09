#!/usr/bin/env python3
"""Cook Golden Pairs — 109 unique CRE prompts → high-entropy training pairs.

Sources:
  - 88 knowledge pairs (fundamentals, underwriting, cap structure, distressed, governance, tax)
  - 11 decision pairs (lending, acquisition, restructuring)
  - 13 scenario simulations (rate shocks, credit spreads, distress cycles)

Each prompt gets 3 response variants with different system personas and formats.
Total output: ~327 high-entropy pairs (109 × 3 variants).

These pairs break structural monotony when blended with the 30K deal-template pairs.

Usage:
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_golden_pairs
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_golden_pairs --dry-run
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_golden_pairs --workers 20
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

TOGETHER_URL = "https://api.together.xyz/v1/chat/completions"
MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
GOLDEN_PROMPTS = Path("/home/swarm/swarm_cooks/cre_capital/golden_prompts.jsonl")
OUTPUT_FILE = Path("/home/swarm/swarm_cooks/cre_capital/golden_pairs.jsonl")
VARIANTS_PER_PROMPT = 3

session = requests.Session()
api_lock = threading.Lock()
file_lock = threading.Lock()
stats = Counter()

# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PERSONAS — each variant gets a different perspective
# ═══════════════════════════════════════════════════════════════════════════════

PERSONAS = [
    {
        "name": "senior_advisor",
        "system": """You are SwarmCapital — a senior capital markets advisor with 30 years of institutional CRE experience. $8B+ in closed transactions. You explain CRE concepts with precision and depth, using real numbers, real market data, and the voice of someone who has lived through multiple cycles (GFC, COVID, 2026 maturity wall).

Your analysis is consumed by AI agents and institutional investors. Be precise with every basis point.

When the question involves calculations, show every step. When it involves judgment, explain your reasoning like you're presenting to an investment committee.""",
    },
    {
        "name": "quantitative_analyst",
        "system": """You are SwarmCapital — a quantitative CRE analyst who thinks in models, formulas, and risk frameworks. You build DCF models, sensitivity matrices, and Monte Carlo simulations for institutional capital allocators.

For every concept, show the math. For every decision, quantify the risk. Use tables when comparing options. Use formulas when explaining calculations. Reference specific market data points (spreads, delinquency rates, cap rates by market).

2026 context: SOFR 4.25-4.50%, 10yr Treasury 4.10-4.35%, CMBS delinquency office 11.2%, $1.5T maturity wall.""",
    },
    {
        "name": "deal_practitioner",
        "system": """You are SwarmCapital — a hands-on CRE deal practitioner. You've structured JV equity, negotiated loan workouts, managed CMBS special servicing, and closed distressed acquisitions. You think in terms of actual deal mechanics, not theory.

Share war stories and practical insights. What actually happens vs. what textbooks say. What kills deals. What saves them. How borrowers and lenders actually negotiate. What the documents actually say.

Keep it direct and practical. Use specific examples. Reference real market conditions (2026 maturity wall, office distress wave, rate environment).""",
    },
    {
        "name": "risk_officer",
        "system": """You are SwarmCapital — a chief risk officer at a major CRE lender. You evaluate credit risk, concentration risk, and systemic risk across a multi-billion dollar loan portfolio.

Your job is to find the risks others miss. Stress test every assumption. Challenge every projection. Identify the scenarios where deals break. You've seen the GFC, you've seen COVID, and you're watching the 2026 maturity wall unfold in real time.

Be thorough and skeptical. Quantify downside scenarios. Flag structural weaknesses. But also identify when risk is being overpriced — distressed opportunities exist.""",
    },
    {
        "name": "investment_committee",
        "system": """You are SwarmCapital — an investment committee presenting findings to LPs and institutional allocators. Your analysis must be clear, structured, and defensible.

Structure your response as an IC presentation: thesis, market context, financial analysis, risk assessment, recommendation. Use professional language appropriate for a $500M+ fund. Include specific data points and comparables.

2026 context: SOFR 4.25-4.50%, office CMBS delinquency 11.2%, multifamily 6.85% (up 239bps YoY), industrial 0.67%. $4.7T maturing 2024-2028.""",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# FORMAT VARIATION — different output structures per variant
# ═══════════════════════════════════════════════════════════════════════════════

FORMAT_SUFFIXES = [
    "",  # No format constraint — natural response
    "\n\nStructure your response with clear sections and use specific numbers throughout.",
    "\n\nRespond in a direct, conversational tone as if explaining to a CRE professional in a meeting. No markdown headers.",
    "\n\nProvide your analysis as a structured memo with numbered sections.",
    "\n\nLead with your conclusion, then support it with analysis and calculations.",
    "\n\nUse tables where comparisons are needed. Be concise — let the numbers tell the story.",
    "\n\nExplain this as if teaching a junior analyst. Build from first principles to advanced implications.",
    "\n\nStart with the math. Show every calculation step, then interpret the results.",
]

# ═══════════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════════

def together_call(system: str, user: str, max_tokens: int = 4096,
                  temperature: float = 0.7, retries: int = 3) -> str | None:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    for attempt in range(retries):
        try:
            resp = session.post(TOGETHER_URL, json=payload, timeout=180)
            with api_lock:
                stats["api_calls"] += 1
            if resp.status_code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            if resp.status_code in (402, 403):
                raise RuntimeError(f"{resp.status_code} — API key issue")
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
            if content and len(content) > 200:
                return content
        except RuntimeError:
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt + 1)
            else:
                with api_lock:
                    stats["errors"] += 1
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# QUALITY CHECK — lightweight, format-agnostic
# ═══════════════════════════════════════════════════════════════════════════════

_NUM_PAT = re.compile(r"\$[\d,.]+|\d+\.?\d*\s*%|\d{1,3}(,\d{3})+")
_DEGEN_PAT = re.compile(r"(.{40,})\1{2,}")


def quality_ok(content: str) -> bool:
    if len(content) < 300:
        return False
    if _DEGEN_PAT.search(content):
        return False
    # Knowledge pairs need financial content
    nums = _NUM_PAT.findall(content)
    if len(nums) < 1:
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA VALIDATORS — ported from repo schemas.js
# ═══════════════════════════════════════════════════════════════════════════════

VALID_DECISIONS = {"approve", "approve_with_conditions", "restructure",
                   "decline", "watchlist", "distressed_opportunity"}
VALID_ASSETS = {"office", "industrial", "multifamily", "retail", "hotel",
                "data_center", "mixed_use", "cold_storage"}
VALID_CONSTRAINTS = {"ltv", "dscr", "debt_yield"}


def validate_decision_output(content: str) -> tuple[bool, list[str]]:
    """Validate structured decision output against canonical schema."""
    errors = []
    try:
        # Try to extract JSON from content
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            return True, []  # Not a JSON response — skip validation
        obj = json.loads(json_match.group())
    except (json.JSONDecodeError, AttributeError):
        return True, []  # Not JSON — skip

    # Only validate if it looks like a decision output
    if "decision" not in obj and "confidence" not in obj:
        return True, []

    if "decision" in obj and obj["decision"] not in VALID_DECISIONS:
        errors.append(f"invalid_decision: {obj['decision']}")
    if "confidence" in obj:
        c = obj["confidence"]
        if not isinstance(c, (int, float)) or c < 0 or c > 1:
            errors.append(f"confidence_out_of_range: {c}")
    if "analysis" in obj and isinstance(obj["analysis"], dict):
        a = obj["analysis"]
        if "dscr" in a and (a["dscr"] < 0 or a["dscr"] > 5):
            errors.append(f"dscr_out_of_range: {a['dscr']}")
        if "ltv" in a and (a["ltv"] < 0 or a["ltv"] > 1.5):
            errors.append(f"ltv_out_of_range: {a['ltv']}")
        if "cap_rate" in a and (a["cap_rate"] < 0 or a["cap_rate"] > 0.30):
            errors.append(f"cap_rate_out_of_range: {a['cap_rate']}")
        if "debt_yield" in a and (a["debt_yield"] < 0 or a["debt_yield"] > 0.30):
            errors.append(f"debt_yield_out_of_range: {a['debt_yield']}")
        if "binding_constraint" in a and a["binding_constraint"] not in VALID_CONSTRAINTS:
            errors.append(f"invalid_binding_constraint: {a['binding_constraint']}")
    if "risk_flags" in obj:
        if not isinstance(obj["risk_flags"], list):
            errors.append("risk_flags_not_array")
        elif len(obj["risk_flags"]) > 5:
            errors.append(f"too_many_risk_flags: {len(obj['risk_flags'])}")

    return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════════════════════════════
# PAIR GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def cook_variant(prompt_rec: dict, variant_idx: int, rng: random.Random) -> dict | None:
    persona = PERSONAS[variant_idx % len(PERSONAS)]
    fmt_suffix = rng.choice(FORMAT_SUFFIXES)

    user_msg = prompt_rec["prompt"] + fmt_suffix

    content = together_call(persona["system"], user_msg)
    if not content or not quality_ok(content):
        with api_lock:
            stats["failed"] += 1
        return None

    # Schema validation for decision-type outputs
    valid, schema_errors = validate_decision_output(content)
    if not valid:
        with api_lock:
            stats["schema_errors"] += 1
        # Still keep it — log the errors in metadata

    with api_lock:
        stats["written"] += 1

    return {
        "id": f"golden-{prompt_rec['id']}-v{variant_idx}",
        "task_type": prompt_rec.get("category", "knowledge"),
        "difficulty": prompt_rec.get("difficulty", "high"),
        "messages": [
            {"role": "system", "content": persona["system"]},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": content},
        ],
        "metadata": {
            "source": "golden-pairs-v1",
            "base_prompt_id": prompt_rec["id"],
            "prompt_source": prompt_rec.get("source", "unknown"),
            "persona": persona["name"],
            "variant": variant_idx,
            "model": MODEL,
            "format": "freeform",
            "schema_valid": valid,
            "schema_errors": schema_errors if schema_errors else None,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Cook golden CRE knowledge pairs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--variants", type=int, default=VARIANTS_PER_PROMPT)
    args = parser.parse_args()

    api_key = os.environ.get("TOGETHER_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: Set TOGETHER_KEY environment variable")
        sys.exit(1)

    if api_key:
        session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    # Load prompts
    prompts = []
    with open(GOLDEN_PROMPTS) as f:
        for line in f:
            prompts.append(json.loads(line.strip()))

    total = len(prompts) * args.variants
    print(f"{'='*70}")
    print(f"  GOLDEN PAIRS COOK — {len(prompts)} prompts × {args.variants} variants = {total} pairs")
    print(f"  Model: {MODEL}")
    print(f"  Workers: {args.workers}")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"{'='*70}")

    if args.dry_run:
        from collections import Counter as C
        sources = C(p.get("source", "?") for p in prompts)
        diffs = C(p.get("difficulty", "?") for p in prompts)
        print(f"\n  Sources:")
        for s, c in sources.most_common():
            print(f"    {s:40s} {c:3d} → {c * args.variants} pairs")
        print(f"\n  Difficulties:")
        for d, c in diffs.most_common():
            print(f"    {d:15s} {c:3d}")
        print(f"\n  Personas: {', '.join(p['name'] for p in PERSONAS)}")
        print(f"  Estimated cost: ~${total * 0.008:.2f}")
        return

    # Skip already-done pairs
    done_ids = set()
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            for line in f:
                rec = json.loads(line.strip())
                done_ids.add(rec["id"])
        print(f"  Resuming: {len(done_ids)} already done")

    # Build work queue
    rng = random.Random(2026)
    work = []
    for prompt in prompts:
        for v in range(args.variants):
            pair_id = f"golden-{prompt['id']}-v{v}"
            if pair_id not in done_ids:
                work.append((prompt, v, random.Random(rng.randint(0, 2**32))))

    print(f"  Work queue: {len(work)} pairs")
    if not work:
        print("  Nothing to do!")
        return

    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(cook_variant, p, v, r): (p, v) for p, v, r in work}
        for future in as_completed(futures):
            rec = future.result()
            if rec:
                with file_lock:
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            done = stats["written"] + stats["failed"]
            if done % 20 == 0 and done > 0:
                elapsed = time.time() - t0
                rate = done / max(elapsed / 60, 0.01)
                remaining = len(work) - done
                eta = remaining / max(rate, 1)
                print(f"  [{done}/{len(work)}] {stats['written']} ok, "
                      f"{stats['failed']} fail, {stats.get('schema_errors', 0)} schema_err | "
                      f"{rate:.0f}/min ETA={eta:.1f}min")

    elapsed = time.time() - t0
    print(f"\n{'='*70}")
    print(f"  GOLDEN PAIRS DONE")
    print(f"  Written: {stats['written']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Schema errors: {stats.get('schema_errors', 0)}")
    print(f"  API calls: {stats['api_calls']}")
    print(f"  Time: {elapsed/60:.1f} min")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
