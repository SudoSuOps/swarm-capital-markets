/**
 * SkillDetail — Full skill detail drawer/panel.
 *
 * Triggered when a skill card is clicked.
 * Slides in from the right as an overlay drawer.
 *
 * Shows:
 *   - Skill name, role, version
 *   - Full description
 *   - Supported models
 *   - CLI usage examples
 *   - API usage examples
 *   - Input/output JSON examples
 *   - Install instructions
 *   - "Try this skill" button → opens SkillPreview
 *
 * Design:
 *   - Overlay backdrop: rgba(10,14,20,0.8)
 *   - Drawer: background var(--bg), border-left var(--border)
 *   - Width: 520px desktop, full mobile
 */

import React from 'react';
import type { SkillData } from './skills_page';

export type Skill = SkillData; // Re-export for convenience.

interface SkillDetailProps {
  skill: SkillData;
  onClose: () => void;
  onTry: () => void;
}

export function SkillDetail({ skill, onClose, onTry }: SkillDetailProps) {
  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(10,14,20,0.8)',
          zIndex: 200,
        }}
      />

      {/* Drawer */}
      <div style={{
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        width: '520px',
        maxWidth: '100vw',
        background: 'var(--bg)',
        borderLeft: '1px solid var(--border)',
        zIndex: 201,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 20px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--bg-2)',
          position: 'sticky',
          top: 0,
          zIndex: 1,
        }}>
          <div>
            <div style={{
              fontSize: '1.1rem',
              fontWeight: 700,
              fontFamily: 'var(--mono)',
              color: 'var(--text)',
            }}>
              {skill.displayName}
            </div>
            <div style={{
              fontSize: '0.72rem',
              color: 'var(--text-3)',
              marginTop: 2,
            }}>
              v{skill.version} &middot; {skill.role}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-2)',
              fontFamily: 'var(--mono)',
              fontSize: '0.78rem',
              padding: '4px 12px',
              cursor: 'pointer',
            }}
          >
            &times; close
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '24px 20px', flex: 1 }}>

          {/* Description */}
          <DetailSection title="Description">
            <p style={{
              color: 'var(--text-2)',
              fontSize: '0.85rem',
              lineHeight: 1.7,
              fontFamily: 'var(--sans)',
            }}>
              {skill.description}
            </p>
          </DetailSection>

          {/* Model & Category */}
          <DetailSection title="Specifications">
            <SpecRow label="Model" value={skill.model} />
            <SpecRow label="Tier" value={skill.modelTier} />
            <SpecRow label="Category" value={skill.category} />
            <SpecRow label="Complexity" value={skill.complexity} />
            <SpecRow label="Domain" value={skill.domain} />
          </DetailSection>

          {/* CLI Usage */}
          <DetailSection title="CLI Usage">
            <CodeBlock title="Install">
              $ {skill.installCmd}
            </CodeBlock>
            <CodeBlock title="Run">
              $ {skill.runCmd}
            </CodeBlock>
          </DetailSection>

          {/* API Usage */}
          <DetailSection title="API Usage">
            <CodeBlock title="Endpoint">
              {skill.apiEndpoint}
            </CodeBlock>
            <div style={{
              fontSize: '0.78rem',
              color: 'var(--text-2)',
              fontFamily: 'var(--sans)',
              marginTop: 8,
            }}>
              Send a JSON deal input as the request body. Returns structured JSON output validated against the skill schema.
            </div>
          </DetailSection>

          {/* Example Input */}
          <DetailSection title="Example Input">
            <CodeBlock title="deal.json" lang="json">
              {skill.inputExample}
            </CodeBlock>
          </DetailSection>

          {/* Example Output */}
          <DetailSection title="Example Output">
            <CodeBlock title="response.json" lang="json">
              {skill.outputExample}
            </CodeBlock>
          </DetailSection>

          {/* Install Panel */}
          <DetailSection title="Installation">
            <div style={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              padding: 16,
            }}>
              <div style={{ marginBottom: 12 }}>
                <div style={{
                  fontSize: '0.68rem',
                  color: 'var(--text-3)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  marginBottom: 6,
                }}>
                  CLI
                </div>
                <code style={{
                  fontSize: '0.82rem',
                  color: 'var(--accent)',
                }}>
                  {skill.installCmd}
                </code>
              </div>
              <div>
                <div style={{
                  fontSize: '0.68rem',
                  color: 'var(--text-3)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  marginBottom: 6,
                }}>
                  API
                </div>
                <code style={{
                  fontSize: '0.82rem',
                  color: 'var(--primary)',
                }}>
                  POST /api/skills/install
                </code>
                <pre style={{
                  fontSize: '0.75rem',
                  color: 'var(--text-2)',
                  marginTop: 6,
                }}>
                  {`{ "skill": "${skill.id}" }`}
                </pre>
              </div>
            </div>
          </DetailSection>
        </div>

        {/* Footer actions */}
        <div style={{
          padding: '16px 20px',
          borderTop: '1px solid var(--border)',
          background: 'var(--bg-2)',
          display: 'flex',
          gap: 12,
          position: 'sticky',
          bottom: 0,
        }}>
          <button className="btn-primary" onClick={onTry} style={{ flex: 1 }}>
            Try this skill
          </button>
          <a
            href={skill.docsUrl}
            className="btn-outline"
            style={{ flex: 1, textAlign: 'center' }}
          >
            View Docs
          </a>
        </div>
      </div>
    </>
  );
}

// ─── Sub-components ─────────────────────────────────────────────────────────

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{
        fontSize: '0.68rem',
        color: 'var(--text-3)',
        textTransform: 'uppercase',
        letterSpacing: '0.08em',
        fontWeight: 700,
        marginBottom: 10,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function SpecRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      padding: '6px 0',
      borderBottom: '1px solid var(--border)',
      fontSize: '0.82rem',
    }}>
      <span style={{ color: 'var(--text-2)' }}>{label}</span>
      <span style={{ color: 'var(--text)', fontWeight: 600, fontFamily: 'var(--mono)' }}>{value}</span>
    </div>
  );
}

function CodeBlock({ title, lang, children }: { title: string; lang?: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: 'var(--code-bg)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      overflow: 'hidden',
      marginBottom: 8,
    }}>
      <div style={{
        padding: '6px 12px',
        background: 'var(--surface)',
        borderBottom: '1px solid var(--border)',
        fontSize: '0.68rem',
        color: 'var(--text-3)',
        display: 'flex',
        justifyContent: 'space-between',
      }}>
        <span>{title}</span>
        {lang && <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{lang}</span>}
      </div>
      <pre style={{
        padding: '10px 14px',
        fontSize: '0.78rem',
        lineHeight: 1.6,
        color: 'var(--text-2)',
        overflowX: 'auto',
        whiteSpace: 'pre-wrap',
        margin: 0,
      }}>
        {children}
      </pre>
    </div>
  );
}
