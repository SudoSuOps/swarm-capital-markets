#!/usr/bin/env python3
"""Reasoning Path Augmentation (RPA) — Multi-Trajectory Supervision
====================================================================

Takes existing high-value prompts and generates 2 additional reasoning paths
from different institutional perspectives. Same deal, different strategies.

Personas (reasoning trajectories):
  1. credit_committee    — lender perspective, risk-first, DSCR/LTV gates
  2. distressed_investor — opportunity-first, loan-to-own, basis discount
  3. equity_sponsor      — sponsor perspective, recapitalization, hold vs sell
  4. sell_side_broker     — market-driven, comparable transactions, pricing
  5. risk_officer         — downside scenarios, stress testing, tail risks

Source streams (best candidates for multi-trajectory):
  - cmbs_distress        (naturally supports all 5 perspectives)
  - debt_maturity        (refi vs restructure vs sell — classic multi-path)
  - macro_causality      (temporal reasoning across eras)
  - valuation_advisory   (portfolio + single-asset dual view)

Target: ~6,000 source prompts → 2 additional paths each → 12,000 RPA pairs
Total augmented dataset contribution: ~12,000 pairs

Usage:
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_rpa
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_rpa --dry-run
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_rpa --workers 40
    TOGETHER_KEY=... python3 -m data.swarmcre_dataset.cook_rpa --sample 2000
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
MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"  # Quality model for reasoning paths

COOK_DIR = Path("/home/swarm/swarm_cooks/cre_capital")
SOURCE_STREAMS = [
    "stream_cmbs_distress.jsonl",
    "stream_debt_maturity.jsonl",
    "stream_equity_advisory.jsonl",
    "stream_valuation_advisory.jsonl",
    "stream_macro_causality.jsonl",
]
OUTPUT_FILE = COOK_DIR / "rpa_pairs.jsonl"
DEFAULT_SAMPLE = 6_000
PATHS_PER_PROMPT = 2

session = requests.Session()
api_lock = threading.Lock()
file_lock = threading.Lock()
stats = Counter()

# ═══════════════════════════════════════════════════════════════════════════════
# REASONING PERSONAS — each produces a distinct analytical trajectory
# ═══════════════════════════════════════════════════════════════════════════════

REASONING_PERSONAS = [
    {
        "strategy": "credit_committee",
        "system": """You are a credit committee officer at a major CRE lender evaluating this deal for loan approval.

Your reasoning MUST follow the lender's analytical framework:
1. Credit risk assessment — borrower strength, sponsor track record, guarantor net worth
2. Collateral analysis — property quality, market position, replacement cost basis
3. Cash flow underwriting — DSCR stress testing at multiple rate scenarios, debt yield floor
4. Structure assessment — LTV compliance, covenant package, reserve requirements
5. Decision — approve / approve with conditions / decline, with specific conditions and confidence (0-1)

You think in terms of: downside protection, loss severity, recovery rate, regulatory capital.
Show every calculation. Be conservative. Flag what kills the deal.

IMPORTANT: Label your analysis with strategy: "credit_committee" """,
    },
    {
        "strategy": "distressed_investor",
        "system": """You are a distressed debt investor at a CRE hedge fund evaluating this as an acquisition opportunity.

Your reasoning MUST follow the opportunistic framework:
1. Distress diagnosis — why is this asset/loan in trouble? Is the distress temporary or structural?
2. Basis analysis — what can you acquire the note or asset for vs stabilized value? What's the discount?
3. Loan-to-own math — if you buy the note at par/discount, what's your effective basis? Can you foreclose and reposition?
4. Recovery modeling — stabilization cost, timeline, exit cap rate, total return potential
5. Decision — acquire / pass / watch, with expected IRR, multiple, and confidence (0-1)

You think in terms of: basis discount, recovery upside, execution risk, capital deployment.
Show the full investment thesis with numbers. Be aggressive but disciplined.

IMPORTANT: Label your analysis with strategy: "distressed_investor" """,
    },
    {
        "strategy": "equity_sponsor",
        "system": """You are the equity sponsor/borrower evaluating your options for this property.

Your reasoning MUST follow the sponsor's decision framework:
1. Current position assessment — what's your equity at risk? Book value vs market value? Accrued returns?
2. Capital injection analysis — how much additional equity would stabilize? What does that do to your blended basis and return?
3. Negotiation leverage — what cards do you hold with the lender? Extension, modification, principal paydown?
4. Hold vs sell vs hand back — at what point is it rational to give up the asset? What's your BATNA?
5. Decision — inject equity / negotiate extension / sell / deed-in-lieu, with timeline and confidence (0-1)

