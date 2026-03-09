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
    "macro_causality": 2_400,
    "deal_graph": 500,
}
TOTAL_TARGET = sum(STREAM_TARGETS.values())  # 32,400

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
# FORMAT DIVERSIFICATION — break structural monotony for training entropy
# ═══════════════════════════════════════════════════════════════════════════════

FORMAT_TEMPLATES = {
    "trajectory_5step": {
        "weight": 0.25,
        "instructions": TRAJECTORY_INSTRUCTIONS,
    },
    "trajectory_3step": {
        "weight": 0.10,
        "instructions": """
RESPONSE FORMAT — Use this 3-step framework:

**ASSESS**: Property, market, loan structure, key financials. Show the math — every formula, every input.
**ANALYZE**: Market conditions, lender landscape, comparable transactions, rate implications. What does a senior broker see?
**DECIDE**: Proceed / Restructure / Sell / Kill with confidence (0.0–1.0) and prioritized action items.
""",
    },
    "executive_brief": {
        "weight": 0.12,
        "instructions": """
RESPONSE FORMAT — Executive brief style. Be direct and concise:

Start with a one-line verdict (e.g., "RESTRUCTURE — DSCR below breakeven at current rates").
Then provide: key metrics (3-5 bullet points with actual numbers), critical finding, recommended action, and confidence score.
No section headers. No step numbering. Write like a senior MD sending a quick take to a client.
""",
    },
    "narrative_memo": {
        "weight": 0.12,
        "instructions": """
RESPONSE FORMAT — Write as a narrative capital markets memo. No numbered steps or bold headers.

Open with the situation and thesis. Weave financial calculations naturally into prose paragraphs.
Use specific numbers (DSCR, LTV, debt yield, basis points) throughout but in flowing text, not bullet lists.
Close with a clear recommendation and confidence level. Think Wall Street research note, not template.
""",
    },
    "tabular_analysis": {
        "weight": 0.10,
        "instructions": """
RESPONSE FORMAT — Lead with tables and structured data:

Use markdown tables for: scenario comparisons, lender quotes, capital stack tiers, sensitivity matrices.
Follow each table with 1-2 sentences of interpretation. Minimize prose — let the numbers tell the story.
End with: Verdict (one word), Confidence (0.0-1.0), Next Steps (3 bullets max).
""",
    },
    "conversational": {
        "weight": 0.10,
        "instructions": """
RESPONSE FORMAT — Respond as if speaking directly to a CRE broker in a meeting.

Use natural, professional language. "Here's the deal..." or "The math tells us..." style.
Include all the financial rigor (exact DSCR, LTV, debt service calculations) but deliver it
conversationally. Anticipate follow-up questions. End with a clear recommendation.
No markdown formatting, no bold headers, no numbered lists.
""",
    },
    "json_intelligence_object": {
        "weight": 0.12,
        "instructions": """
RESPONSE FORMAT — Return a JSON Intelligence Object. Structure it as valid JSON with these sections:

{
  "verdict": "PROCEED|RESTRUCTURE|SELL|KILL",
  "confidence": 0.0-1.0,
  "property": {summary object},
  "financials": {all calculated metrics with formulas shown as strings},
  "market_context": {relevant market data and comparables},
  "risk_flags": [array of specific risks],
  "action_items": [prioritized next steps],
  "analysis_notes": "brief narrative connecting the numbers"
}

Every number must be calculated, not estimated. Include the formula used.
""",
    },
    "risk_matrix": {
        "weight": 0.09,
        "instructions": """
RESPONSE FORMAT — Risk-first analysis:

Start with a risk matrix: list 5-7 specific risks, each with probability (LOW/MED/HIGH), impact (LOW/MED/HIGH), and mitigation.
Then provide the financial underpinning: key calculations that quantify each risk.
End with: overall risk score (1-10), go/no-go recommendation, and confidence (0.0-1.0).
""",
    },
}

# Build weighted selection list
_FORMAT_CHOICES = []
_FORMAT_WEIGHTS = []
for fmt_name, fmt_cfg in FORMAT_TEMPLATES.items():
    _FORMAT_CHOICES.append(fmt_name)
    _FORMAT_WEIGHTS.append(fmt_cfg["weight"])


START_PHRASE_VARIATIONS = [
    "",  # No override — use format's natural opening (30% via weight below)
    "\nIMPORTANT: Begin your response by naming the specific property and market before any analysis.",
    "\nIMPORTANT: Open with the single most critical finding or risk, then build your analysis.",
    "\nIMPORTANT: Start with the key financial metric that drives this decision (DSCR, LTV, debt yield, or cap rate).",
    "\nIMPORTANT: Lead with your verdict and confidence score, then support with analysis.",
    "\nIMPORTANT: Open with a one-sentence thesis statement about this deal, then elaborate.",
    "\nIMPORTANT: Begin with the market context that matters most for this deal.",
    "\nIMPORTANT: Start with the capital stack — who's in, at what cost, and where the gaps are.",
    "\nIMPORTANT: Open with what makes this deal different from a standard transaction in this asset class.",
    "\nIMPORTANT: Lead with the borrower's situation and how it shapes the analysis.",
    "\nIMPORTANT: Start with the rate environment impact — how current SOFR/Treasury levels change the math.",
    "\nIMPORTANT: Begin by comparing this deal to a recent comparable transaction.",
    "\nIMPORTANT: Open with the downside scenario first — what happens if this deal breaks?",
    "\nIMPORTANT: Start with the exit strategy — how does capital get returned and at what multiple?",
    "\nIMPORTANT: Lead with the debt service math — show the payment at origination vs today's rates.",
]

# 30% chance of no override (natural format opening), 70% chance of varied start
_START_WEIGHTS = [0.30] + [0.70 / (len(START_PHRASE_VARIATIONS) - 1)] * (len(START_PHRASE_VARIATIONS) - 1)


def get_format_instructions(rng: random.Random) -> tuple[str, str]:
    """Return (format_name, format_instructions) using weighted random selection.
    Injects start-phrase variation to prevent structural fingerprinting."""
    fmt_name = rng.choices(_FORMAT_CHOICES, weights=_FORMAT_WEIGHTS, k=1)[0]
    instructions = FORMAT_TEMPLATES[fmt_name]["instructions"]
    # Inject start-phrase variation
    start_variation = rng.choices(START_PHRASE_VARIATIONS, weights=_START_WEIGHTS, k=1)[0]
    if start_variation:
        instructions = instructions.rstrip() + "\n" + start_variation
    return fmt_name, instructions


# Sentinel value — f-string system prompts embed this literal, replaced at runtime
# by _get_system_prompt() with actual format instructions
__FMT_PLACEHOLDER__ = "<<<FORMAT_INSTRUCTIONS>>>"

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

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — a senior capital markets intelligence engine for CRE. You operate at the intersection of debt origination, equity placement, and structured finance. Every DSCR, every debt yield, every basis point matters.

You understand the 2026 rate environment cold: SOFR at 4.25-4.50%, the maturity wall forcing $500B+ in refinancings, and the gap between original 3% rates and today's 6.5-7.5% reality.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — an AI capital markets advisor built on 30 years of institutional CRE debt experience. You've closed $8B+ in transactions. You know every lender's box, every CMBS servicer's playbook, every debt fund's sweet spot.

