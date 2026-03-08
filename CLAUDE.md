# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

SwarmCapitalMarkets is a CRE capital markets intelligence engine with three layers:
1. **Eval Suite** (`eval/`): 180 institutional-grade prompts across 11 CRE domains, 5 difficulty tiers
2. **Training Datasets** (`datasets/`, `decision_pairs/`, `scenarios/`): Knowledge pairs, deal decisions, macro simulations
3. **Skills** (`skills/`, `worker/`): 7 installable capital markets AI skills with SKILL.md specs, JS modules, validators

## Architecture

```
SwarmCapitalMarkets Intelligence Engine
├── Deal Input → Normalization → Feature Engineering → Retrieval → Multi-Analyst Reasoning → Decision → Output
├── 4 Product Modes: Deal Screen, Credit Memo, Scenario Lab, Distress Mode
├── 5 Analyst Modules: Underwriting, Credit, Capital Markets, Distress, Investment Committee
└── 7 Skills: DealPacket, Underwrite, CreditCommittee, DistressAnalyzer, CapStackBuilder, WaterfallModel, LoanWorkout
```

## Skills Framework

Each skill has 3 artifacts:
- `skills/{name}/SKILL.md` — YAML frontmatter + markdown spec (role, system prompt, output schema, examples)
- `worker/src/skills/{name}.js` — JS module exporting `{ name, version, role, description, systemPrompt, examples }`
- `worker/src/skills/schemas.js` — Deterministic validator (no LLM calls)

Registry: `worker/src/skills/registry.js` — imports all skills, exports `executeSkill()`, `listSkills()`, `getSkillSpec()`

## Eval Suite

180 prompts in `eval/eval_swarmcapital.jsonl`, one JSON object per line:
```json
{"id": "eval-cap-001", "category": "valuation_stress", "difficulty": "high", "prompt": "..."}
```

Tiers: Bronze (8), Silver (5), Gold (2), High (146), Platinum (19)

## Dataset Types

| Directory | Purpose | Format |
|-----------|---------|--------|
| `datasets/` | Conceptual CRE knowledge (6 domain buckets) | JSONL prompt objects |
| `decision_pairs/` | Structured approve/reject/restructure decisions | JSONL with decision metadata |
| `scenarios/` | Macro-to-asset causality (rate shocks, spreads, cycles) | JSONL scenario prompts |
| `benchmarks/` | Test suites for model evaluation | JSONL bench prompts |

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

## Cook Scripts

- `data/cre_capital_cook.py` — 6-stream capital markets factory (debt_maturity, cmbs_distress, rate_advisory, equity_advisory, valuation_advisory, deal_origination)
- `data/cook_platinum_mutations.py` — Hedge-fund grade mutation engine for platinum pairs
