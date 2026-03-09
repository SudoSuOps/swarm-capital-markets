# DealPacket Examples

## Example 1: Clean Industrial Deal

**Input:**
```
Industrial warehouse, Dallas-Fort Worth, $82M purchase, $5.9M NOI,
94% occupied, 240K SF, built 2019, requesting 65% LTV at 6.25%, 10yr/30yr amort
```

**Expected Output:**
```json
{
  "skill": "deal_packet",
  "deal_id": "dp-20260309-001",
  "status": "complete",
  "property": {
    "asset_type": "industrial",
    "market": "Dallas-Fort Worth",
    "sf": 240000,
    "year_built": 2019,
    "occupancy": 0.94
  },
  "financials": {
    "purchase_price": 82000000,
    "noi": 5900000
  },
  "derived_metrics": {
    "cap_rate": 0.0720,
    "ltv": 0.65,
    "dscr": 1.34,
    "debt_yield": 0.1108
  },
  "debt_terms": {
    "loan_amount": 53300000,
    "interest_rate": 0.0625,
    "term_years": 10,
    "amortization_years": 30
  },
  "missing_fields": [],
  "sanity_flags": [],
  "ready_for_underwriting": true
}
```

## Example 2: Incomplete Office Deal

**Input:**
```
Office tower, Manhattan, 450K SF, $180M, NOI $9.5M, current occupancy 72%
```

**Expected Output:**
```json
{
  "skill": "deal_packet",
  "deal_id": "dp-20260309-002",
  "status": "incomplete",
  "property": {
    "asset_type": "office",
    "market": "Manhattan",
    "sf": 450000,
    "occupancy": 0.72
  },
  "financials": {
    "purchase_price": 180000000,
    "noi": 9500000
  },
  "derived_metrics": {
    "cap_rate": 0.0528
  },
  "missing_fields": ["loan_amount", "interest_rate", "term_years", "amortization_years", "year_built"],
  "sanity_flags": ["unusual_cap_rate"],
  "ready_for_underwriting": false
}
```
