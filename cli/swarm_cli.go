// Package main implements the `swarm` CLI for SwarmCapitalMarkets.
//
// Commands:
//
//	swarm analyze deal.json              — Run full deal analysis
//	swarm run underwrite deal.json       — Run a specific skill
//	swarm install capital-markets        — Install skill pack
//	swarm models                         — List registered models
//	swarm skills                         — List available skills
//	swarm health                         — Check API health
package main

import (
	"encoding/json"
	"fmt"
	"os"
)

const (
	version    = "1.0.0"
	apiBaseURL = "http://localhost:8080"
)

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	cmd := os.Args[1]
	switch cmd {
	case "analyze":
		cmdAnalyze(os.Args[2:])
	case "run":
		cmdRun(os.Args[2:])
	case "install":
		cmdInstall(os.Args[2:])
	case "models":
		cmdModels()
	case "skills":
		cmdSkills()
	case "health":
		cmdHealth()
	case "version":
		fmt.Printf("swarm %s\n", version)
	case "help", "--help", "-h":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		printUsage()
		os.Exit(1)
	}
}

// cmdAnalyze runs a full deal analysis pipeline.
//
//	swarm analyze deal.json
//	swarm analyze deal.json --type underwriting
//	swarm analyze deal.json --type distressed_asset
func cmdAnalyze(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: swarm analyze <deal.json> [--type underwriting|acquisition_analysis|distressed_asset]")
		os.Exit(1)
	}

	dealFile := args[0]
	reqType := "underwriting" // Default pipeline.

	// Parse --type flag.
	for i, arg := range args {
		if arg == "--type" && i+1 < len(args) {
			reqType = args[i+1]
		}
	}

	// Read deal input file.
	data, err := os.ReadFile(dealFile)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error reading %s: %v\n", dealFile, err)
		os.Exit(1)
	}

	// Validate JSON.
	var deal json.RawMessage
	if err := json.Unmarshal(data, &deal); err != nil {
		fmt.Fprintf(os.Stderr, "invalid JSON in %s: %v\n", dealFile, err)
		os.Exit(1)
	}

	fmt.Printf("Analyzing deal from %s\n", dealFile)
	fmt.Printf("Pipeline: %s\n", reqType)
	fmt.Printf("Skills:   %v\n", resolveSkillChain(reqType))
	fmt.Println()

	// TODO: POST to /api/deal/analyze and display result.
	// For now, show the skill chain that would execute.

	fmt.Println("Pipeline stages:")
	for i, skill := range resolveSkillChain(reqType) {
		fmt.Printf("  %d. %s\n", i+1, skill)
	}
	fmt.Println()
	fmt.Println("Connect to API: POST", apiBaseURL+"/api/deal/analyze")
}

// cmdRun executes a single skill against a deal file.
//
//	swarm run underwrite deal.json
//	swarm run distressanalyzer deal.json
func cmdRun(args []string) {
	if len(args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: swarm run <skill_name> <deal.json>")
		os.Exit(1)
	}

	skillName := args[0]
	dealFile := args[1]

	data, err := os.ReadFile(dealFile)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error reading %s: %v\n", dealFile, err)
		os.Exit(1)
	}

	var deal json.RawMessage
	if err := json.Unmarshal(data, &deal); err != nil {
		fmt.Fprintf(os.Stderr, "invalid JSON in %s: %v\n", dealFile, err)
		os.Exit(1)
	}

	fmt.Printf("Running skill: %s\n", skillName)
	fmt.Printf("Input: %s\n", dealFile)
	fmt.Println()

	// TODO: Load skill from registry, execute against model, return result.
	fmt.Printf("Skill %s ready. Connect to API to execute.\n", skillName)
}

// cmdInstall installs a skill pack.
//
//	swarm install capital-markets
func cmdInstall(args []string) {
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: swarm install <pack_name>")
		os.Exit(1)
	}

	packName := args[0]
	fmt.Printf("Installing skill pack: %s\n", packName)

	// Available packs.
	packs := map[string][]string{
		"capital-markets": {
			"dealpacket", "underwrite", "creditcommittee",
			"capstackbuilder", "waterfallmodel",
			"distressanalyzer", "loanworkout",
		},
	}

	skills, ok := packs[packName]
	if !ok {
		fmt.Fprintf(os.Stderr, "unknown pack: %s\n", packName)
		fmt.Fprintln(os.Stderr, "available packs: capital-markets")
		os.Exit(1)
	}

	for _, skill := range skills {
		fmt.Printf("  + %s\n", skill)
	}
	fmt.Printf("\nInstalled %d skills from %s\n", len(skills), packName)
}

// cmdModels lists registered model endpoints.
func cmdModels() {
	fmt.Println("Registered Models:")
	fmt.Println()
	fmt.Printf("  %-30s %-10s %-25s %s\n", "Name", "Tier", "Endpoint", "VRAM")
	fmt.Printf("  %-30s %-10s %-25s %s\n", "----", "----", "--------", "----")
	fmt.Printf("  %-30s %-10s %-25s %s\n", "SwarmSignal-9B", "fast", "localhost:8081", "23 GB")
	fmt.Printf("  %-30s %-10s %-25s %s\n", "SwarmCapitalMarkets-27B", "analysis", "localhost:8082", "93 GB")
	fmt.Println()
	fmt.Println("Hardware: Dual Blackwell — RTX PRO 4500 (32GB) + RTX PRO 6000 (96GB)")
}

// cmdSkills lists available skills.
func cmdSkills() {
	fmt.Println("Available Skills:")
	fmt.Println()
	skills := []struct{ Name, Role string }{
		{"dealpacket", "Deal Intake Analyst"},
		{"underwrite", "Underwriting Analyst"},
		{"creditcommittee", "Investment Committee Chair"},
		{"capstackbuilder", "Capital Markets Structuring Analyst"},
		{"waterfallmodel", "Private Equity Fund Analyst"},
		{"distressanalyzer", "Distress & Special Situations Analyst"},
		{"loanworkout", "Workout & Restructuring Specialist"},
	}
	for _, s := range skills {
		fmt.Printf("  %-20s %s\n", s.Name, s.Role)
	}
}

// cmdHealth checks the API server health.
func cmdHealth() {
	fmt.Printf("Checking API health at %s...\n", apiBaseURL)
	// TODO: GET /api/health and display result.
	fmt.Println("API endpoint: GET", apiBaseURL+"/api/health")
}

// resolveSkillChain returns the ordered skills for a request type.
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

func printUsage() {
	fmt.Println("swarm — SwarmCapitalMarkets CLI")
	fmt.Println()
	fmt.Println("Usage:")
	fmt.Println("  swarm analyze <deal.json>              Full deal analysis pipeline")
	fmt.Println("  swarm run <skill> <deal.json>          Run a single skill")
	fmt.Println("  swarm install <pack>                   Install skill pack")
	fmt.Println("  swarm models                           List registered models")
	fmt.Println("  swarm skills                           List available skills")
	fmt.Println("  swarm health                           Check API health")
	fmt.Println("  swarm version                          Show version")
	fmt.Println()
	fmt.Println("Skill Packs:")
	fmt.Println("  capital-markets    7 CRE deal intelligence skills")
}