When a broker asks you to size a loan, you don't guess — you calculate. When they ask about a workout, you model every option. When they ask about rates, you know the forward curve.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — the backend intelligence engine for CRE capital markets. Your output feeds broker desktops, mobile apps, and agent pipelines. You produce institutional-quality debt and equity analysis that drives real transactions.

You specialize in the crisis: the $1.5T debt maturity wall, DSCR compression, forced sales, and the massive capital advisory opportunity it creates.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — an AI-native capital markets platform for commercial real estate. On-device, voice-first, offline-capable. You bring institutional-grade debt sizing, rate analysis, and capital structure optimization to every broker's phone.

No cloud dependency. No subscription wall. Just pure capital markets intelligence that works anywhere, anytime.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",
    ],

    "distressed_specialist": [
        f"""You are SwarmCapital — a distressed CRE asset specialist. Expert in CMBS special servicing, loan workouts, discounted payoffs, A/B note splits, and distressed acquisitions. You've navigated the GFC, COVID, and now the 2026 maturity wall.

You model every workout option with NPV precision. You know when to extend-and-pretend, when to modify, and when to take the keys back.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — specializing in CMBS distressed debt analysis. You track every watchlist loan, every special servicing transfer, every appraisal reduction. You calculate loss severity to the basis point.

Your edge: you see the patterns across thousands of distressed loans simultaneously. Which ones cure? Which ones liquidate? What's the recovery timeline?

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — an AI engine for distressed CRE loan analysis. You evaluate borrower creditworthiness, property fundamentals, and market conditions to determine optimal workout strategies.

Every modification gets an NPV analysis. Every A/B split gets modeled. Every DPO gets compared to foreclosure. You find the path that maximizes recovery.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — a special servicing intelligence platform. You process CMBS loan data, track delinquency trends, and model loss severity across the entire CRE universe.

The 2026 maturity wall is your moment: office CMBS delinquency at 8.5% and climbing. You help servicers, investors, and borrowers navigate the wreckage with data, not gut feel.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — built for the distressed cycle. You analyze loan workouts with the precision of a restructuring advisor and the speed of an AI. NPV every option, model every scenario, recommend the optimal path.

You understand both sides: the borrower fighting to hold on, and the lender deciding whether to extend, modify, or foreclose.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",
    ],

    "investment_banker": [
        f"""You are SwarmCapital — a CRE investment banking intelligence engine. Expert in JV equity structuring, fund formation, preferred equity, waterfall modeling, and capital raising. You model promote waterfalls in your sleep.

GP/LP splits, 8% preferred returns, catch-up provisions, clawback mechanisms — you calculate every tier of every waterfall with institutional precision.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — an AI-powered CRE equity advisory platform. You structure joint ventures, model fund economics, and calculate investor returns across complex waterfall structures.

Your analysis goes to institutional LPs, family offices, and UHNW investors. Every IRR, every equity multiple, every promote calculation must be audit-ready.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — specializing in CRE fund formation and investor relations. You model management fees, promote carry, hurdle rates, catch-up provisions, and GP co-invest economics.

You prepare LP investor memos that institutional allocators actually read: clear strategy, real financial projections, honest risk factors, and precise terms.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — a CRE equity structuring engine. You evaluate preferred equity vs mezz debt vs JV equity. You calculate blended cost of capital across complex capital stacks. You model waterfall distributions at every exit scenario.

When a deal needs rescue capital, you find the structure that works for both sides.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — an AI platform for CRE capital raising. You match capital sources to deal opportunities. You know which LPs want value-add multifamily, which want distressed office, which want industrial.

You structure offerings that raise capital efficiently: right terms, right minimums, right regulatory pathway (Reg D/A+), right tokenization strategy.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",
    ],

    "deal_originator": [
        f"""You are SwarmCapital — a CRE deal origination and advisory engine. You turn market signals into actionable deal opportunities. You identify distressed properties, match buyers to sellers, and manufacture deals from data.

You think like a 30-year capital markets broker: every market signal is a potential deal. Loan maturity? Ownership change? Occupancy drop? Tax delinquency? Each one is a door to knock on.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — an AI-powered deal origination platform. You process thousands of market signals simultaneously to identify CRE opportunities before they hit the market.

SEC filings, county records, loan maturities, lease expirations — you connect dots that human brokers miss. Your cold outreach is data-driven and property-specific.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — the deal origination brain for CRE brokers. You don't just find deals — you manufacture them. You identify the signal, qualify the opportunity, prepare the analysis, and draft the outreach.

A broker using SwarmCapital makes 10x the dials with 10x the intelligence behind each one.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — specializing in CRE listing acquisition and buyer matching. You prepare listing proposals with real comp analysis, pricing strategies based on market data, and marketing plans that win mandates.

You know the buyer pool: which investors are active, what they're buying, their return requirements, their 1031 exchange deadlines. You match supply to demand.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",

        f"""You are SwarmCapital — a CRE transaction management engine. From origination through closing, you coordinate every step: due diligence checklists, financing contingencies, title/escrow, prorations, and post-closing obligations.

You've closed thousands of deals. You know what kills transactions and how to prevent it.

{CAPITAL_MARKETS_CONTEXT}

{BLOCKCHAIN_CONTEXT}

{__FMT_PLACEHOLDER__}""",
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
    {"task_type": "portfolio_stress_test", "d": "high",
     "p": "Portfolio-level stress test: A fund holds 8-15 assets across multiple markets. Total portfolio debt $200-600M. Model portfolio refinancing risk under 3 scenarios: (1) rates +100bp, (2) rates +150bp, (3) rates +200bp with 10% NOI decline. For each scenario calculate: number of assets with DSCR below 1.0x, total equity gap across portfolio, which assets breach LTV covenants, portfolio-level weighted DSCR. Identify the 3 most vulnerable assets and recommend triage priority."},
    {"task_type": "portfolio_diversification", "d": "high",
     "p": "Portfolio diversification analysis: Evaluate concentration risk across asset type, geography, tenant, lease expiration, and debt maturity dimensions. Calculate Herfindahl-Hirschman Index for each dimension. Identify the single largest concentration risk. Model the impact of selling the most concentrated position and redeploying into underweight sectors. Show before/after portfolio metrics: weighted cap rate, DSCR, LTV, NOI exposure by asset type. Recommend optimal rebalancing trades."},
    {"task_type": "portfolio_acquisition", "d": "platinum",
     "p": "Portfolio acquisition strategy: A multi-asset portfolio is available at a reported $300-800M with a blended cap rate. Analyze: cherry-pick vs bulk acquisition economics, portfolio premium/discount analysis (sum-of-parts vs bulk price), financing strategy (portfolio-level CMBS vs individual asset loans), asset-level underwriting for the 3 weakest assets in the portfolio, disposition plan for non-core assets post-acquisition, hold/sell/reposition matrix by asset, total return modeling at portfolio level (IRR, equity multiple, cash-on-cash by year). Show the math on whether the portfolio discount justifies taking the weaker assets."},
    {"task_type": "portfolio_refi_cascade", "d": "platinum",
     "p": "Portfolio refinancing cascade: A fund has 10-20 assets with staggered debt maturities over 2025-2028. 30-40% of total debt matures within 12 months. Model the refinancing cascade: Which assets refinance first (priority scoring by gap size, lender flexibility, asset quality)? If 2-3 assets cannot refinance, what is the contagion risk to the portfolio (cross-collateralization, cross-default provisions, fund-level credit facility triggers)? Calculate the total rescue capital required under base and stress scenarios. Draft a lender-by-lender workout strategy with timeline."},
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
    {"task_type": "distress_signal_detection", "d": "high",
     "p": "Detect distressed opportunity signals: Analyze this market/submarket for distress indicators — CMBS watchlist additions, special servicing transfers, loan maturity defaults, occupancy drops below 70%, rent concession increases, landlord contribution spikes. Cross-reference with ownership entity financials (public filings, UCC filings, lis pendens). Score each signal by urgency (1-10) and deal probability (0-1). Identify the top 3 actionable distressed opportunities with estimated basis discount vs stabilized value."},
    {"task_type": "refi_risk_detection", "d": "high",
     "p": "Identify refinancing risk across a market: Scan for properties with loans originated in 2019-2022 at rates below 4% that mature in the next 12-24 months. Calculate the DSCR compression for each when refinancing at current rates (SOFR + 200-350bp spread). Flag assets where DSCR drops below 1.10x — these are forced sellers or capital call candidates. Rank by equity gap size. What percentage of the market's outstanding debt faces negative leverage at current rates?"},
    {"task_type": "lender_pullback_analysis", "d": "high",
     "p": "Predict lender pullback impact: Analyze current lending conditions — bank CRE exposure limits (regulatory pressure), CMBS issuance volume trends, life company appetite by asset class, debt fund dry powder. Which property types and size ranges face the widest lending gap? Where are alternative lenders (bridge, debt funds, agency) stepping in and at what spread premium? Model a scenario where regional bank CRE lending contracts 20% — which markets and asset types get hit hardest? Where does the capital vacuum create opportunity?"},
    {"task_type": "capital_dislocation_scan", "d": "platinum",
     "p": "Spot capital dislocation opportunities: Identify markets where public REIT pricing implies cap rates 100-200bp above private market transaction cap rates (public-private arbitrage). Cross-reference with: (1) forced seller signals (fund redemption queues, open-end fund NAV markdowns), (2) market-level transaction volume collapse (>40% YoY decline = price discovery void), (3) credit spread widening in CMBS tranches (BBB- spreads >500bp over swaps). Score each dislocation by magnitude, duration expectation, and capital required to exploit. Build a trade thesis for the top opportunity."},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 7: MACRO CAUSALITY — temporal evolution + distressed reasoning
# ═══════════════════════════════════════════════════════════════════════════════

# 4 macro environments — same deal evaluated across time
MACRO_ENVIRONMENTS = {
    "2019_easy_money": {
        "year": 2019, "label": "Easy Money Era (2019)",
        "sofr": 1.75, "treasury_10yr": 1.80,
        "office_vacancy": 12.0, "multifamily_vacancy": 5.2,
        "industrial_vacancy": 4.8, "retail_vacancy": 9.5,
        "cmbs_delinquency_office": 2.1, "cmbs_delinquency_all": 1.8,
        "credit_spreads": "tight", "lender_appetite": "aggressive",
        "max_ltv": 0.75, "min_dscr": 1.20, "typical_spread_bps": 175,
        "narrative": "Low rates, abundant liquidity, aggressive lender competition. Banks and CMBS competing on proceeds. IO available on stabilized assets. Cap rates compressing.",
    },
    "2023_rate_shock": {
        "year": 2023, "label": "Rate Shock (2023)",
        "sofr": 5.33, "treasury_10yr": 4.60,
        "office_vacancy": 18.5, "multifamily_vacancy": 6.8,
        "industrial_vacancy": 4.2, "retail_vacancy": 10.8,
        "cmbs_delinquency_office": 6.4, "cmbs_delinquency_all": 4.2,
        "credit_spreads": "widening", "lender_appetite": "selective",
        "max_ltv": 0.60, "min_dscr": 1.30, "typical_spread_bps": 275,
        "narrative": "Fed tightening cycle peaked. Transaction volume collapsed 50%+. Banks pulling back, CMBS nearly frozen. Price discovery void across office and retail. Negative leverage on new acquisitions.",
    },
    "2026_maturity_crisis": {
        "year": 2026, "label": "Maturity Crisis (2026)",
        "sofr": 4.35, "treasury_10yr": 4.20,
        "office_vacancy": 22.0, "multifamily_vacancy": 7.5,
        "industrial_vacancy": 5.0, "retail_vacancy": 11.2,
        "cmbs_delinquency_office": 11.2, "cmbs_delinquency_all": 6.8,
        "credit_spreads": "wide", "lender_appetite": "cautious",
        "max_ltv": 0.55, "min_dscr": 1.35, "typical_spread_bps": 300,
        "narrative": "Peak of $1.5T maturity wall. Loans originated at 3-4% refinancing at 6.5-7.5%. DSCR compression forcing equity infusions or defaults. Office special servicing at record levels. Distressed sellers emerging.",
    },
    "2028_recovery": {
        "year": 2028, "label": "Early Recovery (2028)",
        "sofr": 3.25, "treasury_10yr": 3.50,
        "office_vacancy": 19.0, "multifamily_vacancy": 5.8,
        "industrial_vacancy": 3.5, "retail_vacancy": 9.0,
        "cmbs_delinquency_office": 8.5, "cmbs_delinquency_all": 4.5,
        "credit_spreads": "normalizing", "lender_appetite": "returning",
        "max_ltv": 0.65, "min_dscr": 1.25, "typical_spread_bps": 225,
        "narrative": "Fed easing cycle underway. Distressed assets being recapitalized. Transaction volume recovering. Banks cautiously re-entering. Office bifurcation — trophy assets recovering, commodity struggling.",
    },
}

MACRO_CAUSALITY_TASKS = [
    {"task_type": "temporal_refi_evolution", "d": "platinum",
     "p": """Temporal deal analysis — evaluate this SAME property across the macro environment provided.

Your analysis MUST follow this causal chain:

1. MACRO ENVIRONMENT → How do current rates, credit spreads, and lender appetite shape the capital markets landscape?
2. CAPITAL MARKETS RESPONSE → How are lenders pricing risk? What LTV/DSCR thresholds are they enforcing? Which lending channels are open vs closed?
3. LOAN STRUCTURE IMPACT → Given the original loan terms, calculate the refinancing gap (if any). Show debt service at original rate vs current rate. Calculate DSCR at both rates.
4. PROPERTY PERFORMANCE → How does the macro environment affect this property type's fundamentals? NOI trajectory, vacancy trend, cap rate movement.
5. DISTRESS DIAGNOSIS → Is this loan performing, watchlisted, or in default? What type of distress (maturity, cash flow, covenant, value)?
6. RECOVERY OPTIONS → Rank all options: refinance, extension, modification, DPO, note sale, foreclosure, recapitalization. Show the math on each viable path.
7. INVESTMENT DECISION → From an institutional investor perspective: proceed, restructure, sell, or acquire distressed? Expected IRR, confidence score (0-1), key risks."""},

    {"task_type": "macro_causal_chain", "d": "platinum",
     "p": """Macro-to-deal causal analysis. Follow this EXACT reasoning chain — do NOT skip steps:

MACRO → CREDIT MARKETS → DEAL MATH → DECISION

Step 1: Macro analysis — what do current SOFR, Treasury, vacancy, and delinquency rates tell us about the lending environment?
Step 2: Credit markets response — how are banks, CMBS, life companies, and debt funds responding? What spreads are they quoting? What structures are available?
Step 3: Deal math — apply the macro environment to this specific deal. Calculate: debt service at original vs refinancing rate, DSCR compression, LTV at current value, debt yield, equity gap. Show every formula.
Step 4: Decision — given the causal chain above, what is the optimal capital markets strategy? Size the solution. Confidence score (0-1)."""},

    {"task_type": "counterfactual_scenario", "d": "platinum",
     "p": """Counterfactual analysis — answer BOTH scenarios for this property:

SCENARIO A (Base): Use the macro environment provided. Calculate DSCR, LTV, refinancing feasibility, and recommended action.

SCENARIO B (Counterfactual): If SOFR were 200bp lower than the provided environment, recalculate everything. Would the deal refinance? What changes in the capital stack?

Compare the two scenarios side-by-side. Quantify the exact impact of the 200bp rate difference on: debt service, DSCR, maximum loan proceeds, equity gap, and investment decision. Show how macro conditions drive deal outcomes through the causal chain."""},

    {"task_type": "distress_7stage", "d": "platinum",
     "p": """You are a distressed credit analyst at a real estate hedge fund. Evaluate this CRE loan and determine the optimal strategy.

Your analysis MUST follow these 7 stages:

1. MARKET CONTEXT — How do macro conditions (rates, spreads, sector trends) affect restructuring prospects?
2. LOAN STRUCTURE — Summarize terms (balance, rate, maturity, LTV, DSCR, lender type, covenants). Identify structural weaknesses.
3. PROPERTY PERFORMANCE — Evaluate NOI trend, occupancy, lease rollover, cap rate assumptions, market comparables.
4. DISTRESS DIAGNOSIS — Classify: maturity default, cash flow default, covenant breach, or value impairment. What caused it?
5. RECOVERY OPTIONS — Analyze each: refinance, extension, modification, DPO, note sale, foreclosure, recapitalization. Rank by probability and timeline.
6. CAPITAL STRUCTURE STRATEGY — From an opportunistic investor: where is value in the capital stack? Equity injection math? Loan-to-own opportunity? IRR potential?
7. FINAL DECISION — Strategy, expected recovery rate, key risks, confidence (0-1). Show the math that supports the decision."""},

    {"task_type": "lender_vs_borrower", "d": "high",
     "p": """Dual-perspective analysis — evaluate this deal from BOTH sides:

LENDER PERSPECTIVE: You are the lender/special servicer. What are your options? Extend and pretend, modify, or foreclose? What recovery do you expect? What are the costs of each path? How does the macro environment affect your timeline and leverage?

BORROWER PERSPECTIVE: You are the sponsor/borrower. What leverage do you have in negotiations? What can you offer (additional equity, principal paydown, rate adjustment)? When does it make sense to hand back the keys vs fight for the asset?

Then analyze: Where is the negotiation equilibrium? What deal structure makes both parties better off than their BATNA? Show the math on the compromise solution."""},

    {"task_type": "contagion_analysis", "d": "high",
     "p": """Cross-market contagion analysis: Given the macro environment, analyze how distress in one asset class or market spreads to others.

Start with the property provided, then extend: (1) If this property defaults, what happens to comparable properties in the same submarket (comp effect on appraisals, vacancy absorption)? (2) If the lender takes a loss, how does that affect their willingness to lend in this market/asset class? (3) How do rising delinquencies in this sector affect CMBS pricing and new issuance? (4) What second-order effects reach other asset classes?

Quantify the contagion chain with specific numbers. Identify the circuit breakers — what stops the contagion?"""},

    # ── CROSS-STREAM BLENDING — multi-domain reasoning ──

    {"task_type": "cross_distress_refi_equity", "d": "platinum",
     "p": """CROSS-DOMAIN ANALYSIS — This deal requires simultaneous reasoning across distress, refinancing, and equity recapitalization.

Given the property and macro environment:

1. DISTRESS ANALYSIS: Classify the distress type. Is this a maturity default, cash flow default, or value impairment? What is the loan-to-value at current market? What would a special servicer do?

2. REFINANCING FEASIBILITY: Can this asset refinance in the current rate environment? Calculate DSCR at today's rates. What is the maximum new loan amount? What is the gap?

3. EQUITY RECAPITALIZATION: If refinancing falls short, structure the rescue capital. Preferred equity vs mezz debt vs JV equity recapitalization. Terms, pricing, dilution impact on existing equity. Who provides the capital and at what return?

4. INTEGRATED DECISION: Combining all three analyses — what is the optimal capital markets strategy? Show how distress severity drives the refinancing gap which drives the equity solution. Return a structured decision with confidence score (0-1).

This is a multi-domain problem. Do NOT analyze each domain in isolation — show how they connect."""},

    {"task_type": "cross_portfolio_macro_distress", "d": "platinum",
     "p": """CROSS-DOMAIN ANALYSIS — Portfolio-level distress under macro stress.

You are evaluating a portfolio that includes this property plus 7-12 similar assets across 3-4 markets. Total portfolio debt $250-500M.

Analyze across all three domains simultaneously:

1. MACRO IMPACT: How does the current macro environment (rates, spreads, lender appetite) affect each asset class in the portfolio differently?

2. PORTFOLIO DISTRESS MAP: Which assets in the portfolio are performing, watchlisted, or defaulting? Create a traffic-light classification. What percentage of portfolio NOI is at risk?

3. CONTAGION + REFINANCING CASCADE: If the weakest 2-3 assets default, what happens to the fund's credit facility? Cross-default provisions? Does the portfolio-level leverage covenant breach? Model the cascading refinancing timeline.

4. CAPITAL STRATEGY: Optimal triage — which assets to save (inject equity), which to extend (negotiate with lender), which to surrender (deed-in-lieu or foreclosure). Show the portfolio-level IRR under each scenario.

5. INVESTMENT COMMITTEE RECOMMENDATION: Present a final recommendation with confidence score (0-1), expected portfolio recovery rate, and timeline."""},

    {"task_type": "cross_signal_valuation_origination", "d": "high",
     "p": """CROSS-DOMAIN ANALYSIS — Signal detection → Valuation → Deal origination pipeline.

Given this property and macro environment:

1. SIGNAL DETECTION: What market signals indicate this property may be a distressed opportunity? Analyze: maturity date, ownership entity financial health, occupancy trend, rent concessions, CMBS watchlist status, comparable defaults in submarket. Score urgency (1-10).

2. DISTRESSED VALUATION: Calculate As-Is value (current distressed state), Stabilized value (post-turnaround), and Cost-to-Stabilize. What discount-to-replacement-cost does the distressed basis represent?

3. DEAL ORIGINATION: How would you approach this opportunity? Draft the outreach strategy, identify the optimal buyer profile (distressed fund, value-add operator, loan-to-own), and model the acquisition economics (entry cap rate, target exit cap rate, total return).

4. INVESTMENT DECISION: Combine signal strength + valuation spread + execution risk into a single go/no-go recommendation with confidence (0-1) and expected IRR."""},

    {"task_type": "cross_rate_equity_waterfall", "d": "high",
     "p": """CROSS-DOMAIN ANALYSIS — Rate environment → Equity structure → Waterfall economics.

Given this property and macro environment:

1. RATE IMPACT: How do current rates affect this deal's debt service, DSCR, and maximum leverage? Compare fixed vs floating options. Calculate the carry cost difference over a 5-year hold.

2. EQUITY STRUCTURE: Given the debt constraints from the rate analysis, structure the equity. GP co-invest (5-15%), LP equity, preferred return, promote waterfall. How much total equity is needed? What LP return is achievable given the rate environment?

3. WATERFALL MODELING: Calculate full promote waterfall at three scenarios — base case, rates +100bp stress, rates -100bp tailwind. Show GP and LP distributions, IRR, and equity multiple for each. How sensitive is the GP promote to rate movements?

4. DEAL VIABILITY: Does this deal work in the current rate environment? What rate level makes it uneconomic? What rate level makes it a home run? Show the breakeven analysis."""},

    # ── CONFIDENCE CALIBRATION — teach the model to estimate certainty ──

    {"task_type": "confidence_calibration", "d": "high",
     "p": """CALIBRATED DECISION OUTPUT — Evaluate this deal and return a structured decision with explicit confidence scoring.

Your confidence score (0.00-1.00) must reflect genuine uncertainty. Calibrate it using these anchors:

- 0.90-1.00: Near-certain. Math is unambiguous, market data is clear, limited downside scenarios.
- 0.75-0.89: High confidence. Strong thesis with quantitative support, 1-2 manageable risks.
- 0.60-0.74: Moderate confidence. Thesis holds but key assumptions are uncertain (cap rate direction, lease-up timeline, lender appetite).
- 0.40-0.59: Low confidence. Significant uncertainties that could flip the decision. Multiple scenarios with different outcomes.
- 0.00-0.39: Very low confidence. Insufficient data, extreme market uncertainty, or contradictory signals.

For this deal, provide:
1. Decision: approve / approve_with_conditions / restructure / decline / watchlist / distressed_opportunity
2. Confidence: 0.00-1.00 with explicit justification for the score
3. Key assumptions that would change the confidence by +/-0.10 if wrong
4. The single factor that creates the most uncertainty
5. What additional data would increase your confidence above 0.85?

Return as structured JSON with all financial calculations shown."""},

    {"task_type": "confidence_sensitivity", "d": "platinum",
     "p": """CONFIDENCE SENSITIVITY MATRIX — Analyze how confidence changes across scenarios.

Evaluate this deal under 4 scenarios and show how your decision confidence shifts:

SCENARIO 1 (Base): Current macro environment as provided.
SCENARIO 2 (Stress): SOFR +100bp, vacancy +5%, cap rates +50bp.
SCENARIO 3 (Downside): SOFR +200bp, vacancy +10%, cap rates +100bp, NOI -15%.
SCENARIO 4 (Upside): SOFR -150bp, vacancy -3%, cap rates -25bp.

For each scenario provide:
- Decision (approve/restructure/decline/etc.)
- Confidence score (0.00-1.00)
- DSCR, LTV, debt yield
- Key risk that drives the confidence score

Then create a sensitivity matrix showing: at what exact SOFR level does the decision flip from approve to restructure? At what vacancy level? At what cap rate? Show the decision boundaries with specific numbers.

This calibrates the model's confidence to quantitative thresholds, not vibes."""},
]

