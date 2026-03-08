#!/usr/bin/env python3
"""SwarmCapital Platinum Mutation Cook
=====================================

Takes hand-crafted eval prompts and generates 20 trajectory mutations each.
Each mutation varies: property type, market, deal size, severity, perspective.

11 base prompts × 20 mutations = 220 platinum pairs via Qwen3-235B.

Usage:
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_platinum_mutations
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_platinum_mutations --dry-run
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_platinum_mutations --workers 50
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
MUTATIONS_PER_PROMPT = 20
WORKERS = 50

EVAL_FILE = Path(__file__).parent / "eval_swarmcapital.jsonl"
OUTPUT_DIR = Path(os.environ.get("SWARM_COOK_DIR",
    Path.home() / "swarm_cooks")) / "cre_capital"
OUTPUT_FILE = OUTPUT_DIR / "platinum_mutations.jsonl"

# ═══════════════════════════════════════════════════════════════════════════════
# MUTATION AXES — 20 unique combinations per prompt
# ═══════════════════════════════════════════════════════════════════════════════

PROPERTY_TYPES = [
    ("Class A Office Tower", "450,000 SF, 32 floors, Manhattan"),
    ("Suburban Office Park", "280,000 SF, 4 buildings, Atlanta suburbs"),
    ("Class B Multifamily", "320 units, 1985 vintage, Phoenix"),
    ("Luxury Multifamily", "185 units, 2021 vintage, Miami Brickell"),
    ("Grocery-Anchored Retail", "125,000 SF, Kroger anchor, Nashville"),
    ("Regional Mall", "680,000 SF, 3 anchors, secondary market"),
    ("Full-Service Hotel", "285 rooms, convention district, Chicago"),
    ("Select-Service Hotel", "142 rooms, airport submarket, Dallas"),
    ("Industrial Distribution", "750,000 SF, 36' clear, Inland Empire"),
    ("Cold Storage Facility", "180,000 SF, -20F capable, Atlanta"),
    ("Data Center Shell", "120,000 SF, 40MW, Northern Virginia"),
    ("Medical Office Building", "95,000 SF, hospital-adjacent, Houston"),
    ("Self-Storage Portfolio", "12 facilities, 8,400 units, Sun Belt"),
    ("Mixed-Use Development", "350,000 SF office + 200 units + retail, Denver"),
    ("Life Science Campus", "220,000 SF, Class A lab, San Diego"),
]

MARKETS = [
    ("Manhattan", "NY"), ("Los Angeles", "CA"), ("Chicago", "IL"),
    ("Dallas-Fort Worth", "TX"), ("Miami", "FL"), ("San Francisco", "CA"),
    ("Atlanta", "GA"), ("Phoenix", "AZ"), ("Seattle", "WA"),
    ("Denver", "CO"), ("Boston", "MA"), ("Nashville", "TN"),
    ("Austin", "TX"), ("Charlotte", "NC"), ("Washington DC", "DC"),
    ("Tampa", "FL"), ("Houston", "TX"), ("Minneapolis", "MN"),
    ("San Diego", "CA"), ("Portland", "OR"),
]

DEAL_SIZES = [
    ("$18M", 18_000_000), ("$35M", 35_000_000), ("$52M", 52_000_000),
    ("$78M", 78_000_000), ("$125M", 125_000_000), ("$210M", 210_000_000),
    ("$340M", 340_000_000), ("$500M", 500_000_000),
]

SEVERITY_LEVELS = [
    ("mild", "modest stress — 50-100bp rate increase, 5-10% NOI decline"),
    ("moderate", "meaningful stress — 150-250bp rate increase, 15-20% NOI decline"),
    ("severe", "crisis-level stress — 300bp+ rate increase, 25-40% NOI decline"),
    ("extreme", "GFC-level stress — 400bp+ rate increase, 40%+ NOI decline, liquidity freeze"),
]

PERSPECTIVES = [
    "borrower (GP/developer seeking to protect equity and find solutions)",
    "senior lender (bank managing credit exposure and regulatory requirements)",
    "mezzanine lender (subordinate position, first loss after equity)",
    "CMBS special servicer (managing distressed pool, maximizing bondholder recovery)",
    "distressed debt fund (looking for mispriced credit opportunities)",
    "institutional LP (evaluating portfolio allocation and manager performance)",
    "REIT portfolio manager (managing public market exposure to CRE)",
    "insurance company (conservative lender, managing ALM and regulatory capital)",
    "family office (opportunistic buyer, long hold horizon, flexible capital)",
    "regulator (monitoring systemic CRE exposure across banking sector)",
]

# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are SwarmCapital — an elite capital markets intelligence engine built on 30 years of institutional CRE debt and equity experience. $8B+ in closed transactions across office, industrial, multifamily, retail, hospitality, and specialty asset classes.

You produce institutional-quality analysis using the 5-step trajectory:
1. **IDENTIFY** — the core question, property type, market, capital structure, and key variables
2. **CALCULATE** — step-by-step financial math with every formula, every input, every result. Use real numbers.
3. **ANALYZE** — market conditions, comparable transactions, lender appetite, rate environment
4. **EVALUATE** — capital structure options, workout feasibility, risk-reward, tokenization readiness
5. **RECOMMEND** — Proceed / Restructure / Sell / Kill — with confidence score (0.0-1.0) and prioritized action items

CAPITAL MARKETS ENVIRONMENT (2026):
- SOFR: 4.25-4.50% (Federal Reserve holding after 75bp cuts in late 2025)
- 10-Year Treasury: 4.10-4.35% range
- Fed Funds Rate: 4.25-4.50% target range
- CRE Debt Maturity Wall: $1.5T maturing 2025-2027, peak in 2026
- CMBS Delinquency: Office 8.5%, Retail 6.2%, Hotel 4.8%, Multifamily 2.1%, Industrial 0.8%
- Cap Rate Spreads (over 10yr): Office 200-350bp, Multifamily 100-175bp, Industrial 75-150bp
- Lending Sources: Banks (35%, tightening), CMBS (25%, selective), Life Cos (15%), Agency (10%, multifamily only), Debt Funds (15%, opportunistic)
- Key Metrics: LTV (55-70%), DSCR (>1.25x), Debt Yield (>8%), IO increasingly rare

BLOCKCHAIN / RWA TOKENIZATION:
- Real World Assets on-chain: $16B+, CRE fastest-growing segment
- Hedera HTS: native tokenization, $0.0001/tx, 10K TPS
- Token standards: ERC-1400 (security tokens), ERC-3643 (identity-compliant)
- Fractional ownership: Reg D 506(c) ($25K-100K min), Reg A+ ($500-10K min)

Always show the math. Always use real numbers. Never approximate without calculating first."""

