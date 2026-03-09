/**
 * SwarmSkills Marketplace — Page Root
 *
 * Route: /skills
 *
 * Orchestrates the full skills marketplace:
 *   Hero → Filters → Skills Grid → Detail Panel → Developer Preview
 *
 * Design system: matches swarmandbee.ai exactly
 *   --primary: #58a6ff    --surface: #161b22    --border: #30363d
 *   --accent: #3fb950     --bg: #0a0e14         --text: #e6edf3
 *   Fonts: JetBrains Mono (mono), Inter (sans)
 *
 * API integration:
 *   GET  /api/skills/list    — all available skills
 *   GET  /api/skills/{id}    — skill detail + schema
 *   POST /api/skills/run     — execute skill with input
 */

import React, { useState, useEffect, useCallback } from 'react';
import { SkillFilters, type FilterState } from './skill_filters';
import { SkillsGrid } from './skills_grid';
import { SkillDetail, type Skill } from './skill_detail';
import { SkillPreview } from './skill_preview';

// ─── Skill data type ────────────────────────────────────────────────────────

export interface SkillData {
  id: string;
  name: string;
  displayName: string;
  description: string;
  category: string;
  domain: string;
  model: string;
  modelTier: 'edge' | 'cluster' | 'blackwell';
  complexity: 'low' | 'medium' | 'high';
  version: string;
  installCmd: string;
  runCmd: string;
  apiEndpoint: string;
  role: string;
  inputExample: string;
  outputExample: string;
  docsUrl: string;
}

// ─── Preloaded skills ───────────────────────────────────────────────────────

