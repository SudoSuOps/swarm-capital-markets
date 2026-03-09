// Package api provides the HTTP server for SwarmCapitalMarkets.
//
// Endpoints:
//
//	POST /api/deal/analyze  — Full deal analysis pipeline
//	GET  /api/health        — Service health check
//	GET  /api/models        — List registered models
//	GET  /api/skills        — List available skills
package api

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"
)

// Server holds the HTTP server and its dependencies.
type Server struct {
	Addr   string
	mux    *http.ServeMux
}

// NewServer creates an API server on the given address.
func NewServer(addr string) *Server {
	s := &Server{
		Addr: addr,
		mux:  http.NewServeMux(),
	}
	s.routes()
	return s
}

// Start begins listening for HTTP requests.
func (s *Server) Start() error {
	srv := &http.Server{
		Addr:         s.Addr,
		Handler:      s.mux,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 300 * time.Second, // Long timeout for LLM inference.
		IdleTimeout:  120 * time.Second,
	}
	log.Printf("SwarmCapitalMarkets API listening on %s", s.Addr)
	return srv.ListenAndServe()
}

// routes registers all HTTP endpoints.
func (s *Server) routes() {
	s.mux.HandleFunc("POST /api/deal/analyze", s.handleDealAnalyze)
	s.mux.HandleFunc("GET /api/health", s.handleHealth)
	s.mux.HandleFunc("GET /api/models", s.handleListModels)
	s.mux.HandleFunc("GET /api/skills", s.handleListSkills)
}

// handleHealth returns service status and model availability.
func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	resp := map[string]interface{}{
		"status":  "ok",
		"service": "swarm-capital-markets",
		"version": "1.0.0",
		"time":    time.Now().UTC().Format(time.RFC3339),
	}
	writeJSON(w, http.StatusOK, resp)
}

// handleListModels returns all registered model endpoints.
func (s *Server) handleListModels(w http.ResponseWriter, r *http.Request) {
	// TODO: Wire to ModelRegistry.List()
	models := []map[string]interface{}{
		{"name": "SwarmSignal-9B", "tier": "fast", "endpoint": "localhost:8081"},
		{"name": "SwarmCapitalMarkets-27B", "tier": "analysis", "endpoint": "localhost:8082"},
	}
	writeJSON(w, http.StatusOK, models)
}

// handleListSkills returns all available skills.
func (s *Server) handleListSkills(w http.ResponseWriter, r *http.Request) {
	// TODO: Wire to SkillRegistry.List()
	skills := []string{
		"dealpacket", "underwrite", "creditcommittee",
		"capstackbuilder", "waterfallmodel",
		"distressanalyzer", "loanworkout",
	}
	writeJSON(w, http.StatusOK, skills)
}

// writeJSON sends a JSON response with the given status code.
func writeJSON(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(data); err != nil {
		log.Printf("json encode error: %v", err)
	}
}

// writeError sends a JSON error response.
func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// Placeholder to prevent unused import warning.
var _ = fmt.Sprintf
