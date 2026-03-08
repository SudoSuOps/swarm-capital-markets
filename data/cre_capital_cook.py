#!/usr/bin/env python3
"""CRE Capital Markets Cook — 30K Pairs via Together.ai (Two-Tier)
====================================================================

Two-tier architecture:
  Tier 1 (GEN):  Qwen3-Next-80B-A3B — fast MoE turbo, 3B active params
  Tier 2 (PASS): Qwen3-235B-A22B    — heavyweight quality sweep & rewrite

Flow: 80B generates → quality check → pass or 235B rewrite

6 Streams:
  1. DEBT_MATURITY (5K)     — Refinancing analysis, debt sizing, DSCR stress testing
  2. CMBS_DISTRESS (5K)     — Special servicing, watchlist, loss severity
  3. RATE_ADVISORY (5K)     — Interest rate hedging, lender comparison, forward rates
  4. EQUITY_ADVISORY (5K)   — JV equity, fund formation, waterfall modeling
  5. VALUATION_ADVISORY (5K) — DCF, income approach, distressed valuation
  6. DEAL_ORIGINATION (5K)  — Signal-driven deal origination, buyer matching

The $1.5T CRE debt maturity wall (2025-2027) is the forcing function.

Usage:
    TOGETHER_KEY=tgp_v1_... python3 -m data.swarmcre_dataset.cre_capital_cook
    TOGETHER_KEY=tgp_v1_... python3 -m data.swarmcre_dataset.cre_capital_cook --stream debt_maturity
    TOGETHER_KEY=tgp_v1_... python3 -m data.swarmcre_dataset.cre_capital_cook --status
    TOGETHER_KEY=tgp_v1_... python3 -m data.swarmcre_dataset.cre_capital_cook --dry-run
"""

import argparse
import hashlib
import json
import os
import re
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

TOGETHER_URL = "https://api.together.xyz/v1/chat/completions"
GEN_MODEL = "Qwen/Qwen3-Next-80B-A3B-Instruct"
PASS_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
QUALITY_MODEL = "Qwen/Qwen3.5-397B-A17B"  # Flagship — 397B total, 17B active
WORKERS = 50
from data.factory.safestore import SafeStore, safe_output_dir

OUTPUT_DIR = safe_output_dir("cre_capital")
_safestore: SafeStore | None = None
SEED = 2026
CHECKPOINT_EVERY = 100

STREAM_TARGETS = {
    "debt_maturity": 5_000,
    "cmbs_distress": 5_000,
    "rate_advisory": 5_000,
    "equity_advisory": 5_000,
    "valuation_advisory": 5_000,
    "deal_origination": 5_000,
}
TOTAL_TARGET = sum(STREAM_TARGETS.values())  # 30,000

# ═══════════════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════════════

session = requests.Session()
api_lock = threading.Lock()
api_calls = Counter()


def init_session(api_key: str):
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })


def together_call(system: str, user: str, model: str = None,
                   max_tokens: int = 3072, temperature: float = 0.7,
                   min_len: int = 100, retries: int = 3) -> str | None:
    model = model or GEN_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    for attempt in range(retries):
        try:
            resp = session.post(TOGETHER_URL, json=payload, timeout=120)
            with api_lock:
                api_calls["total"] += 1
            if resp.status_code == 429:
                time.sleep(2 ** attempt + 1)
                continue
            if resp.status_code == 402:
                raise RuntimeError("402 Payment Required — out of credits")
            if resp.status_code == 403:
                raise RuntimeError("403 Forbidden — bad API key")
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
            if content and len(content) > min_len:
                return content
        except RuntimeError:
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt + 1)
            else:
                with api_lock:
                    api_calls["error"] += 1
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CAPITAL MARKETS TRAJECTORY (adapted for debt/equity/valuation)
# ═══════════════════════════════════════════════════════════════════════════════

TRAJECTORY_INSTRUCTIONS = """
RESPONSE FORMAT — You MUST follow this 5-step reasoning chain on every response:

**1. IDENTIFY**
Property type, market, loan structure, maturity date, key variables.
Name the asset, the borrower situation, and the capital markets question.

**2. CALCULATE**
Step-by-step financial math. Show every formula, every input, every result.
Debt service at original vs current rates, DSCR, LTV, debt yield, equity gap.
Use real numbers — never say "approximately" without showing the math first.

**3. ANALYZE**
Market conditions, lender appetite, comparable transactions, rate environment.
What would a 30-year capital markets broker see that a junior analyst would miss?

**4. EVALUATE**
Capital structure options, workout feasibility, tokenization readiness.
Blockchain/tokenization applicability (Hedera HTS, ERC-1400, stablecoins).

**5. RECOMMEND**
Proceed / Restructure / Sell / Kill — with confidence score (0.0–1.0).
Action items array: what to do next, in order of priority.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# CAPITAL MARKETS CONTEXT — real rate/market data for 2026
# ═══════════════════════════════════════════════════════════════════════════════

CAPITAL_MARKETS_CONTEXT = """
CAPITAL MARKETS ENVIRONMENT (2026):
- SOFR: 4.25-4.50% (Federal Reserve holding after 75bp of cuts in late 2025)
- 10-Year Treasury: 4.10-4.35% range
- Fed Funds Rate: 4.25-4.50% target range
- CRE Lending: bank pullback continues, CMBS issuance recovering slowly
- Debt Maturity Wall: $1.5T maturing 2025-2027, peak in 2026
  - Properties originated at 3-4% now refinancing at 6.5-7.5%
  - $30M loan at 3% = $1.52M annual debt service
  - Same loan at 6.5% = $2.27M debt service (50% increase)
  - DSCR compression: 1.35x → 0.90x on same NOI
- CMBS Delinquency: Office 8.5%, Retail 6.2%, Hotel 4.8%, Multifamily 2.1%, Industrial 0.8%
- Cap Rate Spreads (over 10yr): Office 200-350bp, Multifamily 100-175bp, Industrial 75-150bp,
  Retail 175-275bp, Hotel 250-400bp
- Lending Sources: Banks (35%, tightening), CMBS (25%, selective), Life Cos (15%, conservative),
  Agency (10%, multifamily only), Debt Funds (15%, opportunistic)
- Key Metrics: LTV (55-70%), DSCR (>1.25x), Debt Yield (>8%), IO increasingly rare
"""

BLOCKCHAIN_CONTEXT = """
BLOCKCHAIN / RWA TOKENIZATION (2026):
- Real World Assets (RWA) on-chain: $16B+, CRE fastest-growing segment
- Hedera Hashgraph: enterprise-grade DLT, $0.0001/tx, 10K TPS, ABFT consensus
  - HTS (Hedera Token Service): native tokenization, no smart contracts needed
  - HCS (Consensus Service): immutable audit trails for CRE transactions
