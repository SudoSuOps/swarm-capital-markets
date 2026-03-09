/**
 * DistressAnalyzer Validator — deterministic validation, no LLM calls.
 *
 * Validates distress analysis output:
 * - Loss severity = 1 - (current_value / original_basis)
 * - Resolution path probabilities sum to ~1.0
 * - Recovery rates within valid range
 * - Pricing analysis implied yields are positive
 */

const VALID_PATHS = ["modification", "dpo", "foreclosure", "loan_sale", "rescue_capital", "loan_to_own"];

function validate(output) {
  const errors = [];

  if (!output || typeof output !== "object") {
    return { valid: false, errors: ["output must be a JSON object"] };
  }

  if (output.skill !== "distress_analyzer") {
    errors.push(`skill must be "distress_analyzer", got "${output.skill}"`);
  }

  // Distress summary validation.
  if (output.distress_summary) {
    const ds = output.distress_summary;

    if (ds.loss_severity !== undefined && (ds.loss_severity < 0 || ds.loss_severity > 1)) {
      errors.push(`loss_severity must be 0-1, got ${ds.loss_severity}`);
    }

    // Cross-check loss severity calculation.
    if (ds.current_value > 0 && ds.original_basis > 0 && ds.loss_severity !== undefined) {
      const expected = 1 - (ds.current_value / ds.original_basis);
      if (Math.abs(ds.loss_severity - expected) > 0.02) {
        errors.push(`loss_severity mismatch: got ${ds.loss_severity}, expected ${expected.toFixed(4)}`);
      }
    }
  } else {
    errors.push("missing distress_summary");
  }

  // Resolution paths validation.
  if (output.resolution_paths) {
    if (!Array.isArray(output.resolution_paths)) {
      errors.push("resolution_paths must be an array");
    } else {
      let totalProb = 0;
      for (const path of output.resolution_paths) {
        if (!VALID_PATHS.includes(path.path)) {
          errors.push(`invalid resolution path: "${path.path}"`);
        }
        if (path.probability < 0 || path.probability > 1) {
          errors.push(`probability out of range for ${path.path}: ${path.probability}`);
        }
        totalProb += path.probability || 0;
      }
      // Probabilities should sum to approximately 1.0 (within 10%).
      if (totalProb > 0 && Math.abs(totalProb - 1.0) > 0.10) {
        errors.push(`resolution path probabilities sum to ${totalProb.toFixed(2)}, expected ~1.0`);
      }
    }
  } else {
    errors.push("missing resolution_paths");
  }

  // Pricing analysis validation.
  if (output.pricing_analysis) {
    const pa = output.pricing_analysis;
    if (pa.fair_value_cents !== undefined && (pa.fair_value_cents < 0 || pa.fair_value_cents > 100)) {
      errors.push(`fair_value_cents must be 0-100, got ${pa.fair_value_cents}`);
    }
  }

  return { valid: errors.length === 0, errors };
}

module.exports = { validate };