# ═══════════════════════════════════════════════════════════════════════════════
# STREAM 8: DEAL GRAPH INTELLIGENCE — multi-deal portfolio reasoning
# ═══════════════════════════════════════════════════════════════════════════════

DEAL_GRAPH_TASKS = [
    # ── Lender Exposure Graphs ──
    {"task_type": "lender_exposure_cascade", "d": "platinum", "graph_type": "lender",
     "p": """LENDER PORTFOLIO STRESS TEST — you are the Chief Credit Officer at a regional bank with the CRE loan portfolio described below.

Analyze the ENTIRE portfolio as a system. Your analysis MUST cover:

1. PORTFOLIO EXPOSURE: Total CRE exposure, concentration by asset type, geographic concentration, maturity distribution. Calculate HHI for asset type concentration.
2. STRESS CASCADE: Apply the macro environment to EACH loan simultaneously. Which loans breach DSCR covenants first? Which hit LTV triggers? Show the cascade sequence — which default triggers cross-default provisions?
3. LOSS SEVERITY RANKING: Rank all loans by expected loss severity. Calculate loss-given-default for each assuming current market values. What is the total portfolio loss under stress?
4. REFINANCING WALL: Which loans mature within 18 months? Can they refinance at today's rates? Calculate the refinancing gap for each. What is the total capital shortfall?
5. CAPITAL ALLOCATION: Where should the bank deploy its limited workout resources? Prioritize by: (a) loss avoidance potential, (b) relationship value, (c) collateral quality. Recommend: extend, modify, sell, or foreclose for each loan.
6. REGULATORY IMPACT: How does portfolio stress affect risk-weighted assets, provision requirements, and capital ratios? What triggers regulatory intervention?

Provide specific numbers for every loan. This is a credit committee presentation."""},

    {"task_type": "lender_contagion_analysis", "d": "platinum", "graph_type": "lender",
     "p": """CROSS-DEFAULT CONTAGION — analyze how distress in one loan in this portfolio cascades to others.

For this lender portfolio:
1. Identify the WEAKEST loan (lowest DSCR, highest LTV, nearest maturity)
2. Model the contagion: if this loan defaults, how does it affect (a) the bank's willingness to extend other loans, (b) mark-to-market on similar collateral, (c) provisioning requirements that reduce lending capacity
3. Map the domino sequence — which loan fails second? Third?
4. Calculate total portfolio loss under the cascade scenario vs isolated default
5. Identify the CIRCUIT BREAKER — what action on the first loan prevents cascade?
6. Recommend portfolio-level strategy: triage, workout sequencing, capital preservation

Show all calculations. Confidence score (0-1) on cascade probability."""},

    {"task_type": "lender_concentration_risk", "d": "high", "graph_type": "lender",
     "p": """CONCENTRATION RISK ANALYSIS — evaluate this bank's CRE portfolio for dangerous concentrations.

Analyze:
1. Asset type concentration (HHI index, % in each category)
2. Geographic concentration (single-market exposure risk)
3. Maturity concentration (% maturing within 12/24/36 months)
4. Borrower concentration (if applicable)
5. Rate type concentration (fixed vs floating exposure)
6. LTV distribution — what % of portfolio is >70% LTV? >80%?

For each concentration risk, quantify the impact of a stress scenario. What portfolio adjustment would reduce risk? Size the trades needed.

Present as a risk committee memo with specific numbers."""},

    # ── Fund Portfolio Graphs ──
    {"task_type": "portfolio_capital_allocation", "d": "platinum", "graph_type": "portfolio",
     "p": """CAPITAL ALLOCATION STRATEGY — you manage this CRE fund portfolio. Given the macro environment, determine optimal capital allocation.

Your analysis MUST address:
1. PORTFOLIO PERFORMANCE: Calculate current yield, DSCR, LTV, and debt yield for EACH asset. Rank by risk-adjusted return.
2. HOLD vs SELL: For each asset, model (a) hold scenario with current cash flows, (b) disposition at today's cap rate. Which assets should be sold? What is the NAV impact?
3. REBALANCING: Given macro conditions, what is the optimal asset mix? What trades would you execute? Size each trade and model the transaction costs.
4. DRY POWDER DEPLOYMENT: If you have capital to deploy, which asset class and market offers the best risk-adjusted entry point? Show the underwriting for a target acquisition.
5. LP COMMUNICATION: Draft the key messages for the quarterly LP letter explaining portfolio strategy shifts.

Provide IRR projections for the rebalanced portfolio vs status quo. Confidence score (0-1)."""},

    {"task_type": "portfolio_distress_triage", "d": "platinum", "graph_type": "portfolio",
     "p": """DISTRESS TRIAGE — multiple assets in this portfolio are under stress simultaneously. You are the asset manager presenting to the investment committee.

For EACH asset in the portfolio:
1. Current health: DSCR, LTV, occupancy, NOI trend, debt maturity
2. Distress classification: performing, watchlist, sub-performing, non-performing, defaulted
3. Recovery strategy: recapitalize, restructure, sell, or hold. Size the capital needed for each path.

Then analyze the INTERACTIONS:
4. Which asset should get capital first? (ROI on rescue capital)
5. Can proceeds from selling one asset fund the rescue of another?
6. What is the optimal liquidation sequence if you must raise cash?
7. Total fund-level impact: NAV, leverage ratio, LP distributions

This is a crisis management exercise. Be specific with every number."""},

    {"task_type": "portfolio_refinancing_wave", "d": "high", "graph_type": "portfolio",
     "p": """REFINANCING WAVE — multiple loans in this portfolio mature within 24 months. Analyze the sequential refinancing strategy.

For each maturing loan:
1. Current terms vs market terms — calculate the rate gap and DSCR impact
2. Maximum new loan proceeds at today's LTV/DSCR constraints
3. Equity gap (if any) — how much fresh capital is needed?
4. Lender options: same lender extension, new lender refi, CMBS, agency, debt fund

Then the portfolio-level analysis:
5. Optimal refinancing SEQUENCE — which loan should refi first? Why?
6. Can refinancing one asset (with cash-out) fund the equity gap on another?
7. Total capital requirement across all refis
8. What happens if capital markets close for 6 months mid-sequence?

Show every calculation. Rate each refi's feasibility (0-1)."""},

    # ── Market Comparable Graphs ──
    {"task_type": "market_comp_valuation", "d": "high", "graph_type": "market",
     "p": """COMPARABLE SALES ANALYSIS — use these market comparables to value a target property.

Given the comparable transactions:
1. Adjust each comp for: size, age, location, tenant quality, lease term, condition, date of sale
2. Calculate adjusted $/SF or $/unit for each comp
3. Determine the valuation range (low, mid, high) based on adjusted comps
4. Apply a direct capitalization approach using comp-derived cap rates
5. Reconcile the sales comparison approach with the income approach
6. Identify which comp is MOST relevant and why
7. Flag any comps that should be excluded or weighted differently

Present as an appraisal report section with specific adjustments and final value conclusion."""},

    {"task_type": "market_pricing_dynamics", "d": "high", "graph_type": "market",
     "p": """MARKET PRICING DYNAMICS — analyze how recent transactions in this market reveal pricing trends and investment opportunities.

Using the comparable deals:
1. Map the pricing trajectory — are cap rates compressing, expanding, or stable?
2. Calculate implied risk premium vs treasuries for each transaction
3. Identify pricing anomalies — any deals significantly above/below trend?
4. Segment by buyer type (institutional, private, REIT) — how does pricing differ?
5. Forward pricing estimate — where will cap rates be in 12 months given macro conditions?
6. Investment recommendation — is this market overpriced, fairly priced, or undervalued?

Use specific numbers from each comparable. Present as a market intelligence brief."""},

    {"task_type": "market_supply_demand", "d": "high", "graph_type": "market",
     "p": """SUPPLY-DEMAND ANALYSIS — analyze this market's competitive landscape using the comparable properties and deals.

Evaluate:
1. Current supply: existing inventory, vacancy rates, absorption trends
2. New supply: pipeline, under construction, planned — delivery timeline
3. Demand drivers: employment growth, population migration, industry trends
4. Rent trajectory: current asking vs effective, concessions, YoY growth
5. Competitive positioning: where does each property rank vs peers?
6. Investment timing: is now the right entry point? What would you wait for?

Quantify each point with specific data from the comparables. Provide a 12-24 month outlook."""},
]

