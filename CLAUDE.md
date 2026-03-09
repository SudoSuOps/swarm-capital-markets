# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

SwarmCapitalMarkets is a CRE capital markets intelligence engine — the training data factory for SwarmCapitalMarkets-27B, a fine-tuned Qwen3.5-27B model purpose-built for institutional CRE debt analysis.

Three layers:
1. **Cook Pipeline** (`data/`): 8-stream capital markets data factory + golden pairs + RPA + mutations + assembly
2. **Eval Suite** (`eval/`): 180 institutional-grade prompts across 11 CRE domains, 5 difficulty tiers
3. **Skills** (`skills/`): 7 installable capital markets AI skills with SKILL.md specs, validators, schemas

## Cook Architecture

```
SwarmCapitalMarkets Training Pipeline
├── 8 Cook Streams (Together.ai API → ~35K raw pairs)
│   ├── debt_maturity      6,073 pairs — loan maturity, refi analysis, extension modeling
│   ├── cmbs_distress      5,113 pairs — special servicing, loss severity, tranche analysis
│   ├── rate_advisory      5,666 pairs — hedging, swap/cap pricing, rate lock strategy
│   ├── equity_advisory    5,187 pairs — JV structuring, promote waterfalls, GP/LP terms
│   ├── valuation_advisory 5,142 pairs — DCF, cap rate, comparable sales, stress testing
│   ├── deal_origination   5,000 pairs — sourcing, screening, LOI, due diligence
│   ├── macro_causality    2,400 pairs — 12 macro tasks (rate shocks, spreads, cycles)
│   └── deal_graph           500 pairs — multi-deal portfolio reasoning (3-8 deals)
├── Golden Pairs (327 pairs — 109 prompts × 3 personas, hand-crafted conceptual)
├── RPA (12,000 target — 5 personas × 2 paths, 235B quality model)
├── Platinum Mutations (194 pairs — hedge-fund grade variant engine)
└── Assembly → 5-Pool Blend → Contrastive Rebalance → Eval Holdout → Train JSONL
```

## Cook Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `data/cre_capital_cook.py` | 8-stream capital markets factory (70+ task types) | `TOGETHER_KEY=... python3 -m data.cre_capital_cook --stream all --workers 50` |
| `data/cook_rpa.py` | Reasoning Path Augmentation (5 personas, 235B model) | `TOGETHER_KEY=... python3 -m data.cook_rpa --workers 100` |
| `data/cook_golden_pairs.py` | 109 hand-crafted prompts × 3 response variants | `TOGETHER_KEY=... python3 -m data.cook_golden_pairs` |
| `data/cook_platinum_mutations.py` | Platinum-tier mutation engine | `TOGETHER_KEY=... python3 -m data.cook_platinum_mutations` |
| `data/assemble_final.py` | 5-pool blend + rebalance + eval holdout + audit | `python3 -m data.assemble_final [--dry-run]` |

## 5-Pool Assembly Pipeline

```
Pool Weights:
  Diversified  60%  — 8 core task streams (balanced by stream)
  RPA          25%  — Multi-trajectory reasoning paths
  Macro+Graph   8%  — Temporal evolution + deal graph portfolio reasoning
  Golden        4%  — Hand-crafted conceptual knowledge pairs
  Mutations     3%  — Platinum variant mutations

8-Stage Pipeline:
  1. Load 5 pools from JSONL files
  2. Dedup (MD5 fingerprint on normalized text)
  3. 5-Pool weighted blend
  4. Contrastive rebalance (Bronze 20%, Silver 20%, Gold 15%, High 30%, Platinum 15%)
  5. Eval holdout (500 stratified pairs)
  6. 2× shuffle (structural monotony break)
  7. Audit (start-phrase entropy < 4%, pool proportions, difficulty distribution)
  8. Write train + eval JSONL
```

## Deal Graph Intelligence

Multi-deal portfolio reasoning — 3-8 deals per scenario with macro overlay:
- **Lender Exposure**: Bank portfolio with concentration risk, contagion cascades
- **Fund Portfolio**: GP/LP multi-asset allocation, distress triage, capital calls
- **Market Comparables**: Cross-deal pricing dynamics, supply/demand, cap rate convergence

9 task types: lender_exposure_cascade, lender_contagion_analysis, lender_concentration_risk, portfolio_capital_allocation, portfolio_distress_triage, portfolio_refinancing_wave, market_comp_valuation, market_pricing_dynamics, market_supply_demand

## Macro Causality Tasks (12)

rate_shock_transmission, credit_spread_cascade, maturity_wall_timeline, fed_policy_chain, banking_stress_contagion, cap_rate_cycle_evolution, office_secular_decline, industrial_divergence_thesis, distress_cycle_anatomy, capital_flow_rotation, regulatory_tightening_cascade, insurance_cost_spiral

## Training Target

- **Model**: Qwen3.5-27B, QLoRA r=64, alpha=32, lr=2e-5, 2-3 epochs
- **Curriculum**: Phase 1 (procedural — underwriting, calcs) → Phase 2 (strategic — IC memos, advisory) → Phase 3 (RPA + macro + graphs)
- **Hardware**: Dual Blackwell — RTX PRO 4500 (32GB) + RTX PRO 6000 (96GB), 128GB total VRAM
- **Serving**: vLLM bf16, dual-GPU

## Together.ai API

- **GEN model**: `Qwen/Qwen3-Next-80B-A3B-Instruct` (fast generation)
- **PASS model**: `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` (quality rewrites, RPA)
- Two-tier cook: GEN → quality check → PASS (rewrite if fails)
- RPA uses 235B exclusively

## Schemas

- `schemas/deal_input.json` — Canonical deal input (8 asset types, market, capital stack, sponsor)
- `schemas/decision_output.json` — Structured decision output (6 decision classes, confidence, conditions)

## Conventions

- All rates as decimals: `0.065` not `6.5%`
- SF as integers, dollar amounts as raw numbers (no abbreviations)
- Decision classes: `approve | approve_with_conditions | restructure | decline | watchlist | distressed_opportunity`
- Confidence scores: 0.00 to 1.00
- Risk flags: lowercase snake_case strings
- DSCR to 2 decimal places, loan amounts rounded to nearest $100K
- Start-phrase entropy threshold: < 4% (top-1 5-token prefix)

## Eval Suite

180 prompts in `eval/eval_swarmcapital.jsonl`, one JSON object per line:
```json
{"id": "eval-cap-001", "category": "valuation_stress", "difficulty": "high", "prompt": "..."}
```

Tiers: Bronze (8), Silver (5), Gold (2), High (146), Platinum (19)