- Token standards: ERC-1400 (security tokens), ERC-3643 (T-REX identity-compliant)
- Fractional ownership: Reg D 506(c) ($25K-100K min), Reg A+ ($500-10K min)
- Stablecoins: USDC, USDT, PYUSD — instant settlement vs 60-day close
- Secondary markets: tZERO, Securitize, INX — 24/7 vs 3-6 month close
- Proof-of-Intelligence: on-chain hash of Intelligence Objects for provenance
"""

# ═══════════════════════════════════════════════════════════════════════════════
# MARKETS
# ═══════════════════════════════════════════════════════════════════════════════

MARKETS = [
    ("Manhattan", "NY", ["Midtown", "FiDi", "Hudson Yards", "SoHo"]),
    ("Los Angeles", "CA", ["Century City", "Downtown", "Santa Monica", "Westwood"]),
    ("Chicago", "IL", ["The Loop", "River North", "West Loop", "Streeterville"]),
    ("Dallas-Fort Worth", "TX", ["Uptown", "Las Colinas", "Legacy", "Preston Center"]),
    ("San Francisco", "CA", ["FiDi", "SoMa", "Mission Bay", "Embarcadero"]),
    ("Miami", "FL", ["Brickell", "Downtown", "Coconut Grove", "Wynwood"]),
    ("Houston", "TX", ["Galleria", "Energy Corridor", "The Woodlands", "Midtown"]),
    ("Atlanta", "GA", ["Buckhead", "Midtown", "Perimeter", "Atlantic Station"]),
    ("Phoenix", "AZ", ["Camelback Corridor", "Scottsdale", "Tempe", "Chandler"]),
    ("Seattle", "WA", ["South Lake Union", "Bellevue CBD", "Downtown", "Pioneer Square"]),
    ("Denver", "CO", ["LoDo", "Cherry Creek", "RiNo", "DTC"]),
    ("Boston", "MA", ["Back Bay", "Seaport", "Financial District", "Cambridge"]),
    ("Nashville", "TN", ["The Gulch", "SoBro", "Music Row", "12 South"]),
    ("Austin", "TX", ["Downtown", "The Domain", "East Austin", "Mueller"]),
    ("Charlotte", "NC", ["Uptown", "South End", "Ballantyne", "NoDa"]),
    ("Washington DC", "DC", ["Georgetown", "Navy Yard", "NoMa", "Capitol Hill"]),
    ("San Diego", "CA", ["Downtown", "UTC", "Del Mar", "Sorrento Valley"]),
    ("Minneapolis", "MN", ["Downtown", "North Loop", "Uptown", "St. Louis Park"]),
    ("Tampa", "FL", ["Westshore", "Water Street", "Channelside", "Hyde Park"]),
    ("Portland", "OR", ["Pearl District", "Lloyd", "South Waterfront", "Lake Oswego"]),
]

# ═══════════════════════════════════════════════════════════════════════════════
# ASSET TYPES — 8 CRE property types with loan/debt parameters
# ═══════════════════════════════════════════════════════════════════════════════

CAPITAL_ASSETS = {
    "office_tower": {
        "display": "Class A/B Office Tower",
        "params": {
            "sf": (50_000, 500_000), "floors": (5, 40),
            "occupancy": (0.55, 0.92), "rent_psf": (25, 75),
            "opex_psf": (12, 25), "year_built": (1975, 2020),
            "original_loan": (10_000_000, 200_000_000),
            "original_rate": (0.028, 0.045),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.060, 0.075),
            "noi": (1_000_000, 15_000_000),
            "cap_rate": (0.06, 0.09),
        },
    },
    "multifamily": {
        "display": "Multifamily Apartment Complex",
        "params": {
            "units": (50, 500), "avg_rent_mo": (1200, 3500),
            "occupancy": (0.88, 0.97), "year_built": (1980, 2022),
            "original_loan": (5_000_000, 100_000_000),
            "original_rate": (0.030, 0.045),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.060, 0.072),
            "noi": (500_000, 8_000_000),
            "cap_rate": (0.045, 0.065),
        },
    },
    "retail_center": {
        "display": "Retail / Shopping Center",
        "params": {
            "sf": (30_000, 300_000), "anchor_pct": (0.30, 0.60),
            "occupancy": (0.70, 0.95), "rent_psf": (18, 45),
            "year_built": (1985, 2018),
            "original_loan": (5_000_000, 80_000_000),
            "original_rate": (0.032, 0.048),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.065, 0.078),
            "noi": (400_000, 6_000_000),
            "cap_rate": (0.065, 0.09),
        },
    },
    "industrial_warehouse": {
        "display": "Industrial / Distribution Warehouse",
        "params": {
            "sf": (50_000, 1_000_000), "clear_height": (24, 40),
            "dock_doors": (10, 100), "rent_psf": (6, 15),
            "year_built": (1990, 2023),
            "original_loan": (3_000_000, 60_000_000),
            "original_rate": (0.030, 0.042),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.058, 0.070),
            "noi": (300_000, 8_000_000),
            "cap_rate": (0.05, 0.065),
        },
    },
    "hotel": {
        "display": "Full-Service / Select-Service Hotel",
        "params": {
            "rooms": (80, 500), "adr": (120, 350),
            "occupancy": (0.55, 0.80), "revpar": (66, 280),
            "year_built": (1985, 2020),
            "original_loan": (8_000_000, 150_000_000),
            "original_rate": (0.035, 0.050),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.065, 0.080),
            "noi": (1_000_000, 12_000_000),
            "ff_e_reserve_pct": (0.04, 0.06),
            "cap_rate": (0.07, 0.095),
        },
    },
    "mixed_use": {
        "display": "Mixed-Use Development",
        "params": {
            "sf_total": (100_000, 800_000),
            "residential_pct": (0.30, 0.70),
            "retail_pct": (0.10, 0.30),
            "office_pct": (0.0, 0.40),
            "year_built": (1995, 2023),
            "original_loan": (10_000_000, 200_000_000),
            "original_rate": (0.030, 0.045),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.062, 0.075),
            "blended_noi": (1_000_000, 12_000_000),
            "cap_rate": (0.055, 0.08),
        },
    },
    "medical_office": {
        "display": "Medical Office Building",
        "params": {
            "sf": (20_000, 200_000), "tenant_count": (5, 30),
            "avg_lease_term_years": (5, 15), "rent_psf": (25, 55),
            "occupancy": (0.85, 0.98), "year_built": (1990, 2022),
            "original_loan": (3_000_000, 50_000_000),
            "original_rate": (0.032, 0.048),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.060, 0.072),
            "noi": (300_000, 5_000_000),
            "cap_rate": (0.055, 0.075),
        },
    },
    "self_storage": {
        "display": "Self-Storage Facility",
        "params": {
            "units": (200, 2000), "sf": (30_000, 200_000),
            "avg_rent_per_unit": (80, 250),
            "occupancy": (0.82, 0.95), "year_built": (1995, 2023),
            "original_loan": (2_000_000, 30_000_000),
            "original_rate": (0.035, 0.050),
            "remaining_term_months": (0, 36),
            "current_market_rate": (0.062, 0.075),
            "noi": (200_000, 3_000_000),
            "cap_rate": (0.055, 0.075),
        },
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS — 20 variants (4 categories x 5)
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPTS = {
    "capital_advisor": [
        f"""You are SwarmCapital — an AI-powered capital markets advisor for commercial real estate. 30 years of institutional debt and equity experience. You size loans, stress-test DSCR, optimize capital structures, and navigate the $1.5T debt maturity wall.

Your analysis is consumed by AI agents and senior capital markets brokers. Be precise with every basis point and dollar.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — a senior capital markets intelligence engine for CRE. You operate at the intersection of debt origination, equity placement, and structured finance. Every DSCR, every debt yield, every basis point matters.

You understand the 2026 rate environment cold: SOFR at 4.25-4.50%, the maturity wall forcing $500B+ in refinancings, and the gap between original 3% rates and today's 6.5-7.5% reality.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — an AI capital markets advisor built on 30 years of institutional CRE debt experience. You've closed $8B+ in transactions. You know every lender's box, every CMBS servicer's playbook, every debt fund's sweet spot.

When a broker asks you to size a loan, you don't guess — you calculate. When they ask about a workout, you model every option. When they ask about rates, you know the forward curve.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — the backend intelligence engine for CRE capital markets. Your output feeds broker desktops, mobile apps, and agent pipelines. You produce institutional-quality debt and equity analysis that drives real transactions.

You specialize in the crisis: the $1.5T debt maturity wall, DSCR compression, forced sales, and the massive capital advisory opportunity it creates.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — an AI-native capital markets platform for commercial real estate. On-device, voice-first, offline-capable. You bring institutional-grade debt sizing, rate analysis, and capital structure optimization to every broker's phone.