const PRELOADED_SKILLS: SkillData[] = [
  {
    id: 'dealpacket',
    name: 'deal_packet',
    displayName: 'DealPacket.ai',
    description: 'Parse and normalize raw deal inputs into institutional-grade deal packets. Detects missing fields, flags data quality issues, computes derived metrics.',
    category: 'Capital Markets',
    domain: 'capital_markets',
    model: 'SwarmCapitalMarkets-27B',
    modelTier: 'blackwell',
    complexity: 'low',
    version: '1.0',
    installCmd: 'swarm install capital-markets',
    runCmd: 'swarm run deal_packet deal.json',
    apiEndpoint: 'POST /api/skills/dealpacket',
    role: 'Deal Intake Analyst',
    inputExample: JSON.stringify({
      asset_type: 'industrial',
      market: 'Dallas-Fort Worth',
      purchase_price: 82000000,
      noi: 5900000,
      occupancy: 0.94,
      debt_request: { ltv_requested: 0.65, interest_rate: 0.0625, term_years: 10, amortization_years: 30 }
    }, null, 2),
    outputExample: JSON.stringify({
      skill: 'deal_packet',
      status: 'complete',
      derived_metrics: { cap_rate: 0.0720, dscr: 1.34, debt_yield: 0.1108, ltv: 0.65 },
      sanity_flags: [],
      ready_for_underwriting: true
    }, null, 2),
    docsUrl: '/docs/skills/dealpacket',
  },
  {
    id: 'underwrite',
    name: 'underwrite',
    displayName: 'SwarmUnderwrite.ai',
    description: 'Full underwriting analysis — DSCR sizing, loan constraints, 5×5 sensitivity matrix, risk scoring. Identifies binding constraint and sizes maximum loan.',
    category: 'Capital Markets',
    domain: 'capital_markets',
    model: 'SwarmCapitalMarkets-27B',
    modelTier: 'blackwell',
    complexity: 'high',
    version: '1.0',
    installCmd: 'swarm install capital-markets',
    runCmd: 'swarm run underwrite deal.json',
    apiEndpoint: 'POST /api/skills/underwrite',
    role: 'Underwriting Analyst',
    inputExample: JSON.stringify({
      purchase_price: 75000000,
      noi: 4800000,
      interest_rate: 0.0625,
      amortization_years: 30,
      ltv_limit: 0.75,
      dscr_requirement: 1.25,
      debt_yield_min: 0.08
    }, null, 2),
    outputExample: JSON.stringify({
      skill: 'underwrite',
      loan_sizing: { max_loan: 53300000, binding_constraint: 'dscr' },
      underwriting_metrics: { dscr: 1.34, ltv: 0.65, debt_yield: 0.1108 },
      risk_flags: ['exit_cap_sensitivity'],
      risk_score: 4
    }, null, 2),
    docsUrl: '/docs/skills/underwrite',
  },
  {
    id: 'creditcommittee',
    name: 'credit_committee',
    displayName: 'CreditCommittee.ai',
    description: 'Investment committee decision engine — approve, decline, restructure with confidence scoring, risk flags, conditions, and dissenting view.',
    category: 'Capital Markets',
    domain: 'capital_markets',
    model: 'SwarmCapitalMarkets-27B',
    modelTier: 'blackwell',
    complexity: 'high',
    version: '1.0',
    installCmd: 'swarm install capital-markets',
    runCmd: 'swarm run credit_committee deal.json',
    apiEndpoint: 'POST /api/skills/creditcommittee',
    role: 'Investment Committee Chair',
    inputExample: JSON.stringify({
      deal_id: 'dp-20260309-001',
      asset_type: 'industrial',
      purchase_price: 82000000,
      noi: 5900000,
      dscr: 1.34,
      ltv: 0.65,
      sponsor: { track_record_years: 15, aum: 200000000, coinvest_pct: 0.12 }
    }, null, 2),
    outputExample: JSON.stringify({
      skill: 'credit_committee',
      decision: 'approve_with_conditions',
      confidence: 0.84,
      risk_flags: ['exit_cap_sensitivity', 'refinancing_risk'],
      conditions: ['6-month interest reserve', 'DSCR lockbox at 1.15x']
    }, null, 2),
    docsUrl: '/docs/skills/creditcommittee',
  },
  {
    id: 'capstackbuilder',
    name: 'cap_stack_builder',
    displayName: 'CapStackBuilder.ai',
    description: 'Structure multi-layer capital stacks: senior debt, mezzanine, preferred equity, JV equity, GP co-invest. Calculates blended WACC and positive leverage test.',
    category: 'Capital Markets',
    domain: 'capital_markets',
    model: 'SwarmCapitalMarkets-27B',
    modelTier: 'blackwell',
    complexity: 'medium',
    version: '1.0',
    installCmd: 'swarm install capital-markets',
    runCmd: 'swarm run cap_stack_builder deal.json',
    apiEndpoint: 'POST /api/skills/capstackbuilder',
    role: 'Capital Markets Structuring Analyst',
    inputExample: JSON.stringify({
      purchase_price: 100000000,
      noi: 6500000,
      senior_ltv: 0.65,
      senior_rate: 0.0625,
      equity_pct: 0.25,
      sponsor: { coinvest_pct: 0.10 }
    }, null, 2),
    outputExample: JSON.stringify({
      skill: 'cap_stack_builder',
      total_capitalization: 100000000,
      blended_metrics: { wacc: 0.072, positive_leverage: true, senior_ltv: 0.65 },
      layers: [
        { layer: 'senior', amount: 65000000, cost: 0.0625, priority: 1 },
        { layer: 'mezzanine', amount: 10000000, cost: 0.11, priority: 2 },
        { layer: 'lp_equity', amount: 22500000, cost: 0.15, priority: 3 },
        { layer: 'gp_equity', amount: 2500000, cost: 0.20, priority: 4 }
      ]
    }, null, 2),
    docsUrl: '/docs/skills/capstackbuilder',
  },
  {
    id: 'waterfallmodel',
    name: 'waterfall_model',
    displayName: 'WaterfallModel.ai',
    description: 'Model PE distribution waterfalls — multi-tier IRR hurdles, GP catch-up, carried interest, promote structures. Scenario analysis at base/upside/downside.',
    category: 'Capital Markets',
    domain: 'capital_markets',
    model: 'SwarmCapitalMarkets-27B',
    modelTier: 'blackwell',
    complexity: 'high',
    version: '1.0',
    installCmd: 'swarm install capital-markets',
    runCmd: 'swarm run waterfall_model deal.json',
    apiEndpoint: 'POST /api/skills/waterfallmodel',
    role: 'Private Equity Fund Analyst',
    inputExample: JSON.stringify({
      equity_invested: { total: 28000000, lp: 25200000, gp: 2800000, gp_coinvest_pct: 0.10 },
      preferred_return: 0.08,
      promote_tiers: [
        { hurdle_irr: 0.08, split_lp: 0.80, split_gp: 0.20 },
        { hurdle_irr: 0.12, split_lp: 0.70, split_gp: 0.30 }
      ],
      hold_period_years: 5,
      exit_value: 105000000,
      annual_noi_cashflow: 5200000
    }, null, 2),
    outputExample: JSON.stringify({
      skill: 'waterfall_model',
      return_metrics: { project_irr: 0.142, lp_irr: 0.128, gp_irr: 0.312, equity_multiple: 1.82 },
      gp_economics: { promote_dollars: 3200000, effective_ownership: 0.234 }
    }, null, 2),
    docsUrl: '/docs/skills/waterfallmodel',
  },
  {
    id: 'distressanalyzer',
    name: 'distress_analyzer',
    displayName: 'DistressAnalyzer.ai',
    description: 'Evaluate distressed assets — models 6 resolution paths (modification, DPO, foreclosure, loan sale, rescue capital, loan-to-own) with recovery rates and pricing.',
    category: 'Capital Markets',
    domain: 'capital_markets',
    model: 'SwarmCapitalMarkets-27B',
    modelTier: 'blackwell',
    complexity: 'high',
    version: '1.0',
    installCmd: 'swarm install capital-markets',
    runCmd: 'swarm run distress_analyzer deal.json',
    apiEndpoint: 'POST /api/skills/distressanalyzer',
    role: 'Distress & Special Situations Analyst',
    inputExample: JSON.stringify({
      asset_type: 'office',
      loan_balance: 90000000,
      current_value: 55000000,
      original_basis: 120000000,
      noi: 3800000,
      occupancy: 0.62,
      months_in_distress: 14,
      special_servicing: true
    }, null, 2),
    outputExample: JSON.stringify({
      skill: 'distress_analyzer',
      distress_summary: { loss_severity: 0.542, distress_triggers: ['noi_decline', 'vacancy', 'maturity_default'] },
      resolution_paths: [
        { path: 'modification', probability: 0.25, recovery_rate: 0.72 },
        { path: 'dpo', probability: 0.30, recovery_rate: 0.65 },
        { path: 'foreclosure', probability: 0.20, recovery_rate: 0.55 },
        { path: 'loan_to_own', probability: 0.25, recovery_rate: 0.68 }
      ],
      pricing_analysis: { fair_value_cents: 68.5 }
    }, null, 2),
    docsUrl: '/docs/skills/distressanalyzer',
  },
  {
    id: 'loanworkout',
    name: 'loan_workout',
    displayName: 'LoanWorkout.ai',
    description: 'Model loan workouts — modification, A/B note split, discounted payoff, forbearance. NPV comparison of all paths against foreclosure BATNA baseline.',
    category: 'Capital Markets',
    domain: 'capital_markets',
    model: 'SwarmCapitalMarkets-27B',
    modelTier: 'blackwell',
    complexity: 'high',
    version: '1.0',
    installCmd: 'swarm install capital-markets',
    runCmd: 'swarm run loan_workout deal.json',
    apiEndpoint: 'POST /api/skills/loanworkout',
    role: 'Workout & Restructuring Specialist',
    inputExample: JSON.stringify({
      current_loan: { balance: 120000000, rate: 0.0575, current_dscr: 0.74, recourse: 'non_recourse', loan_type: 'cmbs' },
      property_value: 85000000,
      noi: 5100000,
      maturity_months: 6
    }, null, 2),
    outputExample: JSON.stringify({
      skill: 'loan_workout',
      recommended_strategy: 'dpo',
      npv_comparison: {
        modification_npv: 78500000,
        dpo_npv: 82400000,
        foreclosure_npv: 71200000,
        best_recovery: 'dpo'
      },
      risk_flags: ['cmbs_psa_constraint', 'borrower_walk_risk']
    }, null, 2),
    docsUrl: '/docs/skills/loanworkout',
  },
];