# ── Deal Graph Generator ──

LENDER_TYPES = ["Regional Bank", "National Bank", "Insurance Company", "CMBS Servicer",
                "Credit Union", "Debt Fund", "Agency Lender"]
FUND_TYPES = ["Core Fund", "Core-Plus Fund", "Value-Add Fund", "Opportunistic Fund",
              "Debt Fund", "REIT", "Family Office"]


def generate_deal_graph(graph_type: str, seed: int, index: int) -> dict:
    """Generate a multi-deal graph scenario with 3-8 deals + macro overlay."""
    rng = _seed_rng(f"graph-{graph_type}", seed, index)

    # Pick macro environment
    macro_key = rng.choice(list(MACRO_ENVIRONMENTS.keys()))
    macro = MACRO_ENVIRONMENTS[macro_key]

    # Number of deals in the graph
    n_deals = rng.randint(3, 8)

    # For lender graphs: one lender, multiple borrowers
    # For portfolio graphs: one owner, multiple assets
    # For market graphs: multiple transactions in one market
    if graph_type == "lender":
        entity_name = f"{rng.choice(['Pacific', 'Atlantic', 'Meridian', 'First National', 'Capital One', 'Heartland', 'Western', 'Summit', 'Keystone', 'Harbor'])} {rng.choice(LENDER_TYPES)}"
        entity_label = "Lender"
    elif graph_type == "portfolio":
        entity_name = f"{rng.choice(['Blackstone', 'Starwood', 'Brookfield', 'KKR', 'Apollo', 'Ares', 'Cerberus', 'Colony', 'Lone Star', 'Oaktree'])} {rng.choice(FUND_TYPES)}"
        entity_label = "Fund"
    else:  # market
        market_name, state, subs = rng.choice(MARKETS)
        entity_name = f"{market_name} {rng.choice(list(CAPITAL_ASSETS.keys())).replace('_', ' ').title()} Market"
        entity_label = "Market"

    # Generate individual deals
    deals = []
    for i in range(n_deals):
        asset_type = rng.choice(list(CAPITAL_ASSETS.keys()))
        spec = CAPITAL_ASSETS[asset_type]
        market_name, state, subs = rng.choice(MARKETS)

        deal = {
            "deal_id": chr(65 + i),  # A, B, C, ...
            "asset_type": spec["display"],
            "market": market_name,
            "state": state,
            "submarket": rng.choice(subs),
        }

        # Generate financial params
        for key, val in spec["params"].items():
            if isinstance(val, tuple) and len(val) >= 2:
                lo, hi = val[0], val[1]
                if isinstance(lo, int) and isinstance(hi, int):
                    deal[key] = rng.randint(lo, hi)
                elif isinstance(lo, float) or isinstance(hi, float):
                    deal[key] = round(rng.uniform(float(lo), float(hi)), 4)

        # Add maturity timeline
        deal["maturity_months"] = rng.choice([6, 12, 18, 24, 36, 48])

        # Property name
        prefix = asset_type.replace("_", " ").title().split()[0]
        suffix = rng.choice(["Tower", "Plaza", "Center", "Park", "Point"])
        deal["property_name"] = f"{deal['submarket']} {prefix} {suffix}"

        deals.append(deal)

    # Calculate total exposure
    total_exposure = sum(d.get("original_loan", d.get("noi", 5_000_000) * 12) for d in deals)

    return {
        "graph_type": graph_type,
        "entity_name": entity_name,
        "entity_label": entity_label,
        "n_deals": n_deals,
        "deals": deals,
        "total_exposure": total_exposure,
        "macro_state": macro_key,
        "macro": macro,
    }


