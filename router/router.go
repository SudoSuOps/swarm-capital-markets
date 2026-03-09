// Package router implements SwarmRouter — the orchestration layer that routes
// deal analysis requests to the correct model and skill chain.
//
// SwarmRouter selects models by task complexity and chains skills by request type:
//
//	underwriting:        DealPacket → Underwrite → CreditCommittee
//	acquisition_analysis: DealPacket → Underwrite → CapStackBuilder → WaterfallModel
//	distressed_asset:    DealPacket → DistressAnalyzer → LoanWorkout
//
// Models are served locally via vLLM. Router never calls external APIs for inference.
package router

import (
	"encoding/json"
	"fmt"
)

// RequestType defines the analysis pipeline to execute.
type RequestType string

const (
	Underwriting        RequestType = "underwriting"
	AcquisitionAnalysis RequestType = "acquisition_analysis"
	DistressedAsset     RequestType = "distressed_asset"
)

// TaskComplexity determines which model tier handles the request.
type TaskComplexity string

const (
	Fast    TaskComplexity = "fast"    // Classification, routing — SwarmSignal-9B
	Analysis TaskComplexity = "analysis" // Reasoning, underwriting — SwarmCapitalMarkets-27B
	Premium TaskComplexity = "premium"  // Complex multi-deal — 397B class
)

// SkillResult holds the structured JSON output from a single skill execution.
type SkillResult struct {
	SkillName string          `json:"skill"`
	DealID    string          `json:"deal_id"`
	Output    json.RawMessage `json:"output"`
	Error     string          `json:"error,omitempty"`
}

// PipelineResult aggregates all skill results from a full pipeline execution.
type PipelineResult struct {
	RequestType RequestType    `json:"request_type"`
	DealID      string         `json:"deal_id"`
	ModelUsed   string         `json:"model_used"`
	Steps       []SkillResult  `json:"steps"`
	Decision    json.RawMessage `json:"decision,omitempty"` // Final decision output
}

// Router orchestrates model selection and skill chain execution.
type Router struct {
	Models *ModelRegistry
	Skills *SkillRegistry
}

// NewRouter creates a Router with the given model and skill registries.
func NewRouter(models *ModelRegistry, skills *SkillRegistry) *Router {
	return &Router{Models: models, Skills: skills}
}

// Route determines the skill chain for a request type and executes it.
// Each skill in the chain receives the output of the previous skill as context.
func (r *Router) Route(reqType RequestType, dealInput json.RawMessage) (*PipelineResult, error) {
	chain, err := r.resolveChain(reqType)
	if err != nil {
		return nil, fmt.Errorf("resolve chain: %w", err)
	}

	complexity := r.classifyComplexity(reqType, dealInput)
	model, err := r.Models.Select(complexity)
	if err != nil {
		return nil, fmt.Errorf("model select: %w", err)
	}

	result := &PipelineResult{
		RequestType: reqType,
		ModelUsed:   model.Name,
	}

	// Execute skill chain sequentially — each step feeds the next.
	var context json.RawMessage = dealInput
	for _, skillName := range chain {
		skill, err := r.Skills.Get(skillName)
		if err != nil {
			return nil, fmt.Errorf("skill lookup %q: %w", skillName, err)
		}

		stepResult, err := r.executeSkill(skill, model, context)
		if err != nil {
			stepResult = &SkillResult{
				SkillName: skillName,
				Error:     err.Error(),
			}
		}
		result.Steps = append(result.Steps, *stepResult)

		// Pass this step's output as context to the next skill.
		if stepResult.Output != nil {
			context = stepResult.Output
		}
	}

	// The last step's output is the pipeline decision.
	if len(result.Steps) > 0 {
		last := result.Steps[len(result.Steps)-1]
		result.Decision = last.Output
	}

	return result, nil
}

// resolveChain returns the ordered skill names for a request type.
func (r *Router) resolveChain(reqType RequestType) ([]string, error) {
	switch reqType {
	case Underwriting:
		return []string{"dealpacket", "underwrite", "creditcommittee"}, nil
	case AcquisitionAnalysis:
		return []string{"dealpacket", "underwrite", "capstackbuilder", "waterfallmodel"}, nil
	case DistressedAsset:
		return []string{"dealpacket", "distressanalyzer", "loanworkout"}, nil
	default:
		return nil, fmt.Errorf("unknown request type: %s", reqType)
	}
}

// classifyComplexity determines the model tier based on request type and deal data.
// Override logic: multi-deal portfolios or platinum-tier analysis escalate to premium.
func (r *Router) classifyComplexity(reqType RequestType, dealInput json.RawMessage) TaskComplexity {
	// Default mapping by request type.
	switch reqType {
	case Underwriting:
		return Analysis
	case AcquisitionAnalysis:
		return Analysis
	case DistressedAsset:
		return Analysis
	default:
		return Fast
	}
	// TODO: Inspect dealInput for multi-deal graph structures or
	// explicit tier override to escalate to Premium.
}

// executeSkill sends the deal context to a model with the skill's system prompt
// and parses the structured JSON response.
func (r *Router) executeSkill(skill *SkillSpec, model *ModelEndpoint, context json.RawMessage) (*SkillResult, error) {
	// Build the prompt from skill spec + deal context.
	prompt := skill.BuildPrompt(context)

	// Call the model endpoint (vLLM OpenAI-compatible API).
	response, err := model.Complete(prompt)
	if err != nil {
		return nil, fmt.Errorf("model complete: %w", err)
	}

	// Validate response against the skill's schema.
	if err := skill.Validate(response); err != nil {
		// Log validation failure but still return the response.
		return &SkillResult{
			SkillName: skill.Name,
			Output:    response,
			Error:     fmt.Sprintf("validation: %v", err),
		}, nil
	}

	return &SkillResult{
		SkillName: skill.Name,
		Output:    response,
	}, nil
}
