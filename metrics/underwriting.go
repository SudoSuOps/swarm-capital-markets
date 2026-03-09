// Package metrics provides deterministic CRE financial calculations.
//
// CRITICAL: LLMs must NEVER calculate raw math. All financial metrics are
// computed by this package and injected into the pipeline. The LLM's job
// is qualitative analysis and decision reasoning — not arithmetic.
package metrics

import (
	"fmt"
	"math"
)

// DealInput represents the canonical deal input for metric calculation.
type DealInput struct {
	PurchasePrice    float64 `json:"purchase_price"`
	NOI              float64 `json:"noi"`
	Occupancy        float64 `json:"occupancy"` // 0.0 to 1.0
	LoanAmount       float64 `json:"loan_amount"`
	InterestRate     float64 `json:"interest_rate"` // As decimal: 0.065 not 6.5%
	AmortizationYrs  int     `json:"amortization_years"`
	LoanTermYrs      int     `json:"loan_term_years"`
	GrossRevenue     float64 `json:"gross_revenue"`
	OperatingExpense float64 `json:"operating_expenses"`

	// Lender constraints (used for max loan sizing).
	MaxLTV       float64 `json:"ltv_limit"`       // e.g., 0.75
	MinDSCR      float64 `json:"dscr_requirement"` // e.g., 1.25
	MinDebtYield float64 `json:"debt_yield_min"`   // e.g., 0.08
}

// UnderwritingResult holds all computed metrics.
type UnderwritingResult struct {
	CapRate             float64 `json:"cap_rate"`
	DSCR                float64 `json:"dscr"`
	LTV                 float64 `json:"ltv"`
	DebtYield           float64 `json:"debt_yield"`
	LoanConstant        float64 `json:"loan_constant"`
	MaxLoanDSCR         float64 `json:"max_loan_dscr"`
	MaxLoanLTV          float64 `json:"max_loan_ltv"`
	MaxLoanDebtYield    float64 `json:"max_loan_debt_yield"`
	MaxLoan             float64 `json:"max_loan"`             // min(DSCR, LTV, DebtYield)
	BindingConstraint   string  `json:"binding_constraint"`   // "ltv" | "dscr" | "debt_yield"
	BreakEvenOccupancy  float64 `json:"break_even_occupancy"`
	RefinancingGap      float64 `json:"refinancing_gap"`      // Current debt - max new loan
	AnnualDebtService   float64 `json:"annual_debt_service"`
	MonthlyPayment      float64 `json:"monthly_payment"`
}