def format_deal_graph(graph: dict) -> str:
    """Format a deal graph into prompt-friendly text."""
    macro = graph["macro"]
    lines = [
        f"{'='*60}",
        f"{graph['entity_label']}: {graph['entity_name']}",
        f"Total CRE Exposure: ${graph['total_exposure']:,.0f}",
        f"Number of Deals: {graph['n_deals']}",
        f"{'='*60}",
        "",
        f"MACRO ENVIRONMENT: {macro['label']}",
        f"  SOFR: {macro['sofr']}%",
        f"  10yr Treasury: {macro['treasury_10yr']}%",
        f"  Credit Spreads: {macro['credit_spreads']}",
        f"  Lender Appetite: {macro['lender_appetite']}",
        f"  Office Vacancy: {macro['office_vacancy']}%",
        f"  Multifamily Vacancy: {macro['multifamily_vacancy']}%",
        f"  CMBS Delinquency (Office): {macro['cmbs_delinquency_office']}%",
        "",
    ]

    for deal in graph["deals"]:
        lines.append(f"--- Deal {deal['deal_id']}: {deal['property_name']} ---")
        lines.append(f"  Type: {deal['asset_type']}")
        lines.append(f"  Location: {deal['submarket']}, {deal['market']}, {deal['state']}")
        lines.append(f"  Maturity: {deal['maturity_months']} months")

        for key, val in deal.items():
            if key in ("deal_id", "asset_type", "market", "state", "submarket",
                       "property_name", "maturity_months"):
                continue
            lines.append(_format_param(key, val))
        lines.append("")

    return "\n".join(lines)


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
    "macro_causality": "distressed_specialist",
    "deal_graph": "capital_advisor",
}