// ─── Page component ─────────────────────────────────────────────────────────

export function SkillsPage() {
  const [skills, setSkills] = useState<SkillData[]>(PRELOADED_SKILLS);
  const [filters, setFilters] = useState<FilterState>({
    category: 'all',
    model: 'all',
    complexity: 'all',
    domain: 'all',
    search: '',
  });
  const [selectedSkill, setSelectedSkill] = useState<SkillData | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  // Fetch skills from API (falls back to preloaded).
  useEffect(() => {
    fetch('/api/skills/list')
      .then(res => res.json())
      .then(data => {
        if (Array.isArray(data) && data.length > 0) {
          setSkills(data);
        }
      })
      .catch(() => {
        // API unavailable — use preloaded skills.
      });
  }, []);

  // Apply filters.
  const filteredSkills = skills.filter(skill => {
    if (filters.category !== 'all' && skill.category !== filters.category) return false;
    if (filters.model !== 'all' && skill.model !== filters.model) return false;
    if (filters.complexity !== 'all' && skill.complexity !== filters.complexity) return false;
    if (filters.domain !== 'all' && skill.domain !== filters.domain) return false;
    if (filters.search) {
      const q = filters.search.toLowerCase();
      return (
        skill.name.toLowerCase().includes(q) ||
        skill.displayName.toLowerCase().includes(q) ||
        skill.description.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const handleSelectSkill = useCallback((skill: SkillData) => {
    setSelectedSkill(skill);
    setShowPreview(false);
  }, []);

  const handleTrySkill = useCallback((skill: SkillData) => {
    setSelectedSkill(skill);
    setShowPreview(true);
  }, []);

  const handleCloseDetail = useCallback(() => {
    setSelectedSkill(null);
    setShowPreview(false);
  }, []);

  return (
    <div className="skills-page">
      {/* Hero */}
      <section className="skills-hero">
        <div className="skills-hero-inner">
          <div className="skills-badge">AI Skills Marketplace</div>
          <h1><span className="hero-accent">SwarmSkills</span></h1>
          <p className="hero-sub">
            Install specialized AI capabilities powered by Swarm models.
          </p>
          <p className="hero-tagline">
            SwarmSkills are reusable AI workflows that run on specialized models and sovereign compute.
          </p>

          {/* CLI example */}
          <div className="terminal" style={{ maxWidth: 520, margin: '0 auto 28px' }}>
            <div className="terminal-bar">
              <span className="terminal-dot red" />
              <span className="terminal-dot yellow" />
              <span className="terminal-dot green" />
              <span className="terminal-title">swarm-cli</span>
            </div>
            <div className="terminal-body">
              <span className="prompt">$</span> <span className="cmd">swarm install capital-markets</span>{'\n'}
              <span className="prompt">$</span> <span className="cmd">swarm run underwrite deal.json</span>
            </div>
          </div>

          <div className="hero-btns">
            <a href="#skills-grid" className="btn-primary">Browse Skills</a>
            <a href="#preview" className="btn-outline">Try a Skill</a>
          </div>
        </div>
      </section>

      {/* Filters */}
      <SkillFilters filters={filters} onChange={setFilters} skills={skills} />

      {/* Skills Grid */}
      <section className="section" id="skills-grid">
        <div className="section-inner">
          <SkillsGrid
            skills={filteredSkills}
            onSelect={handleSelectSkill}
            onTry={handleTrySkill}
          />
        </div>
      </section>

      {/* Detail Panel (drawer overlay) */}
      {selectedSkill && !showPreview && (
        <SkillDetail skill={selectedSkill} onClose={handleCloseDetail} onTry={() => setShowPreview(true)} />
      )}

      {/* Developer Preview */}
      {selectedSkill && showPreview && (
        <SkillPreview skill={selectedSkill} onClose={handleCloseDetail} />
      )}
    </div>
  );
}