// Calculate computes all underwriting metrics deterministically.
// All rates are decimals. Dollar amounts are raw numbers. DSCR to 2 decimal places.
// Loan amounts rounded to nearest $100K.
func Calculate(d *DealInput) (*UnderwritingResult, error) {
	if d.PurchasePrice <= 0 {
		return nil, fmt.Errorf("purchase_price must be positive")
	}
	if d.NOI < 0 {
		return nil, fmt.Errorf("noi cannot be negative")
	}

	r := &UnderwritingResult{}

	// Cap Rate = NOI / Purchase Price
	r.CapRate = d.NOI / d.PurchasePrice

	// Annual Debt Service (standard amortizing mortgage formula).
	if d.LoanAmount > 0 && d.InterestRate > 0 && d.AmortizationYrs > 0 {
		r.MonthlyPayment = monthlyPayment(d.LoanAmount, d.InterestRate, d.AmortizationYrs)
		r.AnnualDebtService = r.MonthlyPayment * 12
	}

	// DSCR = NOI / Annual Debt Service
	if r.AnnualDebtService > 0 {
		r.DSCR = math.Round(d.NOI/r.AnnualDebtService*100) / 100
	}

	// LTV = Loan Amount / Purchase Price
	if d.LoanAmount > 0 {
		r.LTV = d.LoanAmount / d.PurchasePrice
	}

	// Debt Yield = NOI / Loan Amount
	if d.LoanAmount > 0 {
		r.DebtYield = d.NOI / d.LoanAmount
	}

	// Loan Constant = Annual Debt Service / Loan Amount
	if d.LoanAmount > 0 && r.AnnualDebtService > 0 {
		r.LoanConstant = r.AnnualDebtService / d.LoanAmount
	}

	// Max Loan — LTV Constraint
	if d.MaxLTV > 0 {
		r.MaxLoanLTV = roundTo100K(d.PurchasePrice * d.MaxLTV)
	}

	// Max Loan — DSCR Constraint
	// Solve: NOI / (monthly_payment(L, rate, amort) * 12) = min_dscr
	// → max ADS = NOI / min_dscr → solve for L
	if d.MinDSCR > 0 && d.InterestRate > 0 && d.AmortizationYrs > 0 {
		maxADS := d.NOI / d.MinDSCR
		r.MaxLoanDSCR = roundTo100K(loanFromPayment(maxADS/12, d.InterestRate, d.AmortizationYrs))
	}

	// Max Loan — Debt Yield Constraint
	// debt_yield = NOI / loan → loan = NOI / min_debt_yield
	if d.MinDebtYield > 0 {
		r.MaxLoanDebtYield = roundTo100K(d.NOI / d.MinDebtYield)
	}

	// Max Loan = MIN of all constraints (only positive values).
	r.MaxLoan, r.BindingConstraint = minConstraint(r.MaxLoanLTV, r.MaxLoanDSCR, r.MaxLoanDebtYield)

	// Break-Even Occupancy = (OpEx + Debt Service) / Gross Revenue
	if d.GrossRevenue > 0 {
		r.BreakEvenOccupancy = (d.OperatingExpense + r.AnnualDebtService) / d.GrossRevenue
	}

	// Refinancing Gap = Current Loan Balance - Max New Loan
	if d.LoanAmount > 0 && r.MaxLoan > 0 {
		r.RefinancingGap = d.LoanAmount - r.MaxLoan
		if r.RefinancingGap < 0 {
			r.RefinancingGap = 0 // No gap — loan is within new constraints.
		}
	}

	return r, nil
}

// monthlyPayment calculates the monthly mortgage payment using the standard
// amortization formula: P * [r(1+r)^n] / [(1+r)^n - 1]
// where r = annual_rate/12, n = amort_years * 12.
func monthlyPayment(principal, annualRate float64, amortYears int) float64 {
	r := annualRate / 12
	n := float64(amortYears * 12)
	if r == 0 {
		return principal / n
	}
	factor := math.Pow(1+r, n)
	return principal * (r * factor) / (factor - 1)
}

// loanFromPayment is the inverse of monthlyPayment — given a monthly payment,
// rate, and amortization, solve for the principal (max loan amount).
// P = payment * [(1+r)^n - 1] / [r * (1+r)^n]
func loanFromPayment(payment, annualRate float64, amortYears int) float64 {
	r := annualRate / 12
	n := float64(amortYears * 12)
	if r == 0 {
		return payment * n
	}
	factor := math.Pow(1+r, n)
	return payment * (factor - 1) / (r * factor)
}

// roundTo100K rounds a dollar amount to the nearest $100,000.
func roundTo100K(amount float64) float64 {
	return math.Round(amount/100000) * 100000
}

// minConstraint returns the smallest positive value among the three loan
// constraints and identifies which constraint is binding.
func minConstraint(ltv, dscr, debtYield float64) (float64, string) {
	type constraint struct {
		value float64
		name  string
	}
	constraints := []constraint{
		{ltv, "ltv"},
		{dscr, "dscr"},
		{debtYield, "debt_yield"},
	}

	minVal := math.MaxFloat64
	binding := ""
	for _, c := range constraints {
		if c.value > 0 && c.value < minVal {
			minVal = c.value
			binding = c.name
		}
	}
	if minVal == math.MaxFloat64 {
		return 0, ""
	}
	return minVal, binding
}
