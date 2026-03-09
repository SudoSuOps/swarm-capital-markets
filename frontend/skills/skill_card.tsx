/**
 * SkillCard — Individual skill card in the marketplace grid.
 *
 * Displays:
 *   - Skill name (monospace, bold)
 *   - Short description
 *   - Category badge
 *   - Model tier indicator (edge/cluster/blackwell)
 *   - Install button
 *   - "Try it" link
 *
 * Design: matches .skill-card from swarmandbee.ai/swarmskills
 *   - background: var(--surface)
 *   - border: 1px solid var(--border)
 *   - hover: border-color → var(--accent)
 *   - padding: 22px 20px
 */

import React, { useState } from 'react';
import type { SkillData } from './skills_page';

interface SkillCardProps {
  skill: SkillData;
  onSelect: (skill: SkillData) => void;
  onTry: (skill: SkillData) => void;
}

// Model tier → CSS class mapping (matches existing site).
const TIER_CLASS: Record<string, string> = {
  edge: 'edge',
  cluster: 'cluster',
  blackwell: 'blackwell',
};

// Model tier → display label.
const TIER_LABEL: Record<string, string> = {
  edge: 'edge',
  cluster: 'cluster',
  blackwell: 'blackwell',
};

export function SkillCard({ skill, onSelect, onTry }: SkillCardProps) {
  const [installed, setInstalled] = useState(false);

  const handleInstall = (e: React.MouseEvent) => {
    e.stopPropagation();
    setInstalled(true);
    // Copy install command to clipboard.
    navigator.clipboard?.writeText(skill.installCmd);
  };

  const handleTry = (e: React.MouseEvent) => {
    e.stopPropagation();
    onTry(skill);
  };

  return (
    <div
      className="skill-card"
      onClick={() => onSelect(skill)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onSelect(skill)}
      style={{ cursor: 'pointer' }}
    >
      {/* Skill name */}
      <div className="skill-name">{skill.displayName}</div>

      {/* Description */}
      <div className="skill-desc">{skill.description}</div>

      {/* Model info */}
      <div style={{
        fontSize: '0.75rem',
        color: 'var(--text-3)',
        fontFamily: 'var(--mono)',
        marginBottom: 14,
      }}>
        Model: <span style={{ color: 'var(--text-2)' }}>{skill.model}</span>
      </div>

      {/* Install command preview */}
      <div style={{
        background: 'var(--code-bg)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        padding: '6px 10px',
        fontSize: '0.72rem',
        fontFamily: 'var(--mono)',
        color: 'var(--text-2)',
        marginBottom: 14,
      }}>
        <span style={{ color: 'var(--accent)' }}>$</span>{' '}
        <span style={{ color: 'var(--text)' }}>{skill.installCmd}</span>
      </div>

      {/* Footer: tier badge + actions */}
      <div className="skill-footer">
        <span className={`skill-tier ${TIER_CLASS[skill.modelTier]}`}>
          {TIER_LABEL[skill.modelTier]} &middot; {skill.model.match(/\d+B/)?.[0] || '27B'}
        </span>

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="install-btn"
            onClick={handleTry}
            style={{ fontSize: '0.70rem' }}
          >
            try it
          </button>
          <button
            className="install-btn"
            onClick={handleInstall}
            style={installed ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
          >
            {installed ? 'copied' : 'install'}
          </button>
        </div>
      </div>
    </div>
  );
}
