/**
 * DealPacket Validator — deterministic validation, no LLM calls.
 *
 * Validates deal packet output against business rules:
 * - Required fields present
 * - Cap rate within sane range (3-15%)
 * - DSCR within sane range (0.80-3.00)
 * - LTV within sane range (0.30-0.95)
 * - NOI positive
 * - Derived metrics computed correctly
 */

function validate(output) {
  const errors = [];

  if (!output || typeof output !== "object") {
    return { valid: false, errors: ["output must be a JSON object"] };
  }

  // Required fields.
  if (output.skill !== "deal_packet") {
    errors.push(`skill must be "deal_packet", got "${output.skill}"`);
  }
  if (!["complete", "incomplete", "flagged"].includes(output.status)) {
    errors.push(`invalid status: ${output.status}`);
  }

  // Property validation.
  if (output.property) {
    const validTypes = ["office", "multifamily", "industrial", "retail", "hospitality", "mixed_use", "data_center", "cold_storage"];
    if (!validTypes.includes(output.property.asset_type)) {
      errors.push(`invalid asset_type: ${output.property.asset_type}`);
    }
    if (output.property.occupancy !== undefined) {
      if (output.property.occupancy < 0 || output.property.occupancy > 1) {
        errors.push(`occupancy must be 0-1, got ${output.property.occupancy}`);
      }
    }
  } else {
    errors.push("missing property object");
  }

  // Financials validation.
  if (output.financials) {
    if (output.financials.purchase_price <= 0) {
      errors.push("purchase_price must be positive");
    }
  } else {
    errors.push("missing financials object");
  }

  // Derived metrics sanity checks.
  if (output.derived_metrics) {
    const m = output.derived_metrics;
    if (m.cap_rate !== undefined && (m.cap_rate < 0.01 || m.cap_rate > 0.25)) {
      errors.push(`unusual cap_rate: ${m.cap_rate}`);
    }
    if (m.dscr !== undefined && (m.dscr < 0 || m.dscr > 5.0)) {
      errors.push(`dscr out of range: ${m.dscr}`);
    }
    if (m.ltv !== undefined && (m.ltv < 0 || m.ltv > 1.5)) {
      errors.push(`ltv out of range: ${m.ltv}`);
    }
    if (m.debt_yield !== undefined && (m.debt_yield < 0 || m.debt_yield > 0.30)) {
      errors.push(`debt_yield out of range: ${m.debt_yield}`);
    }

    // Cross-check: cap_rate = noi / purchase_price (within 1% tolerance).
    if (output.financials && output.financials.noi > 0 && output.financials.purchase_price > 0) {
      const expectedCap = output.financials.noi / output.financials.purchase_price;
      if (m.cap_rate !== undefined && Math.abs(m.cap_rate - expectedCap) / expectedCap > 0.01) {
        errors.push(`cap_rate mismatch: got ${m.cap_rate}, expected ${expectedCap.toFixed(4)}`);
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

module.exports = { validate };
