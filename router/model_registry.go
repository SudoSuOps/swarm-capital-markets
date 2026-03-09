// Package router — model registry manages vLLM endpoints and model selection.
//
// Models are served locally on sovereign hardware. The registry maps task
// complexity tiers to specific model endpoints.
//
// Default fleet:
//
//	Fast:     SwarmSignal-9B     (swarmrails:8081) — routing, classification
//	Analysis: SwarmCapitalMarkets-27B (swarmrails:8082) — reasoning, underwriting
//	Premium:  397B class         (configurable)    — complex multi-deal analysis
package router

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// ModelEndpoint represents a vLLM model serving endpoint.
type ModelEndpoint struct {
	Name       string         `json:"name"`        // Human-readable name
	ModelID    string         `json:"model_id"`     // vLLM model name (e.g., "swarmcapmarkets-27b")
	BaseURL    string         `json:"base_url"`     // e.g., "http://localhost:8082"
	Tier       TaskComplexity `json:"tier"`          // fast, analysis, premium
	MaxTokens  int            `json:"max_tokens"`    // Default max output tokens
	VRAM_GB    int            `json:"vram_gb"`       // GPU memory allocated
	TokPerSec  float64        `json:"tok_per_sec"`   // Benchmark throughput
}

// ModelRegistry holds all available model endpoints indexed by tier.
type ModelRegistry struct {
	endpoints map[TaskComplexity]*ModelEndpoint
	client    *http.Client
}

// NewModelRegistry creates a registry with default Swarm fleet configuration.
func NewModelRegistry() *ModelRegistry {
	r := &ModelRegistry{
		endpoints: make(map[TaskComplexity]*ModelEndpoint),
		client: &http.Client{
			Timeout: 180 * time.Second,
		},
	}

	// Default fleet — dual Blackwell on swarmrails.
	r.Register(&ModelEndpoint{
		Name:      "SwarmSignal-9B",
		ModelID:   "swarmsignal-9b",
		BaseURL:   "http://localhost:8081",
		Tier:      Fast,
		MaxTokens: 2048,
		VRAM_GB:   23,
		TokPerSec: 165,
	})
	r.Register(&ModelEndpoint{
		Name:      "SwarmCapitalMarkets-27B",
		ModelID:   "swarmcapmarkets-27b",
		BaseURL:   "http://localhost:8082",
		Tier:      Analysis,
		MaxTokens: 4096,
		VRAM_GB:   93,
		TokPerSec: 88,
	})

	return r
}

// Register adds or replaces a model endpoint for a tier.
func (r *ModelRegistry) Register(endpoint *ModelEndpoint) {
	r.endpoints[endpoint.Tier] = endpoint
}

// Select returns the model endpoint for a given complexity tier.
// Falls back to Analysis tier if the requested tier is unavailable.
func (r *ModelRegistry) Select(tier TaskComplexity) (*ModelEndpoint, error) {
	if ep, ok := r.endpoints[tier]; ok {
		return ep, nil
	}
	// Fallback: premium → analysis → fast.
	if tier == Premium {
		if ep, ok := r.endpoints[Analysis]; ok {
			return ep, nil
		}
	}
	return nil, fmt.Errorf("no model registered for tier %s", tier)
}

// List returns all registered model endpoints.
func (r *ModelRegistry) List() []*ModelEndpoint {
	var out []*ModelEndpoint
	for _, ep := range r.endpoints {
		out = append(out, ep)
	}
	return out
}

// Health checks if a model endpoint is responding.
func (r *ModelRegistry) Health(tier TaskComplexity) error {
	ep, err := r.Select(tier)
	if err != nil {
		return err
	}
	resp, err := r.client.Get(ep.BaseURL + "/health")
	if err != nil {
		return fmt.Errorf("health check %s: %w", ep.Name, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("health check %s: status %d", ep.Name, resp.StatusCode)
	}
	return nil
}

// ChatMessage is a single message in the OpenAI-compatible chat format.
type ChatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// ChatRequest is the vLLM OpenAI-compatible completion request.
type ChatRequest struct {
	Model       string        `json:"model"`
	Messages    []ChatMessage `json:"messages"`
	MaxTokens   int           `json:"max_tokens"`
	Temperature float64       `json:"temperature"`
}

// ChatResponse is the vLLM OpenAI-compatible completion response.
type ChatResponse struct {
	Choices []struct {
		Message ChatMessage `json:"message"`
	} `json:"choices"`
}

// Complete sends a chat completion request to the model endpoint.
// Expects structured JSON output from the model.
func (ep *ModelEndpoint) Complete(messages []ChatMessage) (json.RawMessage, error) {
	req := ChatRequest{
		Model:       ep.ModelID,
		Messages:    messages,
		MaxTokens:   ep.MaxTokens,
		Temperature: 0.3, // Low temperature for structured output.
	}

	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	resp, err := http.Post(
		ep.BaseURL+"/v1/chat/completions",
		"application/json",
		bytes.NewReader(body),
	)
	if err != nil {
		return nil, fmt.Errorf("post to %s: %w", ep.BaseURL, err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("model error %d: %s", resp.StatusCode, string(respBody))
	}

	var chatResp ChatResponse
	if err := json.Unmarshal(respBody, &chatResp); err != nil {
		return nil, fmt.Errorf("unmarshal response: %w", err)
	}

	if len(chatResp.Choices) == 0 {
		return nil, fmt.Errorf("empty response from %s", ep.Name)
	}

	// Return the raw content as JSON (the model should output structured JSON).
	return json.RawMessage(chatResp.Choices[0].Message.Content), nil
}
