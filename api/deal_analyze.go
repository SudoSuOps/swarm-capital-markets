// Package api — deal analysis endpoint.
//
// POST /api/deal/analyze
//
// Accepts a deal_input.json payload, runs the appropriate skill chain
// via SwarmRouter, computes deterministic metrics, and returns a
// structured decision_output.json response.
package api

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

// DealAnalyzeRequest is the incoming deal analysis request.
type DealAnalyzeRequest struct {
	// RequestType determines the skill chain: "underwriting", "acquisition_analysis", "distressed_asset"
	RequestType string          `json:"request_type"`
	Deal        json.RawMessage `json:"deal"`
}

// DealAnalyzeResponse wraps the decision output with execution metadata.
type DealAnalyzeResponse struct {
	DealID       string          `json:"deal_id"`
	RequestType  string          `json:"request_type"`
	ModelUsed    string          `json:"model_used"`
	Decision     json.RawMessage `json:"decision"`
	Metrics      json.RawMessage `json:"metrics"`
	SkillsRun    []string        `json:"skills_run"`
	ElapsedMs    int64           `json:"elapsed_ms"`
}

// handleDealAnalyze processes a full deal analysis request.
//
// Flow:
//  1. Parse and validate deal_input.json
//  2. Compute deterministic metrics (metrics engine — no LLM)
//  3. Route to skill chain via SwarmRouter
//  4. Synthesize decision output
//  5. Return structured response
func (s *Server) handleDealAnalyze(w http.ResponseWriter, r *http.Request) {
	// Read request body.
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeError(w, http.StatusBadRequest, "failed to read request body")
		return
	}
	defer r.Body.Close()

	var req DealAnalyzeRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("invalid JSON: %v", err))
		return
	}

	// Validate request type.
	validTypes := map[string]bool{
		"underwriting":         true,
		"acquisition_analysis": true,
		"distressed_asset":     true,
	}
	if !validTypes[req.RequestType] {
		writeError(w, http.StatusBadRequest, fmt.Sprintf(
			"invalid request_type: %q (must be underwriting, acquisition_analysis, or distressed_asset)",
			req.RequestType,
		))
		return
	}

	// Validate deal input has minimum required fields.
	var dealFields map[string]interface{}
	if err := json.Unmarshal(req.Deal, &dealFields); err != nil {
		writeError(w, http.StatusBadRequest, "deal must be a valid JSON object")
		return
	}

	required := []string{"purchase_price", "noi"}
	for _, field := range required {
		if _, ok := dealFields[field]; !ok {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("missing required field: %s", field))
			return
		}
	}

	// TODO: Wire to the actual pipeline:
	//
	// 1. metricsResult := metrics.Calculate(parsedDeal)
	// 2. pipelineResult := router.Route(reqType, req.Deal)
	// 3. decision := router.Synthesize(pipelineResult, metricsResult)
	//
	// For now, return a skeleton response showing the expected structure.

	resp := DealAnalyzeResponse{
		DealID:      "pending",
		RequestType: req.RequestType,
		ModelUsed:   "swarmcapmarkets-27b",
		Decision:    json.RawMessage(`{"decision": "pending", "confidence": 0.0}`),
		Metrics:     json.RawMessage(`{"cap_rate": 0.0, "dscr": 0.0, "ltv": 0.0}`),
		SkillsRun:   resolveSkillChain(req.RequestType),
		ElapsedMs:   0,
	}

	writeJSON(w, http.StatusOK, resp)
}

// resolveSkillChain returns the skill names for a given request type.
func resolveSkillChain(reqType string) []string {
	switch reqType {
	case "underwriting":
		return []string{"dealpacket", "underwrite", "creditcommittee"}
	case "acquisition_analysis":
		return []string{"dealpacket", "underwrite", "capstackbuilder", "waterfallmodel"}
	case "distressed_asset":
		return []string{"dealpacket", "distressanalyzer", "loanworkout"}
	default:
		return nil
	}
}
