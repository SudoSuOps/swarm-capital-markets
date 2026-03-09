/**
 * SkillsGrid — Responsive grid layout for skill cards.
 *
 * Responsive breakpoints (matches swarmandbee.ai):
 *   Desktop: 3 columns  (minmax 300px)
 *   Tablet:  2 columns  (auto-fill)
 *   Mobile:  1 column   (stacked)
 *
 * Uses CSS Grid with auto-fill for natural responsiveness.
 * Grid class: .skill-grid from the existing design system.
 */

import React from 'react';
import { SkillCard } from './skill_card';
import type { SkillData } from './skills_page';

interface SkillsGridProps {
  skills: SkillData[];
  onSelect: (skill: SkillData) => void;
  onTry: (skill: SkillData) => void;
}

export function SkillsGrid({ skills, onSelect, onTry }: SkillsGridProps) {
  if (skills.length === 0) {
    return (
      <div style={{
        textAlign: 'center',
        padding: '60px 24px',
        color: 'var(--text-3)',
      }}>
        <div style={{
          fontSize: '1.5rem',
          fontWeight: 800,
          fontFamily: 'var(--sans)',
          color: 'var(--text-2)',
          marginBottom: 8,
        }}>
          No skills found
        </div>
        <p style={{ fontSize: '0.85rem', fontFamily: 'var(--sans)' }}>
          Try adjusting your filters or search query.
        </p>
      </div>
    );
  }

  return (
    <>
      {/* Results count */}
      <div style={{
        fontSize: '0.72rem',
        color: 'var(--text-3)',
        fontFamily: 'var(--mono)',
        marginBottom: 16,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
      }}>
        {skills.length} skill{skills.length !== 1 ? 's' : ''} available
      </div>

      {/* Grid — matches .skill-grid from existing site */}
      <div className="skill-grid">
        {skills.map(skill => (
          <SkillCard
            key={skill.id}
            skill={skill}
            onSelect={onSelect}
            onTry={onTry}
          />
        ))}
      </div>
    </>
  );
}