You think in terms of: sunk cost vs incremental return, LP communication, fund-level impact, reputation.
Show the math on whether new money chasing old money actually works.

IMPORTANT: Label your analysis with strategy: "equity_sponsor" """,
    },
    {
        "strategy": "sell_side_broker",
        "system": """You are a senior capital markets broker advising on this transaction.

Your reasoning MUST follow the brokerage analytical framework:
1. Market positioning — where does this asset sit vs current buyer demand? Who's buying this asset type?
2. Pricing analysis — comparable transactions, implied cap rate, price per unit/SF, list-to-sale ratio
3. Buyer pool identification — 1031 exchange buyers, institutional, value-add operators, distressed funds
4. Execution strategy — marketed sale vs off-market, timeline, bid process structure
5. Recommendation — list price, expected close price, days on market, fee structure, confidence (0-1)

You think in terms of: market velocity, buyer depth, pricing psychology, deal certainty.
Reference specific comparable transactions and current market data.

IMPORTANT: Label your analysis with strategy: "sell_side_broker" """,
    },
    {
        "strategy": "risk_officer",
        "system": """You are the chief risk officer evaluating this deal's risk profile for the institution.

Your reasoning MUST follow the risk management framework:
1. Risk identification — enumerate every material risk (market, credit, concentration, liquidity, operational)
2. Stress testing — model 3 downside scenarios with specific assumptions. What breaks first?
3. Tail risk analysis — what's the worst-case loss? What's the probability? VaR/CVaR equivalent
4. Mitigation assessment — for each risk, what structural protections exist? What's missing?
5. Risk-adjusted decision — risk score (1-10), go/no-go, required mitigants, confidence (0-1)

You think in terms of: correlation risk, portfolio concentration, loss-given-default, recovery lag.
Be the skeptic. Find the risks others miss. Quantify everything.

