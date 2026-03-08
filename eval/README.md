# SwarmCapital Eval Suite

**180 institutional-grade evaluation prompts** for CRE capital markets AI.

## Format

Each line in `eval_swarmcapital.jsonl` is a JSON object:

```json
{
  "id": "eval-cap-001",
  "category": "valuation_stress",
  "difficulty": "high",
  "prompt": "..."
}
```

## Difficulty Tiers

| Tier | Count | Purpose |
|------|-------|---------|
| **Bronze** | 8 | CRE fundamentals — cap rate, NOI, LTV, DSCR, lease types |
| **Silver** | 5 | Structured analysis — valuation methods, debt sources, sectors |
| **Gold** | 2 | Advanced mechanics — CMBS securitization, refinancing |
| **High** | 146 | Institutional reasoning — full domain coverage |
| **Platinum** | 19 | Decision intelligence + temporal evolution + engine pipeline |

## Domains

| Domain | Count | Description |
|--------|-------|-------------|
| CRE Fundamentals | 12 | Valuation, cap rates, property economics |
| Capital Markets & Trading | 20 | Spreads, liquidity, institutional flows, cycle behavior |
| Tax Structuring (1031) | 14 | Exchanges, OZ funds, cost segregation, estate planning |
| Structured Finance | 10 | Mezzanine, preferred equity, intercreditor mechanics |
| Distressed Credit & Workouts | 16 | Special servicing, foreclosure, loan-to-own, recovery |
| Governance & Partnerships | 10 | LLC structures, buy-sell, key-man, succession |
| PE Economics (Waterfalls) | 14 | Promote, carry, IRR hurdles, catch-up, fund terms |
| Underwriting Models | 22 | DSCR sizing, sensitivity, DCF, stress testing |
| Real Deal Case Studies | 7 | IC memos, full deal analysis, temporal evolution |
| Decision Intelligence | 8 | Credit committee, IC, workout, distressed desk decisions |
| Engine Pipeline | 5 | Full product mode tests (Deal Screen, Credit Memo, Scenario Lab, Distress Mode) |

## Reasoning Framework

Every high-tier prompt tests the 5-layer analytical framework:

```
MARKET CONTEXT     → Macro conditions, asset class dynamics, capital flows
DEAL STRUCTURE     → Capital stack, financing, ownership structure
FINANCIAL MECHANICS → Valuation, DSCR, leverage, spreads, IRR
RISK ANALYSIS      → Credit, market, execution risks
INVESTOR STRATEGY  → How lenders, PE, hedge funds respond
```

## Decision Intelligence Format

Platinum prompts require structured decisions:

```
DEAL INPUT → ANALYSIS → DECISION → RATIONALE
```

Decision classes: Approve / Approve with Conditions / Restructure / Decline / Watchlist / Distressed Opportunity

## Temporal Deal Evolution

Prompts 172-175 track the same deal across three time periods:
- Acquisition (2019)
- Rate Shock (2023)
- Distress or Exit (2025)

## Usage

```python
import json

with open("eval_swarmcapital.jsonl") as f:
    prompts = [json.loads(line) for line in f]

# Filter by difficulty
platinum = [p for p in prompts if p["difficulty"] == "platinum"]

# Filter by category
distress = [p for p in prompts if "distress" in p["category"]]
```

## License

Proprietary. Copyright 2026 Swarm & Bee.
