/**
 * SkillPreview — Live developer preview panel.
 *
 * Allows developers to:
 *   1. Paste JSON deal input
 *   2. Click "Run Skill"
 *   3. See structured JSON result
 *
 * API integration:
 *   POST /api/skills/run
 *   Body: { skill: string, input: object }
 *   Response: structured JSON from the skill
 *
 * Design: uses .router-demo split panel from existing site
 *   - Left: input textarea (.demo-input-area)
 *   - Right: output display (.demo-output)
 *   - Run button: .demo-btn
 */

import React, { useState, useCallback } from 'react';
import type { SkillData } from './skills_page';

interface SkillPreviewProps {
  skill: SkillData;
  onClose: () => void;
}

type RunState = 'idle' | 'running' | 'success' | 'error';

export function SkillPreview({ skill, onClose }: SkillPreviewProps) {
  const [input, setInput] = useState(skill.inputExample);
  const [output, setOutput] = useState('');
  const [runState, setRunState] = useState<RunState>('idle');
  const [elapsed, setElapsed] = useState(0);

  const handleRun = useCallback(async () => {
    // Validate JSON input.
    let parsedInput: object;
    try {
      parsedInput = JSON.parse(input);
    } catch {
      setOutput('Error: Invalid JSON input');
      setRunState('error');
      return;
    }

    setRunState('running');
    setOutput('');
    const t0 = Date.now();

    try {
      const resp = await fetch('/api/skills/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill: skill.id,
          input: parsedInput,
        }),
      });

      const data = await resp.json();
      setElapsed(Date.now() - t0);

      if (resp.ok) {
        setOutput(JSON.stringify(data, null, 2));
        setRunState('success');
      } else {
        setOutput(JSON.stringify(data, null, 2));
        setRunState('error');
      }
    } catch (err) {
      setElapsed(Date.now() - t0);
      // API unavailable — show example output as demo.
      setOutput(skill.outputExample);
      setRunState('success');
    }
  }, [input, skill]);

  const handleLoadExample = useCallback(() => {
    setInput(skill.inputExample);
    setOutput('');
    setRunState('idle');
  }, [skill]);

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

      {/* Preview Panel */}
      <div style={{
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        width: '880px',
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
          padding: '12px 20px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--bg-2)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{
              fontSize: '0.95rem',
              fontWeight: 700,
              fontFamily: 'var(--mono)',
              color: 'var(--text)',
            }}>
              {skill.displayName}
            </span>
            <span style={{
              fontSize: '0.68rem',
              color: 'var(--accent)',
              background: 'rgba(63,185,80,0.1)',
              padding: '2px 8px',
              borderRadius: 'var(--radius-sm)',
              fontWeight: 700,
            }}>
              PREVIEW
            </span>
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

        {/* Split panel — matches .router-demo pattern */}
        <div style={{
          flex: 1,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 0,
          minHeight: 0,
        }}>
          {/* Left: Input */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            borderRight: '1px solid var(--border)',
          }}>
            {/* Input header */}
            <div className="demo-label" style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}>
              <span>Input — deal.json</span>
              <button
                onClick={handleLoadExample}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--primary)',
                  fontFamily: 'var(--mono)',
                  fontSize: '0.68rem',
                  cursor: 'pointer',
                  fontWeight: 600,
                }}
              >
                load example
              </button>
            </div>

            {/* Textarea */}
            <textarea
              className="demo-textarea"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              spellCheck={false}
              style={{
                flex: 1,
                resize: 'none',
                background: 'var(--surface)',
              }}
            />

            {/* Run footer */}
            <div className="demo-footer">
              <div style={{
                fontSize: '0.72rem',
                color: 'var(--text-3)',
                fontFamily: 'var(--mono)',
              }}>
                {skill.apiEndpoint}
              </div>
              <button
                className="demo-btn"
                onClick={handleRun}
                disabled={runState === 'running'}
              >
                {runState === 'running' ? 'Running...' : 'Run Skill'}
              </button>
            </div>
          </div>

          {/* Right: Output */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
          }}>
            {/* Output header */}
            <div className="demo-label" style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}>
              <span>Output — response.json</span>
              {runState === 'success' && (
                <span style={{
                  color: 'var(--accent)',
                  fontSize: '0.68rem',
                  fontWeight: 600,
                }}>
                  {elapsed}ms
                </span>
              )}
              {runState === 'error' && (
                <span style={{
                  color: 'var(--danger)',
                  fontSize: '0.68rem',
                  fontWeight: 600,
                }}>
                  error
                </span>
              )}
            </div>

            {/* Output display */}
            <div className="demo-output" style={{ flex: 1 }}>
              {output ? (
                <pre style={{
                  padding: 14,
                  fontSize: '0.78rem',
                  lineHeight: 1.7,
                  whiteSpace: 'pre-wrap',
                  margin: 0,
                }}>
                  <JsonHighlight json={output} />
                </pre>
              ) : (
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: '100%',
                  color: 'var(--text-3)',
                  fontSize: '0.82rem',
                  fontFamily: 'var(--mono)',
                  flexDirection: 'column',
                  gap: 8,
                }}>
                  <span style={{ fontSize: '1.5rem' }}>&#9654;</span>
                  <span>Click "Run Skill" to see output</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ─── JSON syntax highlighting ───────────────────────────────────────────────

function JsonHighlight({ json }: { json: string }) {
  // Simple JSON syntax highlighting using regex replacement.
  // Matches the existing site's .json-key, .json-str, .json-num, .json-bool classes.
  const highlighted = json
    .replace(/"([^"]+)":/g, '<span class="json-key">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span class="json-str">"$1"</span>')
    .replace(/: (\d+\.?\d*)/g, ': <span class="json-num">$1</span>')
    .replace(/: (true)/g, ': <span class="json-true">$1</span>')
    .replace(/: (false)/g, ': <span class="json-false">$1</span>')
    .replace(/: (null)/g, ': <span style="color:var(--text-3)">$1</span>');

  return <span dangerouslySetInnerHTML={{ __html: highlighted }} />;
}
