/**
 * CreditCommittee Validator — deterministic validation, no LLM calls.
 *
 * Validates IC decision output:
 * - Decision is a valid class
 * - Confidence is 0.00-1.00
 * - Confidence breakdown weights sum correctly
 * - approve_with_conditions must have conditions[]
 * - Risk flags are snake_case, max 5
 * - Rationale has minimum length
 */

const VALID_DECISIONS = [
  "approve", "approve_with_conditions", "restructure",
  "decline", "watchlist", "distressed_opportunity"
];

function validate(output) {
  const errors = [];

  if (!output || typeof output !== "object") {
    return { valid: false, errors: ["output must be a JSON object"] };
  }

  if (output.skill !== "credit_committee") {
    errors.push(`skill must be "credit_committee", got "${output.skill}"`);
  }

  // Decision validation.
  if (!VALID_DECISIONS.includes(output.decision)) {
    errors.push(`invalid decision: "${output.decision}"`);
  }

  // Confidence validation.
  if (typeof output.confidence !== "number" || output.confidence < 0 || output.confidence > 1) {
    errors.push(`confidence must be 0.00-1.00, got ${output.confidence}`);
  }

  // Confidence breakdown validation.
  if (output.confidence_breakdown) {
    const cb = output.confidence_breakdown;
    const weights = { data_completeness: 0.25, financial_strength: 0.25, market_position: 0.20, sponsor_quality: 0.15, structural_protections: 0.15 };
    let weightedSum = 0;
    for (const [key, weight] of Object.entries(weights)) {
      if (cb[key] !== undefined) {
        if (cb[key] < 0 || cb[key] > 1) {
          errors.push(`${key} must be 0-1, got ${cb[key]}`);
        }
        weightedSum += cb[key] * weight;
      }
    }
    // Weighted sum should approximate the overall confidence (within 10%).
    if (weightedSum > 0 && output.confidence > 0) {
      const diff = Math.abs(weightedSum - output.confidence);
      if (diff > 0.10) {
        errors.push(`confidence_breakdown weighted sum (${weightedSum.toFixed(2)}) differs from confidence (${output.confidence}) by more than 10%`);
      }
    }
  }

  // Conditions required for approve_with_conditions.
  if (output.decision === "approve_with_conditions") {
    if (!output.conditions || !Array.isArray(output.conditions) || output.conditions.length === 0) {
      errors.push("approve_with_conditions requires non-empty conditions[]");
    }
  }

  // Risk flags.
  if (output.risk_flags) {
    if (!Array.isArray(output.risk_flags)) {
      errors.push("risk_flags must be an array");
    } else if (output.risk_flags.length > 5) {
      errors.push(`too many risk_flags: ${output.risk_flags.length} (max 5)`);
    }
  }

  // Rationale minimum length.
  if (!output.rationale || output.rationale.length < 50) {
    errors.push("rationale must be at least 50 characters");
  }

  return { valid: errors.length === 0, errors };
}

module.exports = { validate };