# ═══════════════════════════════════════════════════════════════════════════════
# MUTATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def generate_mutations(prompt_rec: dict, seed: int = 2026) -> list[dict]:
    """Generate 20 mutations of a base prompt."""
    rng = random.Random(f"{prompt_rec['id']}-{seed}")
    mutations = []

    # Shuffle all axes
    props = list(PROPERTY_TYPES)
    mkts = list(MARKETS)
    sizes = list(DEAL_SIZES)
    sevs = list(SEVERITY_LEVELS)
    persp = list(PERSPECTIVES)
    rng.shuffle(props)
    rng.shuffle(mkts)
    rng.shuffle(sizes)
    rng.shuffle(sevs)
    rng.shuffle(persp)

    base_prompt = prompt_rec["prompt"]
    category = prompt_rec["category"]

    for i in range(MUTATIONS_PER_PROMPT):
        prop_type, prop_desc = props[i % len(props)]
        market, state = mkts[i % len(mkts)]
        size_label, size_val = sizes[i % len(sizes)]
        sev_name, sev_desc = sevs[i % len(sevs)]
        perspective = persp[i % len(persp)]

        # Build mutation context
        context_block = f"""DEAL CONTEXT:
- Property: {prop_type} — {prop_desc}
- Market: {market}, {state}
- Deal Size: {size_label}
- Stress Level: {sev_name} ({sev_desc})
- Perspective: Analyze from the viewpoint of a {perspective}"""

        mutated_prompt = f"""{base_prompt}

{context_block}

Apply the 5-step trajectory (IDENTIFY → CALCULATE → ANALYZE → EVALUATE → RECOMMEND).
Show all financial math. Use the deal context above to ground your analysis with specific numbers."""

        mutations.append({
            "mutation_idx": i,
            "base_id": prompt_rec["id"],
            "category": category,
            "property_type": prop_type,
            "market": f"{market}, {state}",
            "deal_size": size_label,
            "severity": sev_name,
            "perspective": perspective,
            "user_prompt": mutated_prompt,
        })

    return mutations


# ═══════════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════════

session = requests.Session()
_lock = threading.Lock()
_stats = Counter()


def init_session(api_key: str):
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })


def call_235b(user_msg: str, retries: int = 3) -> str | None:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 4096,
        "temperature": 0.7,
    }
    for attempt in range(retries):
        try:
            resp = session.post(TOGETHER_URL, json=payload, timeout=180)
            with _lock:
                _stats["calls"] += 1
            if resp.status_code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            if resp.status_code == 402:
                raise RuntimeError("402 — out of credits")
            if resp.status_code == 403:
                raise RuntimeError("403 — bad API key")
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
            if content and len(content) > 500:
                return content
        except RuntimeError:
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt + 1)
            else:
                with _lock:
                    _stats["errors"] += 1
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# QUALITY CHECK
# ═══════════════════════════════════════════════════════════════════════════════

_TRAJ = [
    re.compile(r"\*?\*?1\.?\s*IDENTIFY", re.IGNORECASE),
    re.compile(r"\*?\*?2\.?\s*CALCULATE", re.IGNORECASE),
    re.compile(r"\*?\*?3\.?\s*ANALYZE", re.IGNORECASE),
    re.compile(r"\*?\*?4\.?\s*EVALUATE", re.IGNORECASE),
    re.compile(r"\*?\*?5\.?\s*RECOMMEND", re.IGNORECASE),
]
_NUM = re.compile(r"\$[\d,.]+|\d+\.?\d*\s*%|\d{1,3}(,\d{3})+")