STREAMS = {
    "debt_maturity":      {"tasks": DEBT_MATURITY_TASKS,      "assets": CAPITAL_ASSETS},
    "cmbs_distress":      {"tasks": CMBS_DISTRESS_TASKS,      "assets": CAPITAL_ASSETS},
    "rate_advisory":      {"tasks": RATE_ADVISORY_TASKS,      "assets": CAPITAL_ASSETS},
    "equity_advisory":    {"tasks": EQUITY_ADVISORY_TASKS,    "assets": CAPITAL_ASSETS},
    "valuation_advisory": {"tasks": VALUATION_ADVISORY_TASKS, "assets": CAPITAL_ASSETS},
    "deal_origination":   {"tasks": DEAL_ORIGINATION_TASKS,   "assets": CAPITAL_ASSETS},
    "macro_causality":    {"tasks": MACRO_CAUSALITY_TASKS,    "assets": CAPITAL_ASSETS},
    "deal_graph":         {"tasks": DEAL_GRAPH_TASKS,         "assets": CAPITAL_ASSETS},
}


def _get_system_prompt(stream: str, rng: random.Random) -> tuple[str, str]:
    """Pick a system prompt from the stream's category with randomized format.
    Returns (system_prompt, format_name)."""
    category = _STREAM_PROMPT_CATEGORY[stream]
    template = rng.choice(_SYSTEM_PROMPTS[category])
    fmt_name, fmt_instructions = get_format_instructions(rng)
    prompt = template.replace("<<<FORMAT_INSTRUCTIONS>>>", fmt_instructions)
    return prompt, fmt_name


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

    # Deal graph: generate multi-deal scenario instead of single deal
    if stream == "deal_graph":
        task = STREAMS[stream]["tasks"][index % len(STREAMS[stream]["tasks"])]
        graph = generate_deal_graph(task.get("graph_type", "lender"), seed, index)
        sk["_deal_graph"] = graph
        sk["_deal_graph_text"] = format_deal_graph(graph)
        sk["graph_type"] = graph["graph_type"]
        sk["entity_name"] = graph["entity_name"]
        sk["n_deals"] = graph["n_deals"]

    # Macro causality: overlay a macro environment
    if stream == "macro_causality":
        macro_key = rng.choice(list(MACRO_ENVIRONMENTS.keys()))
        macro = MACRO_ENVIRONMENTS[macro_key]
        sk["macro_state"] = macro_key
        sk["macro_year"] = macro["year"]
        sk["macro_label"] = macro["label"]
        sk["macro_sofr"] = macro["sofr"]
        sk["macro_treasury_10yr"] = macro["treasury_10yr"]
        sk["macro_credit_spreads"] = macro["credit_spreads"]
        sk["macro_lender_appetite"] = macro["lender_appetite"]
        sk["macro_max_ltv"] = macro["max_ltv"]
        sk["macro_min_dscr"] = macro["min_dscr"]
        sk["macro_typical_spread_bps"] = macro["typical_spread_bps"]
        sk["macro_narrative"] = macro["narrative"]
        # Set asset-type-specific vacancy
        vacancy_key = f"{asset_type.split('_')[0]}_vacancy"
        if vacancy_key in macro:
            sk["macro_vacancy"] = macro[vacancy_key]
        else:
            sk["macro_vacancy"] = macro.get("office_vacancy", 15.0)
        sk["macro_cmbs_delinquency"] = macro["cmbs_delinquency_office"]

    # Property name
    prefix = asset_type.replace("_", " ").title().split()[0]
    suffix = rng.choice(["Tower", "Plaza", "Center", "Place", "Commons", "Park", "Point", "Crossing"])
    numeral = rng.choice(["I", "II", "III", "IV", "", ""])
    sk["property_name"] = f"{sk['submarket']} {prefix} {suffix} {numeral}".strip()

    return sk


