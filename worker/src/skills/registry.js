/**
 * SwarmCapitalMarkets — Skills Registry
 * ==================================================
 * Central registry for all capital markets skills.
 * Each skill: name, version, role, systemPrompt, examples.
 *
 * Endpoints:
 *   POST /skill/{name}     — Execute skill
 *   GET  /skills            — List all skills
 *   GET  /skill/{name}/spec — Get skill spec
 */

import { DEAL_PACKET } from './deal_packet.js';
import { UNDERWRITE } from './underwrite.js';
import { CREDIT_COMMITTEE } from './credit_committee.js';
import { DISTRESS_ANALYZER } from './distress_analyzer.js';
import { CAP_STACK_BUILDER } from './cap_stack_builder.js';
import { WATERFALL_MODEL } from './waterfall_model.js';
import { LOAN_WORKOUT } from './loan_workout.js';

export const SKILL_REGISTRY = {
  deal_packet: DEAL_PACKET,
  underwrite: UNDERWRITE,
  credit_committee: CREDIT_COMMITTEE,
  distress_analyzer: DISTRESS_ANALYZER,
  cap_stack_builder: CAP_STACK_BUILDER,
  waterfall_model: WATERFALL_MODEL,
  loan_workout: LOAN_WORKOUT,
};

/**
 * Execute a skill by name.
 * @param {string} name - Skill name from registry
 * @param {string} input - User input / deal description
 * @param {object} env - Runtime environment (AI bindings, etc.)
 * @returns {object} Skill output JSON
 */
export async function executeSkill(name, input, env) {
  const skill = SKILL_REGISTRY[name];
  if (!skill) {
    return { error: `Unknown skill: ${name}`, available: Object.keys(SKILL_REGISTRY) };
  }

  const messages = [
    { role: 'system', content: skill.systemPrompt },
    { role: 'user', content: input },
  ];

  const response = await env.AI.run(env.AI_MODEL || '@cf/qwen/qwen3-30b-a3b-fp8', {
    messages,
    max_tokens: 8192,
    temperature: 0.2,
  });

  let output = response.response || response;

  // Strip think blocks
  output = output.replace(/<think>[\s\S]*?<\/think>/g, '').trim();

  // Extract JSON from markdown fences
  const jsonMatch = output.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (jsonMatch) {
    output = jsonMatch[1].trim();
  }

  try {
    return JSON.parse(output);
  } catch {
    return { skill: name, raw_output: output, parse_error: true };
  }
}

/**
 * List all registered skills.
 * @returns {Array} Skill summaries
 */
export function listSkills() {
  return Object.values(SKILL_REGISTRY).map((s) => ({
    name: s.name,
    version: s.version,
    role: s.role,
    description: s.description,
  }));
}

/**
 * Get full spec for a skill.
 * @param {string} name - Skill name
 * @returns {object|null} Full skill spec or null
 */
export function getSkillSpec(name) {
  return SKILL_REGISTRY[name] || null;
}
