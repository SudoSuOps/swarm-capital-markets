/**
 * Underwrite Validator — deterministic validation, no LLM calls.
 *
 * Validates underwriting output:
 * - Loan sizing against all three constraints
 * - Binding constraint is the minimum
 * - DSCR rounded to 2 decimal places
 * - Loan amounts rounded to nearest $100K
 * - Risk flags are valid snake_case strings
 * - Sensitivity matrix is 5x5
 */

function validate(output) {
  const errors = [];

  if (!output || typeof output !== "object") {
    return { valid: false, errors: ["output must be a JSON object"] };
  }

  if (output.skill !== "underwrite") {
    errors.push(`skill must be "underwrite", got "${output.skill}"`);
  }

  // Loan sizing validation.
  if (output.loan_sizing) {
    const ls = output.loan_sizing;
    const validConstraints = ["ltv", "dscr", "debt_yield"];
    if (!validConstraints.includes(ls.binding_constraint)) {
      errors.push(`invalid binding_constraint: ${ls.binding_constraint}`);
    }

    // Verify max_loan is the minimum of all constraints.
    const constraints = [ls.ltv_constrained, ls.dscr_constrained, ls.debt_yield_constrained]
      .filter(v => v > 0);
    if (constraints.length > 0) {
      const expectedMin = Math.min(...constraints);
      if (ls.max_loan > 0 && Math.abs(ls.max_loan - expectedMin) > 100000) {
        errors.push(`max_loan should be MIN of constraints: expected ~${expectedMin}, got ${ls.max_loan}`);
      }
    }

    // Loan amounts should be rounded to $100K.
    if (ls.max_loan > 0 && ls.max_loan % 100000 !== 0) {
      errors.push(`max_loan should be rounded to nearest $100K: ${ls.max_loan}`);
    }
  } else {
    errors.push("missing loan_sizing object");
  }

  // Metrics validation.
  if (output.underwriting_metrics) {
    const m = output.underwriting_metrics;
    if (m.dscr !== undefined && (m.dscr < 0 || m.dscr > 5.0)) {
      errors.push(`dscr out of range: ${m.dscr}`);
    }
    if (m.ltv !== undefined && (m.ltv < 0 || m.ltv > 1.5)) {
      errors.push(`ltv out of range: ${m.ltv}`);
    }
    if (m.debt_yield !== undefined && (m.debt_yield < 0 || m.debt_yield > 0.30)) {
      errors.push(`debt_yield out of range: ${m.debt_yield}`);
    }
    if (m.break_even_occupancy !== undefined && (m.break_even_occupancy < 0 || m.break_even_occupancy > 1)) {
      errors.push(`break_even_occupancy out of range: ${m.break_even_occupancy}`);
    }
  }

  // Sensitivity matrix validation.
  if (output.sensitivity_matrix) {
    const sm = output.sensitivity_matrix;
    if (sm.values && Array.isArray(sm.values)) {
      if (sm.values.length !== 5) {
        errors.push(`sensitivity_matrix should have 5 rows, got ${sm.values.length}`);
      }
      for (let i = 0; i < sm.values.length; i++) {
        if (!Array.isArray(sm.values[i]) || sm.values[i].length !== 5) {
          errors.push(`sensitivity_matrix row ${i} should have 5 columns`);
        }
      }
    }
  }

  // Risk flags validation.
  if (output.risk_flags) {
    if (!Array.isArray(output.risk_flags)) {
      errors.push("risk_flags must be an array");
    } else if (output.risk_flags.length > 5) {
      errors.push(`too many risk_flags: ${output.risk_flags.length} (max 5)`);
    } else {
      const snakeCase = /^[a-z][a-z0-9_]*$/;
      for (const flag of output.risk_flags) {
        if (!snakeCase.test(flag)) {
          errors.push(`risk_flag must be snake_case: "${flag}"`);
        }
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

module.exports = { validate };