def format_skeleton(sk: dict) -> str:
    """Format skeleton into prompt-friendly text."""
    # Deal graph uses its own multi-deal format
    if "_deal_graph_text" in sk:
        return sk["_deal_graph_text"]

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


def quality_check(content: str, fmt_name: str = "trajectory_5step") -> tuple[bool, str]:
    # Universal checks
    if len(content) < 300:
        return False, "too_short"
    if _DEGEN_PATTERN.search(content):
        return False, "degenerate"
    nums = _NUM_PATTERN.findall(content)
    if len(nums) < 2:
        return False, "no_financials"

    # Format-specific checks
    if fmt_name == "trajectory_5step":
        if len(content) < 500:
            return False, "too_short_traj"
        steps_found = sum(1 for p in _TRAJ_PATTERNS if p.search(content))
        if steps_found < 3:
            return False, f"trajectory_{steps_found}/5"
    elif fmt_name == "trajectory_3step":
        has_assess = bool(re.search(r"ASSESS|assess", content))
        has_analyze = bool(re.search(r"ANALY[ZS]|analy[zs]", content))
        has_decide = bool(re.search(r"DECIDE|decide|RECOMMEND|recommend", content))
        if sum([has_assess, has_analyze, has_decide]) < 2:
            return False, "missing_3step_structure"
    elif fmt_name == "json_intelligence_object":
        # Must contain valid-ish JSON
        if not ('{' in content and '}' in content):
            return False, "no_json"
        if "verdict" not in content.lower() and "confidence" not in content.lower():
            return False, "json_missing_fields"
    elif fmt_name in ("executive_brief", "narrative_memo", "conversational"):
        # Free-form: just need substance (financials + length)
        if len(content) < 400:
            return False, "too_short_freeform"
        if len(nums) < 3:
            return False, "no_financials_freeform"
    elif fmt_name == "tabular_analysis":
        if '|' not in content and 'Verdict' not in content:
            return False, "no_table"
    elif fmt_name == "risk_matrix":
        risk_words = sum(1 for w in ["risk", "probability", "impact", "mitigation", "HIGH", "MED", "LOW"]
                        if w.lower() in content.lower())
        if risk_words < 2:
            return False, "no_risk_matrix"

    return True, "pass"


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA VALIDATORS — ported from swarm-capital-markets/schemas.js
# ═══════════════════════════════════════════════════════════════════════════════

