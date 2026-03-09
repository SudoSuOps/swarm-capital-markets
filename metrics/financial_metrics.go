// Package metrics — additional financial calculations for CRE analysis.
//
// These functions support the full underwriting pipeline:
// - Sensitivity matrices (5x5 NOI × rate stress)
// - IRR calculation (Newton's method)
// - Equity multiple and cash-on-cash return
// - Waterfall distribution mechanics
package metrics

import (
	"math"
)

// SensitivityMatrix holds a 5x5 DSCR stress test grid.
type SensitivityMatrix struct {
	NOIStresses  []float64   `json:"noi_stresses"`  // e.g., [-0.20, -0.10, 0.0, 0.05, 0.10]
	RateStresses []float64   `json:"rate_stresses"`  // e.g., [0.0200, 0.0100, 0.0, -0.0050, -0.0100]
	DSCRValues   [][]float64 `json:"dscr_values"`    // 5x5 matrix
}

// BuildSensitivityMatrix computes a 5x5 DSCR matrix across NOI and rate stress scenarios.
//
// Standard stresses:
//
//	NOI:  -20%, -10%, base, +5%, +10%
//	Rate: +200bps, +100bps, base, -50bps, -100bps
func BuildSensitivityMatrix(noi, loanAmount, baseRate float64, amortYears int) *SensitivityMatrix {
	noiStresses := []float64{-0.20, -0.10, 0.0, 0.05, 0.10}
	rateStresses := []float64{0.0200, 0.0100, 0.0, -0.0050, -0.0100}

	matrix := &SensitivityMatrix{
		NOIStresses:  noiStresses,
		RateStresses: rateStresses,
		DSCRValues:   make([][]float64, len(noiStresses)),
	}

	for i, noiStress := range noiStresses {
		matrix.DSCRValues[i] = make([]float64, len(rateStresses))
		stressedNOI := noi * (1 + noiStress)
		for j, rateStress := range rateStresses {
			stressedRate := baseRate + rateStress
			if stressedRate <= 0 {
				stressedRate = 0.001 // Floor at 10bps.
			}
			payment := monthlyPayment(loanAmount, stressedRate, amortYears)
			ads := payment * 12
			if ads > 0 {
				matrix.DSCRValues[i][j] = math.Round(stressedNOI/ads*100) / 100
			}
		}
	}

	return matrix
}

// IRR calculates the Internal Rate of Return using Newton's method.
// cashFlows[0] is the initial investment (negative), subsequent entries are periodic returns.
// Returns the annualized rate. Assumes annual periods unless periodMonths is specified.
func IRR(cashFlows []float64, maxIterations int) (float64, error) {
	if len(cashFlows) < 2 {
		return 0, nil
	}

	// Initial guess based on total return.
	totalReturn := 0.0
	for _, cf := range cashFlows[1:] {
		totalReturn += cf
	}
	guess := (totalReturn/math.Abs(cashFlows[0]) - 1) / float64(len(cashFlows)-1)
	if guess <= -1 {
		guess = 0.10
	}

	rate := guess
	for iter := 0; iter < maxIterations; iter++ {
		npv := 0.0
		dnpv := 0.0
		for t, cf := range cashFlows {
			discount := math.Pow(1+rate, float64(t))
			npv += cf / discount
			if t > 0 {
				dnpv -= float64(t) * cf / math.Pow(1+rate, float64(t+1))
			}
		}
		if math.Abs(dnpv) < 1e-12 {
			break
		}
		newRate := rate - npv/dnpv
		if math.Abs(newRate-rate) < 1e-8 {
			return newRate, nil
		}
		rate = newRate
	}

	return rate, nil
}

// EquityMultiple calculates total distributions / total invested capital.
func EquityMultiple(totalDistributions, investedCapital float64) float64 {
	if investedCapital <= 0 {
		return 0
	}
	return totalDistributions / investedCapital
}

// CashOnCash calculates annual cash flow / invested equity.
func CashOnCash(annualCashFlow, investedEquity float64) float64 {
	if investedEquity <= 0 {
		return 0
	}
	return annualCashFlow / investedEquity
}

// WACC calculates the Weighted Average Cost of Capital for a capital stack.
// Each layer is (amount, cost_rate). Returns the blended rate.
func WACC(layers []struct{ Amount, Rate float64 }) float64 {
	totalAmount := 0.0
	weightedCost := 0.0
	for _, l := range layers {
		totalAmount += l.Amount
		weightedCost += l.Amount * l.Rate
	}
	if totalAmount <= 0 {
		return 0
	}
	return weightedCost / totalAmount
}

// PositiveLeverage returns true if the cap rate exceeds the WACC,
// meaning leverage is accretive to equity returns.
func PositiveLeverage(capRate, wacc float64) bool {
	return capRate > wacc
}

// LossSeverity calculates the loss as a percentage of original basis.
// loss_severity = 1 - (current_value / original_basis)
func LossSeverity(currentValue, originalBasis float64) float64 {
	if originalBasis <= 0 {
		return 0
	}
	return 1 - (currentValue / originalBasis)
}

// RecoveryRate calculates net recovery as a percentage of loan balance.
func RecoveryRate(netRecovery, loanBalance float64) float64 {
	if loanBalance <= 0 {
		return 0
	}
	return netRecovery / loanBalance
}

// DPOImpliedYield calculates the implied yield of a discounted payoff
// at various purchase prices (cents on dollar), given expected recovery and timeline.
func DPOImpliedYield(loanBalance, expectedRecovery float64, timelineMonths int, purchaseCents float64) float64 {
	purchasePrice := loanBalance * (purchaseCents / 100)
	if purchasePrice <= 0 || timelineMonths <= 0 {
		return 0
	}
	years := float64(timelineMonths) / 12
	// Simple annualized return: (recovery / purchase)^(1/years) - 1
	return math.Pow(expectedRecovery/purchasePrice, 1/years) - 1
}