IMPORTANT: Label your analysis with strategy: "risk_officer" """,
    },
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
            if content and len(content) > 300:
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
# SOURCE SELECTION — pick best candidates for multi-trajectory
# ═══════════════════════════════════════════════════════════════════════════════

def load_source_prompts(sample_size: int, rng: random.Random) -> list[dict]:
    """Load and sample source prompts from completed streams."""
    all_prompts = []

    for stream_file in SOURCE_STREAMS:
        path = COOK_DIR / stream_file
        if not path.exists():
            print(f"  SKIP {stream_file} — not found")
            continue
        count = 0
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    # Extract the user prompt (the deal + question)
                    user_msg = None
                    for m in rec.get("messages", []):
                        if m.get("role") == "user":
                            user_msg = m["content"]
                            break
                    if user_msg and len(user_msg) > 100:
                        all_prompts.append({
                            "source_id": rec.get("id", f"unknown-{count}"),
                            "user_prompt": user_msg,
                            "stream": rec.get("metadata", {}).get("stream", stream_file),
                            "task_type": rec.get("metadata", {}).get("task_type", "unknown"),
                            "difficulty": rec.get("difficulty", "high"),
                        })
                        count += 1
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"  {stream_file}: {count:,} candidates")

    # Prefer high-difficulty and diverse task types
    rng.shuffle(all_prompts)

    # Stratified sample: oversample platinum/high, undersample medium
    platinum = [p for p in all_prompts if p["difficulty"] == "platinum"]
    high = [p for p in all_prompts if p["difficulty"] == "high"]
    medium = [p for p in all_prompts if p["difficulty"] == "medium"]

    selected = []
    # 40% platinum (all of them if not enough), 40% high, 20% medium
    plat_n = min(len(platinum), int(sample_size * 0.40))
    high_n = min(len(high), int(sample_size * 0.40))
    med_n = sample_size - plat_n - high_n

    selected.extend(platinum[:plat_n])
    selected.extend(high[:high_n])
    selected.extend(medium[:med_n])

    # If still short, fill from remaining
    if len(selected) < sample_size:
        remaining = [p for p in all_prompts if p not in selected]
        selected.extend(remaining[:sample_size - len(selected)])

    return selected[:sample_size]


# ═══════════════════════════════════════════════════════════════════════════════
# RPA GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_rpa_pair(source: dict, persona: dict, rng: random.Random) -> dict | None:
    """Generate one reasoning path for a source prompt using a given persona."""

    content = together_call(persona["system"], source["user_prompt"])
    if not content:
        with api_lock:
            stats["failed"] += 1
        return None

    with api_lock:
        stats["written"] += 1

    return {
        "id": f"rpa-{source['source_id']}-{persona['strategy']}",
        "task_type": source["task_type"],
        "difficulty": source["difficulty"],
        "messages": [
            {"role": "system", "content": persona["system"]},
            {"role": "user", "content": source["user_prompt"]},
            {"role": "assistant", "content": content},
        ],
        "metadata": {
            "source": "rpa-v1",
            "strategy": persona["strategy"],
            "reasoning_trajectory": True,
            "base_prompt_id": source["source_id"],
            "base_stream": source["stream"],
            "base_task_type": source["task_type"],
            "model": MODEL,
            "blend_source": "rpa",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Cook RPA multi-trajectory reasoning pairs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=40)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                        help="Number of source prompts to augment")
    parser.add_argument("--paths", type=int, default=PATHS_PER_PROMPT,
                        help="Additional reasoning paths per prompt")
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

    rng = random.Random(2026)

    print("=" * 70)
    print("  REASONING PATH AUGMENTATION (RPA)")
    print("  Multi-Trajectory Supervision for SwarmCapitalMarkets-27B")
    print("=" * 70)

    # Load source prompts
    print(f"\n[1/3] LOADING SOURCE PROMPTS (target: {args.sample:,})")
    sources = load_source_prompts(args.sample, rng)
    print(f"  Selected: {len(sources):,} prompts")

    # Difficulty distribution
    diffs = Counter(s["difficulty"] for s in sources)
    for d, c in diffs.most_common():
        print(f"    {d:12s} {c:>5,} ({c/len(sources)*100:.1f}%)")

    # Stream distribution
    streams = Counter(s["stream"] for s in sources)
    for s, c in streams.most_common():
        print(f"    {s:35s} {c:>5,}")

    total_pairs = len(sources) * args.paths
    print(f"\n  Plan: {len(sources):,} prompts × {args.paths} paths = {total_pairs:,} RPA pairs")
    print(f"  Personas: {', '.join(p['strategy'] for p in REASONING_PERSONAS)}")
    print(f"  Model: {MODEL}")
    print(f"  Workers: {args.workers}")
    est_cost = total_pairs * 0.012  # ~$0.012 per 235B call
    print(f"  Estimated cost: ~${est_cost:.2f}")

    if args.dry_run:
        print(f"\n  DRY RUN — would generate {total_pairs:,} pairs")
        return

    # Skip already-done pairs
    done_ids = set()
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    done_ids.add(rec["id"])
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"  Resuming: {len(done_ids)} already done")

    # Build work queue — assign personas to each source
    print(f"\n[2/3] BUILDING WORK QUEUE")
    work = []
    for source in sources:
        # Pick N distinct personas for this prompt (different from any existing)
        available = list(REASONING_PERSONAS)
        rng.shuffle(available)
        for persona in available[:args.paths]:
            pair_id = f"rpa-{source['source_id']}-{persona['strategy']}"
            if pair_id not in done_ids:
                work.append((source, persona))

    print(f"  Work queue: {len(work):,} pairs")
    if not work:
        print("  Nothing to do!")
        return

    # Cook
    print(f"\n[3/3] COOKING RPA PAIRS")
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(generate_rpa_pair, src, persona, random.Random(rng.randint(0, 2**32))):
            (src, persona) for src, persona in work
        }
        for future in as_completed(futures):
            rec = future.result()
            if rec:
                with file_lock:
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            done = stats["written"] + stats["failed"]
            if done % 50 == 0 and done > 0:
                elapsed = time.time() - t0
                rate = done / max(elapsed / 60, 0.01)
                remaining = len(work) - done
                eta = remaining / max(rate, 1)
                print(f"  [{done}/{len(work)}] {stats['written']} ok, "
                      f"{stats['failed']} fail, {stats['errors']} err | "
                      f"{rate:.0f}/min ETA={eta:.1f}min")

    elapsed = time.time() - t0
    strategies = Counter()
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    strategies[rec.get("metadata", {}).get("strategy", "?")] += 1
                except (json.JSONDecodeError, KeyError):
                    continue

    print(f"\n{'=' * 70}")
    print(f"  RPA COOK COMPLETE")
    print(f"  Written:    {stats['written']:,}")
    print(f"  Failed:     {stats['failed']:,}")
    print(f"  API calls:  {stats['api_calls']:,}")
    print(f"  Errors:     {stats['errors']:,}")
    print(f"  Time:       {elapsed/60:.1f} min")
    print(f"  Strategy distribution:")
    for s, c in strategies.most_common():
        print(f"    {s:25s} {c:>5,}")
    print(f"  Output:     {OUTPUT_FILE}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