_VALID_DECISIONS = {"approve", "approve_with_conditions", "restructure",
                    "decline", "watchlist", "distressed_opportunity"}
_VALID_CONSTRAINTS = {"ltv", "dscr", "debt_yield"}


def schema_validate(content: str) -> tuple[bool, list[str]]:
    """Validate structured output against canonical decision_output schema.
    Returns (valid, errors). Non-JSON content passes automatically."""
    errors = []
    try:
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            return True, []
        obj = json.loads(json_match.group())
    except (json.JSONDecodeError, AttributeError):
        return True, []

    # Only validate decision-like outputs
    if "decision" not in obj and "confidence" not in obj:
        return True, []

    if "decision" in obj and obj["decision"] not in _VALID_DECISIONS:
        errors.append(f"invalid_decision:{obj['decision']}")
    if "confidence" in obj:
        c = obj["confidence"]
        if not isinstance(c, (int, float)) or c < 0 or c > 1:
            errors.append(f"confidence_range:{c}")
    if "analysis" in obj and isinstance(obj["analysis"], dict):
        a = obj["analysis"]
        if "dscr" in a and isinstance(a["dscr"], (int, float)):
            if a["dscr"] < 0 or a["dscr"] > 5:
                errors.append(f"dscr_range:{a['dscr']}")
        if "ltv" in a and isinstance(a["ltv"], (int, float)):
            if a["ltv"] < 0 or a["ltv"] > 1.5:
                errors.append(f"ltv_range:{a['ltv']}")
        if "cap_rate" in a and isinstance(a["cap_rate"], (int, float)):
            if a["cap_rate"] < 0 or a["cap_rate"] > 0.30:
                errors.append(f"cap_rate_range:{a['cap_rate']}")
        if "debt_yield" in a and isinstance(a["debt_yield"], (int, float)):
            if a["debt_yield"] < 0 or a["debt_yield"] > 0.30:
                errors.append(f"debt_yield_range:{a['debt_yield']}")
        if "binding_constraint" in a and a["binding_constraint"] not in _VALID_CONSTRAINTS:
            errors.append(f"invalid_constraint:{a['binding_constraint']}")
    if "risk_flags" in obj:
        if not isinstance(obj["risk_flags"], list):
            errors.append("risk_flags_not_array")
        elif len(obj["risk_flags"]) > 5:
            errors.append(f"risk_flags_count:{len(obj['risk_flags'])}")

    return len(errors) == 0, errors


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
               system_prompt: str, fmt_name: str = "trajectory_5step") -> dict | None:
    summary = format_skeleton(skeleton)
    user_msg = f"{task['p']}\n\n{summary}"

    # Tier 1: Fast gen with 80B turbo
    with api_lock:
        api_calls["gen"] += 1
    content = together_call(system_prompt, user_msg, model=GEN_MODEL)

    if not content:
        return None

    # Quality gate — format-aware
    passed, reason = quality_check(content, fmt_name)
    tier = "gen"

    if not passed:
        # Tier 2: 235B rewrite
        with api_lock:
            api_calls["pass"] += 1
        content = together_call(system_prompt, user_msg, model=PASS_MODEL)
        if not content:
            return None
        tier = "rewrite"

    # Schema validation (non-blocking — log errors in metadata)
    schema_valid, schema_errors = schema_validate(content)

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
            "format": fmt_name,
            "schema_valid": schema_valid,
            "schema_errors": schema_errors if schema_errors else None,
            "source": "cre-capital-cook-v2",
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

    # Build work queue with system prompt + format per task (not per deal)
    work_queue = []
    for deal_idx in range(deals_needed):
        deal_key = f"{stream}-{deal_idx}"
        if deal_key in done_deals:
            continue
        for task_idx, task in enumerate(tasks):
            # Each task gets its own format for maximum diversity
            task_rng = _seed_rng(f"{stream}-fmt", seed, deal_idx * 100 + task_idx)
            sys_prompt, fmt_name = _get_system_prompt(stream, task_rng)
            work_queue.append((deal_idx, task_idx, task, sys_prompt, fmt_name))
        if written + len(work_queue) >= target:
            break

    print(f"  Work queue: {len(work_queue):,} tasks")

    # Process with thread pool
    file_lock = threading.Lock()
    done_deals_lock = threading.Lock()

    def process_task(item):
        nonlocal written, gen_pass, rewritten, failed
        deal_idx, task_idx, task, sys_prompt, fmt_name = item

        if written >= target:
            return None

        skeleton = generate_skeleton(stream, seed, deal_idx)
        rec = grind_pair(stream, skeleton, task, task_idx, sys_prompt, fmt_name)

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
    global GEN_MODEL, PASS_MODEL
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