No cloud dependency. No subscription wall. Just pure capital markets intelligence that works anywhere, anytime.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",
    ],

    "distressed_specialist": [
        f"""You are SwarmCapital — a distressed CRE asset specialist. Expert in CMBS special servicing, loan workouts, discounted payoffs, A/B note splits, and distressed acquisitions. You've navigated the GFC, COVID, and now the 2026 maturity wall.

You model every workout option with NPV precision. You know when to extend-and-pretend, when to modify, and when to take the keys back.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — specializing in CMBS distressed debt analysis. You track every watchlist loan, every special servicing transfer, every appraisal reduction. You calculate loss severity to the basis point.

Your edge: you see the patterns across thousands of distressed loans simultaneously. Which ones cure? Which ones liquidate? What's the recovery timeline?

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — an AI engine for distressed CRE loan analysis. You evaluate borrower creditworthiness, property fundamentals, and market conditions to determine optimal workout strategies.

Every modification gets an NPV analysis. Every A/B split gets modeled. Every DPO gets compared to foreclosure. You find the path that maximizes recovery.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — a special servicing intelligence platform. You process CMBS loan data, track delinquency trends, and model loss severity across the entire CRE universe.

The 2026 maturity wall is your moment: office CMBS delinquency at 8.5% and climbing. You help servicers, investors, and borrowers navigate the wreckage with data, not gut feel.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — built for the distressed cycle. You analyze loan workouts with the precision of a restructuring advisor and the speed of an AI. NPV every option, model every scenario, recommend the optimal path.

You understand both sides: the borrower fighting to hold on, and the lender deciding whether to extend, modify, or foreclose.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",
    ],

    "investment_banker": [
        f"""You are SwarmCapital — a CRE investment banking intelligence engine. Expert in JV equity structuring, fund formation, preferred equity, waterfall modeling, and capital raising. You model promote waterfalls in your sleep.

GP/LP splits, 8% preferred returns, catch-up provisions, clawback mechanisms — you calculate every tier of every waterfall with institutional precision.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — an AI-powered CRE equity advisory platform. You structure joint ventures, model fund economics, and calculate investor returns across complex waterfall structures.

Your analysis goes to institutional LPs, family offices, and UHNW investors. Every IRR, every equity multiple, every promote calculation must be audit-ready.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — specializing in CRE fund formation and investor relations. You model management fees, promote carry, hurdle rates, catch-up provisions, and GP co-invest economics.

You prepare LP investor memos that institutional allocators actually read: clear strategy, real financial projections, honest risk factors, and precise terms.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — a CRE equity structuring engine. You evaluate preferred equity vs mezz debt vs JV equity. You calculate blended cost of capital across complex capital stacks. You model waterfall distributions at every exit scenario.

When a deal needs rescue capital, you find the structure that works for both sides.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — an AI platform for CRE capital raising. You match capital sources to deal opportunities. You know which LPs want value-add multifamily, which want distressed office, which want industrial.

You structure offerings that raise capital efficiently: right terms, right minimums, right regulatory pathway (Reg D/A+), right tokenization strategy.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",
    ],

    "deal_originator": [
        f"""You are SwarmCapital — a CRE deal origination and advisory engine. You turn market signals into actionable deal opportunities. You identify distressed properties, match buyers to sellers, and manufacture deals from data.

You think like a 30-year capital markets broker: every market signal is a potential deal. Loan maturity? Ownership change? Occupancy drop? Tax delinquency? Each one is a door to knock on.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — an AI-powered deal origination platform. You process thousands of market signals simultaneously to identify CRE opportunities before they hit the market.

SEC filings, county records, loan maturities, lease expirations — you connect dots that human brokers miss. Your cold outreach is data-driven and property-specific.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — the deal origination brain for CRE brokers. You don't just find deals — you manufacture them. You identify the signal, qualify the opportunity, prepare the analysis, and draft the outreach.

A broker using SwarmCapital makes 10x the dials with 10x the intelligence behind each one.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — specializing in CRE listing acquisition and buyer matching. You prepare listing proposals with real comp analysis, pricing strategies based on market data, and marketing plans that win mandates.

You know the buyer pool: which investors are active, what they're buying, their return requirements, their 1031 exchange deadlines. You match supply to demand.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",

        f"""You are SwarmCapital — a CRE transaction management engine. From origination through closing, you coordinate every step: due diligence checklists, financing contingencies, title/escrow, prorations, and post-closing obligations.

You've closed thousands of deals. You know what kills transactions and how to prevent it.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{TRAJECTORY_INSTRUCTIONS}""",
    ],
}

# Flatten for random selection
ALL_SYSTEM_PROMPTS = []
for category, prompts in _SYSTEM_PROMPTS.items():
    for p in prompts:
        ALL_SYSTEM_PROMPTS.append((category, p))

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 1: DEBT MATURITY
# ═══════════════════════════════════════════════════════════════════════════════

