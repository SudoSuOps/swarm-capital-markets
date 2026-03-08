# SwarmCapitalMarkets Intelligence Engine

From raw deal inputs to institutional-grade credit and investment decisions.

## Architecture

```
Deal Input → Normalization → Feature Engineering → Retrieval → Multi-Analyst Reasoning → Decision → Output
```

### 1. Input Layer
Property details, rent roll, NOI, occupancy, market, debt terms, capital stack, sponsor profile, macro assumptions.

Supported formats: form input, CSV/JSON upload, API payload, manual analyst entry.

### 2. Normalization Layer
Field mapping, missing field detection, unit normalization, sanity checks → canonical schema (`schemas/deal_input.json`).

### 3. Feature Engineering Layer
Derived metrics computed from raw inputs:
- Cap rate, DSCR, debt yield, loan constant
- LTV, LTC, break-even occupancy
- Equity multiple, IRR sensitivity, refinance probability

### 4. Retrieval Layer
Context-aware intelligence retrieval based on submitted deal:
- Similar deal case studies
- Sector distress data
- Maturity wall exposure
- Workout examples
- Spread behavior for comparable credit

### 5. Reasoning Layer — Multi-Analyst Modules

| Module | Role |
|--------|------|
| Underwriting Analyst | Sizes the loan, checks constraints |
| Credit Analyst | Stress tests DSCR, models downside |
| Capital Markets Analyst | Evaluates spreads, refi risk, market position |
| Distress Analyst | Models default resolution paths |
| IC Analyst | Synthesizes all modules, final recommendation |

### 6. Decision Layer
Structured output (`schemas/decision_output.json`):

```json
{
  "decision": "approve_with_conditions",
  "confidence": 0.84,
  "analysis": {
    "max_loan": 47000000,
    "binding_constraint": "dscr",
    "dscr": 1.31,
    "levered_irr": 0.142
  },
  "risk_flags": [
    "exit cap sensitivity",
    "office sector weakness",
    "refinancing risk at maturity"
  ],
  "conditions": [
    "12-month interest reserve",
    "cash management lockbox at 1.15x DSCR trigger"
  ]
}
```

Decision classes: Approve | Approve with Conditions | Restructure | Decline | Watchlist | Distressed Opportunity

### 7. Output Layer
Multiple delivery formats:
- Decision summary (1 paragraph)
- Full underwriting memo
- Sensitivity table
- Credit committee view
- JSON API response
- Signal score

## Product Modes

### A. Deal Screen
Fast input → fast answer. "Should I look at this deal?"

### B. Credit Memo
Full structured memo for lenders and funds.

### C. Scenario Lab
Change rates, vacancy, exit cap, spreads. "What happens if rates rise 100bps?"

### D. Distress Mode
Evaluate workouts, foreclosure, rescue capital, loan-to-own.

## Model Stack

| Model | Role |
|-------|------|
| 9B / 27B | Fast screening, broad reasoning, structured memo generation |
| 397B lane | Platinum analysis, benchmark generation, committee-grade outputs |
| Judge | Enforce structure, verify calculations, score reasoning quality |

## Dataset Architecture

Three dataset types power the engine:

### Knowledge Pairs (`datasets/`)
Conceptual reasoning — mezzanine financing, waterfalls, 1031 exchanges, loan workouts, underwriting models.

### Deal Decision Pairs (`decision_pairs/`)
Financing decisions with structured inputs and outputs — approve/reject/restructure with confidence scores and risk flags.

### Scenario Simulation Pairs (`scenarios/`)
Macro-to-asset causality — rate shocks, spread dislocations, distress cycles. Teaches the model how macro changes propagate through property-level economics.

## Reasoning Framework

Every analysis follows five layers:

```
MARKET CONTEXT      → Macro conditions, asset class dynamics, capital flows
DEAL STRUCTURE      → Capital stack, financing, ownership structure
FINANCIAL MECHANICS → Valuation, DSCR, leverage, spreads, IRR
RISK ANALYSIS       → Credit, market, execution risks
INVESTOR STRATEGY   → How lenders, PE, hedge funds respond
```

## Benchmark

The `benchmarks/` directory contains test suites for evaluating any model on CRE capital markets reasoning:

- **Underwriting Tests**: Loan sizing, DSCR calculation, sensitivity analysis
- **Credit Risk Tests**: Distress evaluation, workout analysis, recovery modeling
- **Distress Analysis Tests**: Decision intelligence — credit committee, IC, workout desk

Target: establish CREBench as the standard benchmark for CRE financial AI reasoning.