def quality_check(content: str) -> tuple[bool, str]:
    if len(content) < 800:
        return False, "too_short"
    steps = sum(1 for p in _TRAJ if p.search(content))
    if steps < 3:
        return False, f"trajectory_{steps}/5"
    nums = _NUM.findall(content)
    if len(nums) < 5:
        return False, "weak_financials"
    return True, "pass"


# ═══════════════════════════════════════════════════════════════════════════════
# GRINDER
# ═══════════════════════════════════════════════════════════════════════════════

def grind_mutation(mut: dict) -> dict | None:
    content = call_235b(mut["user_prompt"])
    if not content:
        return None

    passed, reason = quality_check(content)
    if not passed:
        # Retry once
        content = call_235b(mut["user_prompt"])
        if not content:
            return None
        passed, reason = quality_check(content)
        if not passed:
            with _lock:
                _stats["rejected"] += 1
            return None

    return {
        "id": f"platinum-{mut['base_id']}-m{mut['mutation_idx']:02d}",
        "task_type": mut["category"],
        "difficulty": "platinum",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": mut["user_prompt"]},
            {"role": "assistant", "content": content},
        ],
        "metadata": {
            "source": "platinum-mutation-v1",
            "base_prompt": mut["base_id"],
            "mutation_idx": mut["mutation_idx"],
            "category": mut["category"],
            "property_type": mut["property_type"],
            "market": mut["market"],
            "deal_size": mut["deal_size"],
            "severity": mut["severity"],
            "perspective": mut["perspective"],
            "model": MODEL,
            "tier": "platinum",
        },
    }


def main():
    parser = argparse.ArgumentParser(description="SwarmCapital Platinum Mutation Cook")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--prompt", type=str, help="Cook only this prompt ID")
    args = parser.parse_args()

    # Load eval prompts
    if not EVAL_FILE.exists():
        print(f"ERROR: {EVAL_FILE} not found")
        sys.exit(1)

    prompts = []
    with open(EVAL_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                prompts.append(json.loads(line))

    if args.prompt:
        prompts = [p for p in prompts if p["id"] == args.prompt]
        if not prompts:
            print(f"ERROR: Prompt {args.prompt} not found")
            sys.exit(1)

    # Generate all mutations
    all_mutations = []
    for p in prompts:
        muts = generate_mutations(p)
        all_mutations.extend(muts)

    total = len(all_mutations)
    print(f"{'='*70}")
    print(f"  SWARMCAPITAL PLATINUM MUTATION COOK")
    print(f"  {len(prompts)} base prompts × {MUTATIONS_PER_PROMPT} mutations = {total} pairs")
    print(f"  Model: {MODEL}")
    print(f"  Workers: {args.workers}")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"  Quality: min 800 chars, 3/5 trajectory, 5+ financial numbers")
    print(f"{'='*70}")

    if args.dry_run:
        for i, mut in enumerate(all_mutations[:6]):
            print(f"\n--- {mut['base_id']} mutation {mut['mutation_idx']} ---")
            print(f"  Property: {mut['property_type']}")
            print(f"  Market: {mut['market']}")
            print(f"  Size: {mut['deal_size']} | Severity: {mut['severity']}")
            print(f"  Perspective: {mut['perspective']}")
            print(f"  Prompt: {mut['user_prompt'][:200]}...")
        print(f"\n  ... and {total - 6} more mutations")
        return

    api_key = os.environ.get("TOGETHER_KEY")
    if not api_key:
        print("ERROR: Set TOGETHER_KEY")
        sys.exit(1)
    init_session(api_key)

    # Test model
    print(f"  Testing 235B...")
    test = call_235b("Say 'SwarmCapital ready'.")
    if not test:
        print("  FAIL — model not responding")
        sys.exit(1)
    print(f"  235B: OK")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    t0 = time.time()
    file_lock = threading.Lock()

    def process(mut):
        nonlocal written
        rec = grind_mutation(mut)
        if rec:
            with file_lock:
                with open(OUTPUT_FILE, "a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
                if written % 10 == 0:
                    elapsed = time.time() - t0
                    rate = written / max(elapsed / 60, 0.1)
                    print(f"  [{written}/{total}] rate={rate:.1f}/min "
                          f"calls={_stats['calls']} err={_stats.get('errors',0)} "
                          f"rej={_stats.get('rejected',0)}")
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, m): m for m in all_mutations}
        for future in as_completed(futures):
            try:
                future.result()
            except RuntimeError as e:
                print(f"\n  FATAL: {e}")
                break
            except Exception:
                pass

    elapsed = time.time() - t0
    rate = written / max(elapsed / 60, 0.1)
    print(f"\n{'='*70}")
    print(f"  PLATINUM COOK COMPLETE")
    print(f"  Written: {written}/{total} pairs")
    print(f"  Rate: {rate:.1f}/min | Time: {elapsed/60:.1f} min")
    print(f"  API calls: {_stats['calls']} | Errors: {_stats.get('errors',0)} "
          f"| Rejected: {_stats.get('rejected',0)}")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
