// Package router — decision engine synthesizes skill chain outputs into a
// canonical decision_output.json response.
//
// The decision engine is the final step after all skills in a chain execute.
// It merges metrics (computed deterministically by the metrics engine) with
// the LLM's qualitative analysis to produce the structured decision.
package router

import (
	"encoding/json"
	"fmt"
)

// Decision represents the canonical decision output schema.
type Decision struct {
	DealID                    string              `json:"deal_id"`
	Decision                  string              `json:"decision"` // approve | approve_with_conditions | restructure | decline | watchlist | distressed_opportunity
	Confidence                float64             `json:"confidence"`
	RecommendedMaxLoan        float64             `json:"recommended_max_loan"`
	BindingConstraint         string              `json:"binding_constraint"`
	RiskFlags                 []string            `json:"risk_flags"`
	CapitalStackRecommendation json.RawMessage    `json:"capital_stack_recommendation,omitempty"`
	Analysis                  *AnalysisMetrics    `json:"analysis"`
	ScenarioAnalysis          *ScenarioAnalysis   `json:"scenario_analysis,omitempty"`
}

// AnalysisMetrics holds deterministic financial metrics.
// These are computed by the metrics engine, NOT by the LLM.
type AnalysisMetrics struct {
	CapRate             float64 `json:"cap_rate"`
	DSCR                float64 `json:"dscr"`
	LTV                 float64 `json:"ltv"`
	DebtYield           float64 `json:"debt_yield"`
	MaxLoanDSCR         float64 `json:"max_loan_dscr"`
	MaxLoanLTV          float64 `json:"max_loan_ltv"`
	BreakEvenOccupancy  float64 `json:"break_even_occupancy"`
	RefinancingGap      float64 `json:"refinancing_gap"`
}

// ScenarioAnalysis holds stress test outcomes at three severity levels.
type ScenarioAnalysis struct {
	Base     *ScenarioOutcome `json:"base"`
	Downside *ScenarioOutcome `json:"downside"`
	Severe   *ScenarioOutcome `json:"severe"`
}

// ScenarioOutcome captures metrics under a specific stress scenario.
type ScenarioOutcome struct {
	NOI              float64 `json:"noi"`
	DSCR             float64 `json:"dscr"`
	LTV              float64 `json:"ltv"`
	DebtYield        float64 `json:"debt_yield"`
	PropertyValue    float64 `json:"property_value"`
	CashFlowCoverage float64 `json:"cash_flow_coverage"`
}

// ValidDecisions lists all permitted decision classes.
var ValidDecisions = map[string]bool{
	"approve":                true,
	"approve_with_conditions": true,
	"restructure":            true,
	"decline":                true,
	"watchlist":              true,
	"distressed_opportunity":  true,
}

// Synthesize merges pipeline skill results and deterministic metrics into
// the canonical decision output.
func Synthesize(pipeline *PipelineResult, metrics *AnalysisMetrics) (*Decision, error) {
	if pipeline == nil || len(pipeline.Steps) == 0 {
		return nil, fmt.Errorf("empty pipeline result")
	}

	decision := &Decision{
		DealID:   pipeline.DealID,
		Analysis: metrics,
	}

	// Extract decision fields from the last skill's output (credit committee or loan workout).
	lastOutput := pipeline.Steps[len(pipeline.Steps)-1].Output
	if lastOutput != nil {
		var raw map[string]interface{}
		if err := json.Unmarshal(lastOutput, &raw); err == nil {
			if d, ok := raw["decision"].(string); ok && ValidDecisions[d] {
				decision.Decision = d
			}
			if c, ok := raw["confidence"].(float64); ok {
				decision.Confidence = clamp(c, 0.0, 1.0)
			}
			if flags, ok := raw["risk_flags"].([]interface{}); ok {
				for _, f := range flags {
					if s, ok := f.(string); ok {
						decision.RiskFlags = append(decision.RiskFlags, s)
					}
				}
			}
			if bc, ok := raw["binding_constraint"].(string); ok {
				decision.BindingConstraint = bc
			}
		}
	}

	// Override metrics with deterministic calculations (LLMs must not compute math).
	if metrics != nil {
		decision.RecommendedMaxLoan = min(metrics.MaxLoanDSCR, metrics.MaxLoanLTV)
		decision.BindingConstraint = determineBindingConstraint(metrics)
	}

	return decision, nil
}

// determineBindingConstraint identifies which metric produces the smallest max loan.
func determineBindingConstraint(m *AnalysisMetrics) string {
	if m.MaxLoanDSCR <= 0 && m.MaxLoanLTV <= 0 {
		return ""
	}
	if m.MaxLoanDSCR > 0 && m.MaxLoanDSCR < m.MaxLoanLTV {
		return "dscr"
	}
	if m.MaxLoanLTV > 0 && m.MaxLoanLTV < m.MaxLoanDSCR {
		return "ltv"
	}
	// Check debt yield constraint.
	if m.DebtYield > 0 {
		debtYieldLoan := 0.0
		if m.DebtYield > 0 {
			// debt_yield = NOI / loan → loan = NOI / min_debt_yield
			// We'd need NOI here; for now, flag if debt yield is tight.
			_ = debtYieldLoan
		}
		return "debt_yield"
	}
	return "ltv"
}

// clamp restricts a value to [min, max].
func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// min returns the smaller of two float64 values.
func min(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