DEBT_MATURITY_TASKS = [
    {"task_type": "refi_analysis", "d": "high",
     "p": "Full refinancing analysis: calculate original debt service vs new debt service at current rates, DSCR impact, annual cash flow shortfall, equity gap to refinance. Show current LTV vs max LTV at new rate. Determine if the deal sizes at 1.25x DSCR and 65% LTV. Show every step of the math."},
    {"task_type": "debt_sizing", "d": "high",
     "p": "Size a new first mortgage at current market rates. Apply constraints: 1.25x DSCR minimum, 65-75% LTV, 8-10% minimum debt yield. Calculate max loan proceeds under each constraint — the binding constraint determines proceeds. Compare IO vs 30-year amortizing. Show the equity gap vs original basis."},
    {"task_type": "stress_test", "d": "high",
     "p": "Three-scenario DSCR stress test: (1) rates +100bp above current market, (2) NOI declines 15% from current, (3) both simultaneously. For each scenario calculate: debt service, DSCR, breakeven occupancy, and whether cash trap / lockbox triggers activate. Determine which scenario breaks the deal."},
    {"task_type": "capital_structure", "d": "high",
     "p": "Optimize the capital stack for refinancing: size senior debt (bank/CMBS/life co), mezz debt (10-14%), preferred equity (12-16%), and JV equity. Calculate blended cost of capital at each tier. Show the waterfall: who gets paid first, promote splits at each tier. Total sources and uses."},
    {"task_type": "workout_analysis", "d": "high",
     "p": "Model 5 loan workout options: (1) Extend & Pretend (2-year extension, rate modification), (2) Loan modification (rate reduction + term extension), (3) A/B note split (A-note at par, B-note at discount), (4) Discounted Payoff (DPO at 70-85 cents), (5) Deed-in-lieu of foreclosure. NPV each option from both lender and borrower perspective."},
    {"task_type": "disposition_analysis", "d": "medium",
     "p": "Sell now vs hold & refinance analysis: calculate current market value at today's cap rate, net disposition proceeds after 2% closing costs, reinvestment yield at current rates. Compare to: refinanced hold scenario with equity injection, projected NOI growth, and 5-year exit. Include 1031 exchange considerations. Which path maximizes total return?"},
    {"task_type": "bridge_to_perm", "d": "high",
     "p": "Bridge-to-permanent financing strategy: size a bridge loan (rate, origination fees, exit fee, term), define the stabilization plan (lease-up, capex, NOI target), then size the permanent takeout at stabilized NOI. Calculate total cost of the bridge strategy including negative carry, fees, and carry costs. Compare to immediate permanent refi at current NOI."},
    {"task_type": "ic_memo", "d": "high",
     "p": "Investment Committee memo for acquiring this asset at a distressed basis: purchase price at discount to replacement cost, renovation/stabilization budget, stabilized NOI target, exit cap rate and timeline, levered IRR and equity multiple. Risk factors: rate environment, market fundamentals, execution risk. Recommend: Buy / Pass with confidence score."},
    {"task_type": "market_report", "d": "medium",
     "p": "Capital markets conditions report for this property type and market: current lending environment, CMBS issuance trends, spread levels over Treasuries, lender appetite by source (bank, CMBS, life co, agency, debt fund), recent comparable financings, and forward rate outlook. How does this market compare to 6 months ago?"},
    {"task_type": "structured_output", "d": "medium",
     "p": "Return a JSON Intelligence Object: {property_summary, loan_metrics: {original_loan, original_rate, current_rate, original_dscr, current_dscr, ltv, debt_yield}, refi_analysis: {max_proceeds, equity_gap, binding_constraint}, capital_stack: {senior, mezz, pref_equity, jv_equity, blended_cost}, risk_flags: [], recommended_action, confidence}"},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 2: CMBS DISTRESS
# ═══════════════════════════════════════════════════════════════════════════════

CMBS_DISTRESS_TASKS = [
    {"task_type": "special_servicing_analysis", "d": "high",
     "p": "Analyze this CMBS loan in special servicing: identify default triggers (monetary vs technical vs maturity), estimate cure timeline, model workout options (modification, foreclosure, note sale, REO). Calculate advancing costs, servicer fees, and total exposure. Recommend optimal resolution strategy with timeline."},
    {"task_type": "watchlist_triage", "d": "medium",
     "p": "Triage this watchlist loan: analyze DSCR trend (3-year), occupancy trend, upcoming lease rollover risk, maturity risk, and property condition. Score each risk factor 1-10. Determine: will this loan cure, transfer to special servicing, or need modification? Recommend monitoring actions."},
    {"task_type": "loss_severity_calc", "d": "high",
     "p": "Calculate expected loss severity: current appraised value, estimated disposition timeline (12-24 months), disposition costs (broker fees, legal, closing), accrued and unpaid interest, servicer advances to recover, property preservation costs. Calculate total loss to trust as $ amount and percentage of current balance. Compare loss-given-default vs modification NPV."},
    {"task_type": "modification_analysis", "d": "high",
     "p": "Evaluate loan modification options: (1) rate reduction from current to market, (2) term extension 3-5 years, (3) principal forgiveness (5-15% of UPB), (4) A/B note split with B-note at 50% discount. For each: calculate NPV to trust, impact on DSCR, servicer advancing costs saved. Recommend optimal modification with NPV analysis."},
    {"task_type": "appraisal_review", "d": "medium",
     "p": "Review the implied appraisal: evaluate methodology (income approach cap rate, DCF discount rate, sales comparison adjustments), compare cap rate to market evidence, assess NOI assumptions (revenue growth, expense ratios, vacancy), check comparable sales selection and adjustments. Is the value conclusion reasonable? What's your independent value estimate?"},
    {"task_type": "borrower_analysis", "d": "medium",
     "p": "Analyze borrower financial strength: guarantor net worth and liquidity, other CRE portfolio exposure and leverage, history of workouts and modifications, willingness to inject additional equity, key person risk, entity structure (SPE, guarantor carve-outs). Can this borrower support a modification or will they hand back the keys?"},
    {"task_type": "market_comp", "d": "medium",
     "p": "Distressed comparable transactions: find 3+ recent defaults, REO dispositions, or note sales in same market and property type. For each: property details, original loan amount, loss severity, disposition price per unit/SF, time in special servicing, resolution type. What does the comp data tell us about likely recovery?"},
    {"task_type": "structured_output", "d": "medium",
     "p": "Return a JSON Intelligence Object: {cmbs_deal_name, tranche, loan_metrics: {original_balance, current_balance, coupon, maturity_date, dscr, ltv}, servicer_status: {watchlist, special_servicing, delinquency_days}, loss_estimate: {appraised_value, disposition_costs, loss_severity_pct, loss_to_trust}, workout_recommendation, timeline_months, confidence}"},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 3: RATE ADVISORY
# ═══════════════════════════════════════════════════════════════════════════════

RATE_ADVISORY_TASKS = [
    {"task_type": "rate_lock_analysis", "d": "medium",
     "p": "Rate lock decision: current quoted rate vs floating, forward curve expectation for 6 and 12 months, rate lock cost (deposit, breakage), break-even analysis (at what future rate does locking save money?), historical rate volatility in this range. Recommend: lock now, float 30 days, or float 90 days with rationale."},
    {"task_type": "hedge_strategy", "d": "high",
     "p": "Interest rate hedging strategy: compare (1) interest rate swap (fixed for floating), (2) rate cap (premium, strike, term), (3) collar (cap + floor), (4) swaption (option to enter swap). For each: calculate upfront cost, effective rate, breakeven rate, mark-to-market risk. Recommend optimal hedge for this borrower profile."},
    {"task_type": "rate_sensitivity", "d": "medium",
     "p": "Portfolio rate sensitivity analysis: for a portfolio with this property type, calculate weighted average maturity, repricing risk (floating vs fixed mix), impact on net interest income per 25bp rate move (up and down), duration, and DSCR sensitivity to rate changes. What rate level breaks the portfolio DSCR below 1.0x?"},
    {"task_type": "forward_rate_sizing", "d": "medium",
     "p": "Size the loan using forward rates: today's rate, 6-month forward, 12-month forward. At each rate level, calculate max proceeds at 1.25x DSCR. Show proceeds variance across the forward curve. If rate expectations shift +50bp, how much proceeds loss? Recommend timing strategy."},
    {"task_type": "lender_comparison", "d": "high",
     "p": "Compare 5 lender quotes for this deal: (1) Regional bank — rate, LTV, recourse, relationship, (2) CMBS — rate, LTV, non-recourse, defeasance, (3) Life insurance co — rate, LTV, conservative, prepayment, (4) Agency (Fannie/Freddie) — rate, LTV, multifamily terms, (5) Debt fund — rate, LTV, flexible but expensive. Rank by total cost of capital including fees, rate, and flexibility."},
    {"task_type": "prepayment_analysis", "d": "medium",
     "p": "Prepayment penalty analysis: model the cost of early exit under 4 structures: (1) yield maintenance (Treasury-based), (2) defeasance (purchase Treasuries), (3) step-down (5-4-3-2-1%), (4) open window (last 3-6 months). NPV each option at current Treasury rates. When is the optimal window to refinance given prepayment costs vs rate savings?"},
    {"task_type": "floating_to_fixed", "d": "medium",
     "p": "Convert floating to fixed: current loan at SOFR + spread, available swap rate, net effective fixed rate, DSCR impact of conversion, swap breakage risk if rates drop, ongoing mark-to-market exposure. Compare to simply refinancing into fixed-rate debt. Which path is cheaper over 3, 5, and 7 year horizons?"},
    {"task_type": "structured_output", "d": "medium",
     "p": "Return a JSON Intelligence Object: {rate_environment: {sofr, treasury_10yr, fed_funds}, property_summary, loan_details: {current_rate, rate_type, maturity, balance}, hedge_recommendation: {strategy, cost, effective_rate}, lender_comparison: [{source, rate, ltv, structure}], dscr_impact: {current, hedged, stressed}, confidence}"},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 4: EQUITY ADVISORY
# ═══════════════════════════════════════════════════════════════════════════════

EQUITY_ADVISORY_TASKS = [
    {"task_type": "jv_structure", "d": "high",
     "p": "Structure a JV equity deal: GP contribution (5-10% co-invest), LP equity required, 8% preferred return with quarterly compounding, promote waterfall (above 8% pref: 70/30 LP/GP, above 12% IRR: 60/40, above 18% IRR: 50/50), clawback provisions, catch-up, key person provisions. Calculate GP promote at 12%, 15%, 18%, and 22% project IRR. Show total GP economics (co-invest return + promote)."},
    {"task_type": "fund_formation", "d": "high",
     "p": "Model fund economics: $100-500M target raise, 1.5% management fee on committed capital (investment period) then on invested capital, 20% carried interest above 8% preferred return, 50% GP catch-up, 1-3% GP commitment. Calculate: management fee revenue by year, total carry at fund IRR of 12%, 15%, 18%. Show J-curve and crossover year."},
    {"task_type": "preferred_equity", "d": "high",
     "p": "Analyze preferred equity terms: 10-14% current pay, payment frequency (monthly/quarterly), accrual mechanics if cash flow insufficient, conversion option to common equity, subordination position relative to senior debt, exit provisions (call protection, minimum hold, put option). Compare preferred equity cost vs mezz debt cost. When is each more appropriate?"},
    {"task_type": "capital_call", "d": "medium",
     "p": "Equity capital call analysis: existing investor assessment (ability and willingness to fund additional equity), additional capital required to refinance/stabilize, dilution impact on existing investors if they don't participate, rescue capital terms (penalty rate, super-priority, promote override). Model pre vs post dilution returns. Draft capital call notice."},
    {"task_type": "waterfall_calc", "d": "high",
     "p": "Full waterfall calculation with actual numbers: contributed capital by tier, accrued preferred return (8%), return of capital, Tier 1 promote (70/30 to 12% IRR), Tier 2 promote (60/40 to 18% IRR), Tier 3 promote (50/50 above 18%). Calculate distributions at 3 exit scenarios: base case, downside -20%, upside +30%. Show GP and LP total returns, IRR, and equity multiple for each."},
    {"task_type": "investor_memo", "d": "high",
     "p": "LP investor memorandum: investment strategy and thesis (why this market, why now), property summary and financial projections (5-year proforma), target returns (IRR, equity multiple, cash-on-cash), fee structure and alignment, risk factors (5 specific risks with mitigations), terms summary (structure, minimum, regulatory pathway), exit strategy and timeline."},
    {"task_type": "disposition_waterfall", "d": "high",
     "p": "Calculate sale proceeds waterfall: gross sale price, less: broker commission, transfer taxes, legal/closing costs, outstanding debt payoff, working capital adjustment. Net to equity. Then distribute through promote waterfall: return of capital, preferred return (accrued), Tier 1 promote, Tier 2 promote. Show GP total take (co-invest + promote), LP net return, IRR, multiple."},
    {"task_type": "structured_output", "d": "medium",
     "p": "Return a JSON Intelligence Object: {deal_summary, equity_structure: {total_equity, gp_coinvest, lp_equity, pref_rate, promote_tiers}, waterfall: {tier_1, tier_2, tier_3}, returns: {gp_irr, lp_irr, gp_multiple, lp_multiple}, investor_terms: {minimum, regulatory, lockup}, confidence}"},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 5: VALUATION ADVISORY
# ═══════════════════════════════════════════════════════════════════════════════

VALUATION_ADVISORY_TASKS = [
    {"task_type": "income_approach", "d": "medium",
     "p": "Direct capitalization valuation: calculate stabilized NOI from rent roll (gross potential revenue, vacancy loss, expense reimbursements, operating expenses). Apply market cap rate with support from recent transactions. Show per-SF and per-unit value. Sensitivity: value at cap rate +/- 25bp and +/- 50bp."},
    {"task_type": "dcf_valuation", "d": "high",
     "p": "10-year discounted cash flow: project year-by-year revenue growth (2-3%), expense growth (3-4%), lease rollovers with market rent adjustments, capital reserves. Reversion value at Year 10 exit cap rate (entry + 50bp). Discount at 8-10% rate. Calculate NPV, IRR at purchase price, and implied going-in cap rate. Show full cash flow table."},
    {"task_type": "sales_comparison", "d": "medium",
     "p": "Sales comparison approach: identify 3+ comparable sales. For each: sale price, date, $/SF or $/unit, cap rate, property condition, location quality, size. Apply adjustments for: condition (+/-5-15%), location (+/-5-10%), size (+/-3-8%), age (+/-5-10%), market conditions (time). Calculate adjusted value range and reconciled value."},
    {"task_type": "cost_approach", "d": "medium",
     "p": "Replacement cost analysis: land value (comparable land sales), hard construction costs (per-SF by property type), soft costs (15-20% of hard), entrepreneurial profit (10-15%), less: physical depreciation, functional obsolescence, external obsolescence. Show indicated value vs income approach value. Calculate replacement cost premium/discount."},
    {"task_type": "distressed_valuation", "d": "high",
     "p": "Distressed vs stabilized valuation: (1) As-Is value in current condition (current occupancy, current NOI, distressed cap rate), (2) Stabilized value (target occupancy, market rents, stabilized cap rate), (3) Cost to stabilize (capital improvements, lease-up costs, carry costs, time). Calculate the spread and whether the distressed basis supports the stabilization investment."},
    {"task_type": "cap_rate_analysis", "d": "medium",
     "p": "Cap rate decomposition: risk-free rate (10yr Treasury) + risk premium (property type, market, tenant quality) + illiquidity premium + growth adjustment. Build up the cap rate from components. Compare to observed market cap rates. Is the market cap rate justified? What is implied growth rate? How does this cap rate compare to the 10-year average?"},
    {"task_type": "highest_best_use", "d": "high",
     "p": "Highest and best use analysis: evaluate current use vs potential conversions (office → residential, retail → industrial, hotel → multifamily). For each option: zoning compatibility, conversion cost estimate, stabilized NOI post-conversion, value post-conversion, construction timeline, market demand for converted use. Which use maximizes value?"},
    {"task_type": "structured_output", "d": "medium",
     "p": "Return a JSON Intelligence Object: {property_summary, income_value: {noi, cap_rate, value}, dcf_value: {discount_rate, terminal_cap, npv, irr}, sales_comp_value: {comps_count, adjusted_range, reconciled}, cost_value: {replacement_cost, depreciation, indicated}, reconciled_value, methodology_weights: {income, dcf, sales, cost}, confidence}"},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 6: DEAL ORIGINATION
# ═══════════════════════════════════════════════════════════════════════════════

DEAL_ORIGINATION_TASKS = [
    {"task_type": "signal_analysis", "d": "medium",
     "p": "Analyze this market signal as a deal opportunity: loan maturity date approaching, ownership entity changes, occupancy decline, major lease expiration, tax delinquency, or CMBS watchlist entry. Assess: deal probability (0-1), estimated value, likely seller motivation, optimal approach strategy, timing urgency. What's the play?"},
    {"task_type": "cold_outreach", "d": "medium",
     "p": "Draft a property-specific broker cold outreach: reference the specific market condition driving the opportunity (maturity wall, rate environment, market shift), include 2-3 data points about their property or market, propose a specific meeting purpose (refinancing advisory, disposition analysis, capital alternatives). Professional, concise, not salesy. Include subject line."},
    {"task_type": "listing_proposal", "d": "high",
     "p": "Prepare a listing proposal to win the mandate: market overview (supply, demand, cap rate trends), pricing strategy (recommended list price with comp support, pricing vs market, expected days on market), marketing plan (target buyer profile, marketing channels, offering memorandum outline), fee structure and team qualifications. Why should the owner hire you?"},
    {"task_type": "buyer_matching", "d": "medium",
     "p": "Match this property to the optimal buyer pool: investor criteria analysis — return requirements (IRR, cash-on-cash), size range, property type preference, geographic focus, 1031 exchange buyers (identify timeline constraints), institutional vs private capital, value-add vs core investors. Rank top 5 buyer profiles by fit score (0-1)."},
    {"task_type": "pricing_strategy", "d": "medium",
     "p": "Pricing analysis: recommended list price (with cap rate and per-unit/SF support), expected sale price (list-to-sale ratio for this market), price vs replacement cost, pricing relative to recent comps, predicted days on market at this price, bid strategy (best-and-final vs negotiated). What price maximizes seller proceeds while ensuring market velocity?"},
    {"task_type": "due_diligence_checklist", "d": "medium",
     "p": "Generate a due diligence checklist specific to this property type: prioritized by importance and timeline. Categories: financial (T12, rent roll, AR/AP), physical (Phase I ESA, PCA, survey), legal (title, zoning letter, estoppels, SNDAs), lease (lease abstracts, tenant credit, expiration schedule), tax (assessment history, appeal opportunity). Include red flags to watch for and estimated timeline."},
    {"task_type": "closing_coordination", "d": "medium",
     "p": "Closing timeline and coordination plan: Day 1-30 due diligence period (checklist with responsible parties), Day 30-45 financing contingency (lender selection, application, appraisal), Day 45-60 title/escrow (title commitment review, survey, objections), Day 55-60 closing (prorations, closing statement, wire instructions, post-closing deliverables). Flag common deal-killers at each stage."},
    {"task_type": "structured_output", "d": "medium",
     "p": "Return a JSON Intelligence Object: {signal_source, property_summary, deal_opportunity: {probability, estimated_value, seller_motivation}, recommended_action, buyer_profile: {type, return_target, size_range}, pricing: {list_price, expected_price, cap_rate, per_unit}, timeline: {dd_days, financing_days, closing_target}, confidence}"},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

# Map stream to system prompt category
_STREAM_PROMPT_CATEGORY = {
    "debt_maturity": "capital_advisor",
    "cmbs_distress": "distressed_specialist",
    "rate_advisory": "capital_advisor",
    "equity_advisory": "investment_banker",
    "valuation_advisory": "capital_advisor",
    "deal_origination": "deal_originator",
}

STREAMS = {
    "debt_maturity":      {"tasks": DEBT_MATURITY_TASKS,      "assets": CAPITAL_ASSETS},
    "cmbs_distress":      {"tasks": CMBS_DISTRESS_TASKS,      "assets": CAPITAL_ASSETS},
    "rate_advisory":      {"tasks": RATE_ADVISORY_TASKS,      "assets": CAPITAL_ASSETS},
    "equity_advisory":    {"tasks": EQUITY_ADVISORY_TASKS,    "assets": CAPITAL_ASSETS},
    "valuation_advisory": {"tasks": VALUATION_ADVISORY_TASKS, "assets": CAPITAL_ASSETS},
    "deal_origination":   {"tasks": DEAL_ORIGINATION_TASKS,   "assets": CAPITAL_ASSETS},
}


def _get_system_prompt(stream: str, rng: random.Random) -> str:
    """Pick a system prompt from the stream's category."""
    category = _STREAM_PROMPT_CATEGORY[stream]
    return rng.choice(_SYSTEM_PROMPTS[category])


# ═══════════════════════════════════════════════════════════════════════════════
# SKELETON GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def _seed_rng(prefix: str, seed: int, index: int) -> random.Random:
    h = hashlib.sha256(f"{seed}:{prefix}:{index}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def generate_skeleton(stream: str, seed: int, index: int) -> dict:
    """Generate a capital markets deal skeleton."""
    rng = _seed_rng(stream, seed, index)
    assets = STREAMS[stream]["assets"]
    asset_type = rng.choice(list(assets.keys()))
    spec = assets[asset_type]
    market_name, state, subs = rng.choice(MARKETS)

    sk = {
        "deal_id": f"CM-{stream[:4].upper()}-{seed}-{index:06d}",
        "asset_type": asset_type,
        "asset_type_display": spec["display"],
        "market_name": market_name,
        "state": state,
        "submarket": rng.choice(subs),
    }

    for key, val in spec["params"].items():
        if isinstance(val, tuple) and len(val) >= 2:
            lo, hi = val[0], val[1]
            if isinstance(lo, int) and isinstance(hi, int):
                sk[key] = rng.randint(lo, hi)
            elif isinstance(lo, float) or isinstance(hi, float):
                sk[key] = round(rng.uniform(float(lo), float(hi)), 4)

    # Add CMBS-specific params for distress stream
    if stream == "cmbs_distress":
        sk["cmbs_deal_name"] = f"CMBS {rng.choice(['GSMS', 'JPMCC', 'WFCM', 'MSBAM', 'CGCMT', 'BMARK', 'CSMC'])} {rng.randint(2018, 2023)}-{rng.choice(['C', 'SB', 'LC'])}{rng.randint(1, 20)}"
        sk["tranche"] = rng.choice(["AAA", "AA", "A", "BBB-", "BBB", "BB", "B"])
        sk["delinquency_days"] = rng.choice([0, 30, 60, 90, 120, 180])
        sk["watchlist_status"] = rng.choice([True, False])
        sk["special_servicing"] = rng.choice([True, False]) if sk["delinquency_days"] >= 60 else False

    # Add rate-specific params for rate advisory
    if stream == "rate_advisory":
        sk["current_sofr"] = round(rng.uniform(4.25, 4.50), 4)
        sk["treasury_10yr"] = round(rng.uniform(4.10, 4.35), 4)
        sk["quoted_spread_bps"] = rng.randint(150, 350)
        sk["rate_type"] = rng.choice(["fixed", "floating"])

    # Add equity-specific params for equity advisory
    if stream == "equity_advisory":
        sk["equity_required"] = rng.randint(2_000_000, 50_000_000)
        sk["target_irr"] = round(rng.uniform(0.12, 0.22), 4)
        sk["hold_period_years"] = rng.choice([3, 5, 7, 10])
        sk["gp_coinvest_pct"] = round(rng.uniform(0.05, 0.15), 4)

    # Property name
    prefix = asset_type.replace("_", " ").title().split()[0]
    suffix = rng.choice(["Tower", "Plaza", "Center", "Place", "Commons", "Park", "Point", "Crossing"])
    numeral = rng.choice(["I", "II", "III", "IV", "", ""])
    sk["property_name"] = f"{sk['submarket']} {prefix} {suffix} {numeral}".strip()

    return sk


def format_skeleton(sk: dict) -> str:
    """Format skeleton into prompt-friendly text."""
    lines = [f"Property: {sk['property_name']}", f"Type: {sk['asset_type_display']}",
             f"Location: {sk['submarket']}, {sk['market_name']}, {sk['state']}", ""]

    # Key financial params first
    priority_keys = ["original_loan", "original_rate", "current_market_rate",
                     "remaining_term_months", "noi", "blended_noi", "cap_rate",
                     "occupancy", "sf", "units", "rooms"]
    shown = set()

    for key in priority_keys:
        if key in sk:
            shown.add(key)
            lines.append(_format_param(key, sk[key]))

    for key, val in sk.items():
        if key in ("deal_id", "asset_type", "asset_type_display", "property_name",
                    "market_name", "state", "submarket") or key in shown:
            continue
        lines.append(_format_param(key, val))

    return "\n".join(lines)


def _format_param(key: str, val) -> str:
    label = key.replace("_", " ").title()
    if isinstance(val, float):
        if val > 1_000_000:
            return f"  {label}: ${val:,.0f}"
        elif val < 1:
            if "rate" in key or "pct" in key or "factor" in key or "yield" in key:
                return f"  {label}: {val*100:.2f}%"
            return f"  {label}: {val:.4f}"
        else:
            return f"  {label}: {val:,.2f}"
    elif isinstance(val, int):
        if val > 1_000_000:
            return f"  {label}: ${val:,.0f}"
        elif val > 1000:
            return f"  {label}: {val:,}"
        else:
            return f"  {label}: {val}"
    elif isinstance(val, bool):
        return f"  {label}: {'Yes' if val else 'No'}"
    elif isinstance(val, str):
        return f"  {label}: {val}"
    return f"  {label}: {val}"


# ═══════════════════════════════════════════════════════════════════════════════
# QUALITY CHECK
# ═══════════════════════════════════════════════════════════════════════════════

_TRAJ_PATTERNS = [
    re.compile(r"\*?\*?1\.\s*IDENTIFY", re.IGNORECASE),
    re.compile(r"\*?\*?2\.\s*CALCULATE", re.IGNORECASE),
    re.compile(r"\*?\*?3\.\s*ANALYZE", re.IGNORECASE),
    re.compile(r"\*?\*?4\.\s*EVALUATE", re.IGNORECASE),
    re.compile(r"\*?\*?5\.\s*RECOMMEND", re.IGNORECASE),
]
_NUM_PATTERN = re.compile(r"\$[\d,.]+|\d+\.?\d*\s*%|\d{1,3}(,\d{3})+")
_DEGEN_PATTERN = re.compile(r"(.{40,})\1{2,}")


def quality_check(content: str) -> tuple[bool, str]:
    if len(content) < 500:
        return False, "too_short"
    steps_found = sum(1 for p in _TRAJ_PATTERNS if p.search(content))
    if steps_found < 3:
        return False, f"trajectory_{steps_found}/5"
    nums = _NUM_PATTERN.findall(content)
    if len(nums) < 3:
        return False, "no_financials"
    if _DEGEN_PATTERN.search(content):
        return False, "degenerate"
    return True, "pass"


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKPOINTING
# ═══════════════════════════════════════════════════════════════════════════════

CHECKPOINT_FILE = OUTPUT_DIR / "checkpoints.json"
_ckpt_lock = threading.Lock()


def load_checkpoint(stream: str) -> dict:
    if CHECKPOINT_FILE.exists():
        data = json.loads(CHECKPOINT_FILE.read_text())
        return data.get(stream, {"written": 0, "done_deals": []})
    return {"written": 0, "done_deals": []}


def save_checkpoint(stream: str, written: int, done_deals: list):
    with _ckpt_lock:
        data = {}
        if CHECKPOINT_FILE.exists():
            data = json.loads(CHECKPOINT_FILE.read_text())
        data[stream] = {"written": written, "done_deals": done_deals[-10000:]}
        CHECKPOINT_FILE.write_text(json.dumps(data))


# ═══════════════════════════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════════════════════════

PROGRESS_FILE = OUTPUT_DIR / "progress.json"
_progress_lock = threading.Lock()
_progress = {"streams": {}, "start_time": None}


def update_progress(stream: str, written: int, target: int,
                    gen_pass: int = 0, rewritten: int = 0, failed: int = 0):
    with _progress_lock:
        _progress["streams"][stream] = {
            "written": written, "target": target,
            "gen_pass": gen_pass, "rewritten": rewritten, "failed": failed,
        }
        total_written = sum(s["written"] for s in _progress["streams"].values())
        total_target = sum(s["target"] for s in _progress["streams"].values())
        total_gen = sum(s.get("gen_pass", 0) for s in _progress["streams"].values())
        total_rw = sum(s.get("rewritten", 0) for s in _progress["streams"].values())
        elapsed = time.time() - (_progress["start_time"] or time.time())
        rate = total_written / max(elapsed / 60, 0.1)
        remaining = total_target - total_written
        eta_hours = (remaining / max(rate, 1)) / 60

        PROGRESS_FILE.write_text(json.dumps({
            "total_written": total_written,
            "total_target": total_target,
            "gen_pass": total_gen,
            "rewritten": total_rw,
            "rewrite_rate": round(total_rw / max(total_gen + total_rw, 1) * 100, 1),
            "api_calls": api_calls["total"],
            "gen_calls": api_calls.get("gen", 0),
            "pass_calls": api_calls.get("pass", 0),
            "errors": api_calls.get("error", 0),
            "rate_per_min": round(rate, 1),
            "elapsed_min": round(elapsed / 60, 1),
            "eta_hours": round(eta_hours, 1),
            "gen_model": GEN_MODEL,
            "pass_model": PASS_MODEL,
            "streams": _progress["streams"],
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# PAIR GENERATION — Two-Tier: 80B gen → quality check → 235B rewrite
# ═══════════════════════════════════════════════════════════════════════════════

def grind_pair(stream: str, skeleton: dict, task: dict, task_idx: int,
               system_prompt: str) -> dict | None:
    summary = format_skeleton(skeleton)
    user_msg = f"{task['p']}\n\n{summary}"

    # Tier 1: Fast gen with 80B turbo
    with api_lock:
        api_calls["gen"] += 1
    content = together_call(system_prompt, user_msg, model=GEN_MODEL)

    if not content:
        return None

    # Quality gate
    passed, reason = quality_check(content)
    tier = "gen"

    if not passed:
        # Tier 2: 235B rewrite
        with api_lock:
            api_calls["pass"] += 1
        content = together_call(system_prompt, user_msg, model=PASS_MODEL)
        if not content:
            return None
        tier = "rewrite"

    return {
        "id": f"swarmcapital-{stream}-{skeleton['deal_id']}-t{task_idx}",
        "deal_id": skeleton["deal_id"],
        "task_type": task["task_type"],
        "difficulty": task["d"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": content},
        ],
        "metadata": {
            "stream": stream,
            "task_type": task["task_type"],
            "asset_type": skeleton["asset_type"],
            "market_name": skeleton["market_name"],
            "state": skeleton.get("state", ""),
            "model": GEN_MODEL if tier == "gen" else PASS_MODEL,
            "tier": tier,
            "source": "cre-capital-cook-v1",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STREAM GRINDER (parallel)
# ═══════════════════════════════════════════════════════════════════════════════

def grind_stream(stream: str, target: int | None = None, seed: int = SEED):
    cfg = STREAMS[stream]
    tasks = cfg["tasks"]
    target = target or STREAM_TARGETS[stream]
    deals_needed = (target // len(tasks)) + 1

    out_file = OUTPUT_DIR / f"stream_{stream}.jsonl"

    print(f"\n{'='*70}")
    print(f"  STREAM: {stream.upper()} — {target:,} pairs")
    print(f"  Assets: {len(cfg['assets'])} types | Tasks: {len(tasks)} per deal")
    print(f"  Deals: {deals_needed:,} x {len(tasks)} = {deals_needed * len(tasks):,} potential")
    print(f"  Gen: {GEN_MODEL}")
    print(f"  Pass: {PASS_MODEL}")
    print(f"  Workers: {WORKERS}")
    print(f"{'='*70}")

    # Load checkpoint
    cp = load_checkpoint(stream)
    done_deals = set(cp.get("done_deals", []))
    written = cp.get("written", 0)
    gen_pass = written
    rewritten = 0
    failed = 0

    if written >= target:
        print(f"  Complete: {written:,} pairs")
        return written

    print(f"  Resuming: {written:,} done, {target - written:,} remaining")

    # Test both models
    print(f"  Testing Gen model (80B)...")
    test1 = together_call("You are SwarmCapital.", "Say 'ready'.",
                          model=GEN_MODEL, max_tokens=10, min_len=1)
    if not test1:
        print(f"  FAIL — Gen model not responding")
        return written
    print(f"  Gen: OK")

    print(f"  Testing Pass model (235B)...")
    test2 = together_call("You are SwarmCapital.", "Say 'ready'.",
                          model=PASS_MODEL, max_tokens=10, min_len=1)
    if not test2:
        print(f"  FAIL — Pass model not responding")
        return written
    print(f"  Pass: OK")

    t0 = time.time()
    session_start = written

    # Build work queue with system prompt per deal
    work_queue = []
    for deal_idx in range(deals_needed):
        deal_key = f"{stream}-{deal_idx}"
        if deal_key in done_deals:
            continue
        deal_rng = _seed_rng(f"{stream}-sysprompt", seed, deal_idx)
        sys_prompt = _get_system_prompt(stream, deal_rng)
        for task_idx, task in enumerate(tasks):
            work_queue.append((deal_idx, task_idx, task, sys_prompt))
        if written + len(work_queue) >= target:
            break

    print(f"  Work queue: {len(work_queue):,} tasks")

    # Process with thread pool
    file_lock = threading.Lock()
    done_deals_lock = threading.Lock()

    def process_task(item):
        nonlocal written, gen_pass, rewritten, failed
        deal_idx, task_idx, task, sys_prompt = item

        if written >= target:
            return None

        skeleton = generate_skeleton(stream, seed, deal_idx)
        rec = grind_pair(stream, skeleton, task, task_idx, sys_prompt)

        if rec:
            with file_lock:
                if written >= target:
                    return None
                with open(out_file, "a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if _safestore:
                    _safestore.save(rec)
                written += 1
                if rec["metadata"]["tier"] == "gen":
                    gen_pass += 1
                else:
                    rewritten += 1

                deal_key = f"{stream}-{deal_idx}"
                with done_deals_lock:
                    done_deals.add(deal_key)

                if written % 100 == 0:
                    elapsed = time.time() - t0
                    rate = (written - session_start) / max(elapsed / 60, 0.1)
                    remaining = target - written
                    eta = (remaining / max(rate, 1)) / 60
                    pct = written / target * 100
                    rw_pct = rewritten / max(gen_pass + rewritten, 1) * 100
                    print(f"  [{written:,}/{target:,}] ({pct:.0f}%) "
                          f"rate={rate:.0f}/min ETA={eta:.1f}h "
                          f"gen={gen_pass:,} rw={rewritten:,} ({rw_pct:.0f}%) "
                          f"err={api_calls.get('error', 0)}")
                    update_progress(stream, written, target, gen_pass, rewritten, failed)

                if written % 500 == 0:
                    save_checkpoint(stream, written, list(done_deals)[-10000:])

            return rec
        else:
            with file_lock:
                failed += 1
            return None

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(process_task, item): item for item in work_queue}
        for future in as_completed(futures):
            try:
                future.result()
            except RuntimeError as e:
                print(f"\n  FATAL: {e}")
                break
            except Exception:
                pass

            if written >= target:
                break

    # Final checkpoint
    save_checkpoint(stream, written, list(done_deals)[-10000:])
    update_progress(stream, written, target, gen_pass, rewritten, failed)

    elapsed = time.time() - t0
    rate = (written - session_start) / max(elapsed / 60, 0.1)
    rw_pct = rewritten / max(gen_pass + rewritten, 1) * 100
    print(f"\n  Stream {stream} done: {written:,} pairs, {rate:.0f}/min, "
          f"{elapsed/60:.1f} min")
    print(f"  Gen pass: {gen_pass:,} | Rewritten by 235B: {rewritten:,} ({rw_pct:.0f}%)")
    print(f"  API calls: {api_calls['total']:,} (gen={api_calls.get('gen',0)}, "
          f"pass={api_calls.get('pass',0)})")

    return written


# ═══════════════════════════════════════════════════════════════════════════════
# ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════════

def assemble():
    print(f"\n{'='*70}")
    print(f"  ASSEMBLY")
    final = OUTPUT_DIR / "cre_capital_30k.jsonl"
    total = 0
    gen_total = 0
    rw_total = 0
    for stream in STREAM_TARGETS:
        sf = OUTPUT_DIR / f"stream_{stream}.jsonl"
        if sf.exists():
            count = 0
            gen_c = 0
            rw_c = 0
            with open(sf) as f:
                for line in f:
                    rec = json.loads(line)
                    count += 1
                    if rec.get("metadata", {}).get("tier") == "rewrite":
                        rw_c += 1
                    else:
                        gen_c += 1
            size = sf.stat().st_size / (1024 * 1024)
            print(f"  {stream:<20} {count:>7,} pairs  (gen={gen_c:,} rw={rw_c:,})  ({size:.1f} MB)")
            total += count
            gen_total += gen_c
            rw_total += rw_c
        else:
            print(f"  {stream:<20} MISSING")

    if total == 0:
        print(f"\n  No data to assemble.")
        return

    with open(final, "w") as out:
        for stream in STREAM_TARGETS:
            sf = OUTPUT_DIR / f"stream_{stream}.jsonl"
            if sf.exists():
                with open(sf) as f:
                    for line in f:
                        out.write(line)

    size = final.stat().st_size / (1024 * 1024)
    rw_pct = rw_total / max(total, 1) * 100
    print(f"\n  Total: {total:,} pairs, {size:.1f} MB")
    print(f"  Gen pass: {gen_total:,} | 235B rewrites: {rw_total:,} ({rw_pct:.0f}%)")
    print(f"  Output: {final}")


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def show_status():
    if PROGRESS_FILE.exists():
        p = json.loads(PROGRESS_FILE.read_text())
        print(f"\nCRE Capital Markets Cook — Two-Tier Progress")
        print(f"  Gen:  {p.get('gen_model', '?')}")
        print(f"  Pass: {p.get('pass_model', '?')}")
        print(f"  Total: {p['total_written']:,} / {p['total_target']:,}")
        print(f"  Gen pass: {p.get('gen_pass', 0):,} | Rewrites: {p.get('rewritten', 0):,} "
              f"({p.get('rewrite_rate', 0)}%)")
        print(f"  Rate: {p['rate_per_min']}/min | ETA: {p['eta_hours']}h")
        print(f"  API: total={p['api_calls']:,} gen={p.get('gen_calls',0):,} "
              f"pass={p.get('pass_calls',0):,} err={p.get('errors',0)}")
        for name, s in p.get("streams", {}).items():
            rw = s.get("rewritten", 0)
            gp = s.get("gen_pass", 0)
            print(f"    {name:<20} {s['written']:>7,} / {s['target']:>7,}  "
                  f"(gen={gp:,} rw={rw:,})")
    else:
        print("No progress data yet.")

    print(f"\n  Stream files:")
    for stream in STREAM_TARGETS:
        sf = OUTPUT_DIR / f"stream_{stream}.jsonl"
        if sf.exists():
            count = sum(1 for _ in open(sf))
            print(f"    {stream:<20} {count:>7,} pairs")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global WORKERS
    parser = argparse.ArgumentParser(
        description="CRE Capital Markets Cook — Two-Tier: 80B turbo gen + 235B pass/rewrite")
    parser.add_argument("--stream", choices=[*STREAM_TARGETS.keys(), "all"], default="all")
    parser.add_argument("--target", type=int, help="Override target for single stream")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--quality", action="store_true",
                        help="Use 235B for ALL generation (skip 80B tier — slower but max quality)")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.assemble:
        assemble()
        return

    api_key = os.environ.get("TOGETHER_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: Set TOGETHER_KEY environment variable")
        sys.exit(1)

    if api_key:
        init_session(api_key)

    WORKERS = args.workers

    # --quality mode: 397B flagship for ALL generation (max quality)
    if args.quality:
        GEN_MODEL = QUALITY_MODEL
        PASS_MODEL = QUALITY_MODEL
        print(f"{'='*70}")
        print(f"  CRE CAPITAL MARKETS COOK v1 — QUALITY MODE (397B flagship)")
        print(f"  The $1.5T Debt Maturity Wall — Capital Markets Intelligence")
        print(f"  Model: {QUALITY_MODEL} (397B, 17B active) — ALL generation")
        print(f"  Workers: {WORKERS}")
        print(f"  Flow: 397B gen → quality gate → 397B retry if fail")
        print(f"{'='*70}")
    else:
        print(f"{'='*70}")
        print(f"  CRE CAPITAL MARKETS COOK v1 — 30K Pairs (Two-Tier)")
        print(f"  The $1.5T Debt Maturity Wall — Capital Markets Intelligence")
        print(f"  Tier 1 Gen:  {GEN_MODEL} (turbo, 3B active)")
        print(f"  Tier 2 Pass: {PASS_MODEL} (quality, 22B active)")
        print(f"  Workers: {WORKERS}")
        print(f"  Flow: 80B gen → quality gate → pass or 235B rewrite")
        print(f"{'='*70}")

    if args.dry_run:
        for stream in (STREAM_TARGETS if args.stream == "all" else {args.stream: 0}):
            cfg = STREAMS[stream]
            target = args.target or STREAM_TARGETS[stream]
            deals = (target // len(cfg["tasks"])) + 1
            print(f"\n{'='*70}")
            print(f"  STREAM: {stream.upper()} — {target:,} pairs")
            print(f"  Assets: {len(cfg['assets'])} types | Tasks: {len(cfg['tasks'])} per deal")
            print(f"  Deals: {deals:,} x {len(cfg['tasks'])} = {deals * len(cfg['tasks']):,} potential")
            print(f"  System prompt category: {_STREAM_PROMPT_CATEGORY[stream]}")
            print(f"{'='*70}")
            for i in range(3):
                sk = generate_skeleton(stream, SEED, i)
                print(f"\n  --- {sk['deal_id']} ---")
                print(f"  {format_skeleton(sk)}")
        return

    # Initialize SafeStore
    global _safestore
    total_target = sum(
        args.target or STREAM_TARGETS[s]
        for s in (STREAM_TARGETS if args.stream == "all"
                  else {args.stream: STREAM_TARGETS[args.stream]})
    )
    _safestore = SafeStore(
        "cre_capital",
        bucket="sb-cre",
        prefix="capital/",
        domain="cre",
        r2_push_every=500,
        supabase_push_every=100,
    )
    _safestore.start(total_expected=total_target)

    _progress["start_time"] = time.time()

    streams_to_run = (STREAM_TARGETS if args.stream == "all"
                      else {args.stream: STREAM_TARGETS[args.stream]})
    for stream, default_target in streams_to_run.items():
        target = args.target or default_target
        grind_stream(stream, target=target, seed=args.seed)

    assemble()

    if _safestore:
        _safestore.finalize()

    rw_rate = api_calls.get("pass", 0) / max(api_calls.get("gen", 0), 1) * 100
    print(f"\n{'='*70}")
    print(f"  DONE — CRE Capital Markets Cook v1 Complete")
    print(f"  API calls: {api_calls['total']:,}")
    print(f"    Gen (80B):   {api_calls.get('gen', 0):,}")
    print(f"    Pass (235B): {api_calls.get('pass', 0):,} ({rw_rate:.0f}% rewrite rate)")
    print(f"    Errors:      {api_calls.get('error', 0)}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
