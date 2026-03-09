/**
 * SkillFilters — Filter bar for the skills marketplace.
 *
 * Filters:
 *   - Category:   Capital Markets, Medical, Pharma, Aviation, Operations, Research
 *   - Model:      SwarmCapitalMarkets-27B, SwarmCurator-9B, etc.
 *   - Complexity:  Low, Medium, High
 *   - Domain:     capital_markets, medical, pharma, aviation, operations, research
 *   - Search:     Free-text search across name + description
 *
 * Design: horizontal filter strip below the hero section
 *   - background: var(--bg-2) with top/bottom borders
 *   - Filter buttons use .mode-btn pattern from the existing site
 *   - Search input matches .demo-textarea styling
 */

import React from 'react';
import type { SkillData } from './skills_page';

export interface FilterState {
  category: string;
  model: string;
  complexity: string;
  domain: string;
  search: string;
}

interface SkillFiltersProps {
  filters: FilterState;
  onChange: (filters: FilterState) => void;
  skills: SkillData[];  // Used to derive available filter options.
}

// Domain display names.
const DOMAINS: Record<string, string> = {
  all: 'All Domains',
  capital_markets: 'Capital Markets',
  medical: 'Medical',
  pharma: 'Pharma',
  aviation: 'Aviation',
  operations: 'Operations',
  research: 'Research',
};

const COMPLEXITIES: Record<string, string> = {
  all: 'All',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
};

export function SkillFilters({ filters, onChange, skills }: SkillFiltersProps) {
  // Derive unique models from available skills.
  const models = ['all', ...new Set(skills.map(s => s.model))];

  const update = (key: keyof FilterState, value: string) => {
    onChange({ ...filters, [key]: value });
  };

  const activeCount = [
    filters.category !== 'all',
    filters.model !== 'all',
    filters.complexity !== 'all',
    filters.domain !== 'all',
    filters.search !== '',
  ].filter(Boolean).length;

  return (
    <div style={{
      background: 'var(--bg-2)',
      borderTop: '1px solid var(--border)',
      borderBottom: '1px solid var(--border)',
      padding: '16px 24px',
      position: 'sticky',
      top: 48, // Below topbar
      zIndex: 50,
    }}>
      <div style={{ maxWidth: 'var(--max-w)', margin: '0 auto' }}>

        {/* Top row: search + clear */}
        <div style={{
          display: 'flex',
          gap: 12,
          alignItems: 'center',
          marginBottom: 12,
        }}>
          {/* Search input */}
          <div style={{
            flex: 1,
            position: 'relative',
          }}>
            <input
              type="text"
              value={filters.search}
              onChange={(e) => update('search', e.target.value)}
              placeholder="Search skills..."
              style={{
                width: '100%',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius)',
                color: 'var(--text)',
                fontFamily: 'var(--mono)',
                fontSize: '0.82rem',
                padding: '8px 14px',
                outline: 'none',
              }}
            />
          </div>

          {/* Active filter count + clear */}
          {activeCount > 0 && (
            <button
              onClick={() => onChange({
                category: 'all', model: 'all', complexity: 'all', domain: 'all', search: '',
              })}
              style={{
                background: 'transparent',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-2)',
                fontFamily: 'var(--mono)',
                fontSize: '0.72rem',
                padding: '6px 12px',
                cursor: 'pointer',
              }}
            >
              clear ({activeCount})
            </button>
          )}
        </div>

        {/* Filter buttons row */}
        <div style={{
          display: 'flex',
          gap: 8,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}>
          {/* Domain filter */}
          <FilterGroup
            label="Domain"
            options={DOMAINS}
            value={filters.domain}
            onChange={(v) => update('domain', v)}
          />

          <Divider />

          {/* Complexity filter */}
          <FilterGroup
            label="Complexity"
            options={COMPLEXITIES}
            value={filters.complexity}
            onChange={(v) => update('complexity', v)}
          />

          <Divider />

          {/* Model filter */}
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{
              fontSize: '0.68rem',
              color: 'var(--text-3)',
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
              marginRight: 4,
            }}>
              Model
            </span>
            {models.map(model => (
              <button
                key={model}
                onClick={() => update('model', model)}
                className={`mode-btn ${filters.model === model ? 'active' : ''}`}
                style={{ padding: '4px 10px', fontSize: '0.70rem' }}
              >
                {model === 'all' ? 'All' : model.replace('Swarm', '').replace('-', ' ')}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Sub-components ─────────────────────────────────────────────────────────

function FilterGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: Record<string, string>;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
      <span style={{
        fontSize: '0.68rem',
        color: 'var(--text-3)',
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '0.04em',
        marginRight: 4,
      }}>
        {label}
      </span>
      {Object.entries(options).map(([key, display]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={`mode-btn ${value === key ? 'active' : ''}`}
          style={{ padding: '4px 10px', fontSize: '0.70rem' }}
        >
          {display}
        </button>
      ))}
    </div>
  );
}

function Divider() {
  return (
    <div style={{
      width: 1,
      height: 20,
      background: 'var(--border)',
      margin: '0 8px',
    }} />
  );
}
