// Package router — skill registry discovers and manages SwarmSkills.
//
// Each skill lives in skills/{name}/ with:
//   - SKILL.md   — spec (YAML frontmatter + markdown body)
//   - schema.json — output validation schema
//   - validator.js — deterministic validator (optional)
//
// The registry loads all skills at startup and provides lookup, validation,
// and prompt construction for the router's skill chain execution.
package router

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// SkillSpec represents a loaded skill with its spec, schema, and system prompt.
type SkillSpec struct {
	Name         string          `json:"name"`
	Version      string          `json:"version"`
	Vertical     string          `json:"vertical"`
	Description  string          `json:"description"`
	Role         string          `json:"role"`
	SystemPrompt string          `json:"system_prompt"`
	Schema       json.RawMessage `json:"schema"`       // Output JSON Schema
	SkillDir     string          `json:"skill_dir"`
}

// SkillRegistry holds all discovered skills.
type SkillRegistry struct {
	skills  map[string]*SkillSpec
	baseDir string
}

// NewSkillRegistry creates a registry and discovers skills from the given directory.
func NewSkillRegistry(skillsDir string) (*SkillRegistry, error) {
	r := &SkillRegistry{
		skills:  make(map[string]*SkillSpec),
		baseDir: skillsDir,
	}

	entries, err := os.ReadDir(skillsDir)
	if err != nil {
		return nil, fmt.Errorf("read skills dir: %w", err)
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		skillName := entry.Name()
		spec, err := r.loadSkill(filepath.Join(skillsDir, skillName), skillName)
		if err != nil {
			// Log but don't fail — partial registry is acceptable.
			fmt.Fprintf(os.Stderr, "warn: skip skill %s: %v\n", skillName, err)
			continue
		}
		r.skills[normalizeSkillName(skillName)] = spec
	}

	return r, nil
}

// Get returns a skill by name. Names are normalized (underscores → no separator).
func (r *SkillRegistry) Get(name string) (*SkillSpec, error) {
	normalized := normalizeSkillName(name)
	if skill, ok := r.skills[normalized]; ok {
		return skill, nil
	}
	return nil, fmt.Errorf("skill not found: %s", name)
}

// List returns all registered skill names.
func (r *SkillRegistry) List() []string {
	var names []string
	for name := range r.skills {
		names = append(names, name)
	}
	return names
}

// loadSkill reads SKILL.md and schema.json from a skill directory.
func (r *SkillRegistry) loadSkill(dir, name string) (*SkillSpec, error) {
	// Read SKILL.md — required.
	skillMD, err := os.ReadFile(filepath.Join(dir, "SKILL.md"))
	if err != nil {
		return nil, fmt.Errorf("read SKILL.md: %w", err)
	}

	// Parse YAML frontmatter from SKILL.md.
	spec := &SkillSpec{
		Name:     name,
		SkillDir: dir,
	}
	spec.parseFrontmatter(string(skillMD))

	// Extract system prompt — everything after the frontmatter's "## System Prompt" section.
	spec.SystemPrompt = extractSystemPrompt(string(skillMD))

	// Read schema.json — optional but recommended.
	schemaPath := filepath.Join(dir, "schema.json")
	if data, err := os.ReadFile(schemaPath); err == nil {
		spec.Schema = json.RawMessage(data)
	}

	return spec, nil
}

// parseFrontmatter extracts YAML frontmatter fields from SKILL.md content.
// Expects format:  ---\nkey: value\n---
func (s *SkillSpec) parseFrontmatter(content string) {
	if !strings.HasPrefix(content, "---") {
		return
	}
	parts := strings.SplitN(content, "---", 3)
	if len(parts) < 3 {
		return
	}
	frontmatter := parts[1]
	for _, line := range strings.Split(frontmatter, "\n") {
		line = strings.TrimSpace(line)
		if kv := strings.SplitN(line, ":", 2); len(kv) == 2 {
			key := strings.TrimSpace(kv[0])
			val := strings.TrimSpace(strings.Trim(kv[1], "\""))
			switch key {
			case "name":
				s.Name = val
			case "version":
				s.Version = val
			case "vertical":
				s.Vertical = val
			case "description":
				s.Description = val
			case "role":
				s.Role = val
			}
		}
	}
}

// extractSystemPrompt pulls the system prompt section from SKILL.md content.
func extractSystemPrompt(content string) string {
	marker := "## System Prompt"
	idx := strings.Index(content, marker)
	if idx == -1 {
		return ""
	}
	rest := content[idx+len(marker):]

	// Find the next ## heading or end of content.
	nextSection := strings.Index(rest, "\n## ")
	if nextSection != -1 {
		rest = rest[:nextSection]
	}

	return strings.TrimSpace(rest)
}

// BuildPrompt constructs chat messages for a skill execution.
// Returns [system, user] messages ready for model.Complete().
func (s *SkillSpec) BuildPrompt(dealContext json.RawMessage) []ChatMessage {
	return []ChatMessage{
		{Role: "system", Content: s.SystemPrompt},
		{Role: "user", Content: string(dealContext)},
	}
}

// Validate checks the model output against the skill's JSON schema.
// Returns nil if no schema is defined (permissive mode).
func (s *SkillSpec) Validate(output json.RawMessage) error {
	if s.Schema == nil {
		return nil // No schema — skip validation.
	}

	// Basic structural validation: must be valid JSON.
	var parsed interface{}
	if err := json.Unmarshal(output, &parsed); err != nil {
		return fmt.Errorf("invalid JSON output: %w", err)
	}

	// TODO: Full JSON Schema validation via a Go library (e.g., santhosh-tekuri/jsonschema).
	// For now, structural validity is sufficient — the validator.js files handle
	// domain-specific checks in the Node.js skill runner.

	return nil
}

// normalizeSkillName converts directory names to registry keys.
// "cap_stack_builder" → "capstackbuilder", "credit_committee" → "creditcommittee"
func normalizeSkillName(name string) string {
	return strings.ReplaceAll(strings.ToLower(name), "_", "")
}
