/**
 * SwarmCapitalMarkets — Skill Output Validators
 * ==================================================
 * Deterministic validation for each skill's JSON output.
 * No LLM calls — pure structural and numeric checks.
 */

// --- Helpers ---

function checkRequired(obj, fields) {
  return fields
    .filter((f) => obj[f] === undefined || obj[f] === null)
    .map((f) => `missing required field: ${f}`);
}

function checkType(obj, field, type) {
  if (obj[field] === undefined) return null;
  if (type === 'array') return Array.isArray(obj[field]) ? null : `${field} must be array`;
  if (type === 'object') return typeof obj[field] === 'object' && !Array.isArray(obj[field]) ? null : `${field} must be object`;
  return typeof obj[field] === type ? null : `${field} must be ${type}`;
}

function checkEnum(obj, field, values) {
  if (obj[field] === undefined) return null;
  return values.includes(obj[field]) ? null : `${field} must be one of: ${values.join(', ')}`;
}

function checkRange(obj, field, min, max) {
  if (obj[field] === undefined || typeof obj[field] !== 'number') return null;
  if (obj[field] < min || obj[field] > max) return `${field} out of range [${min}, ${max}]: ${obj[field]}`;
  return null;
}

function collect(...checks) {
  return checks.filter(Boolean);
}

// --- Validators ---

export function validateDealPacket(output) {
  const errors = [
    ...checkRequired(output, ['skill', 'status', 'property', 'financials', 'derived_metrics']),
    checkEnum(output, 'status', ['complete', 'incomplete', 'flagged']),
    checkType(output, 'property', 'object'),
    checkType(output, 'financials', 'object'),
    checkType(output, 'derived_metrics', 'object'),
    checkType(output, 'missing_fields', 'array'),
    checkType(output, 'sanity_flags', 'array'),
  ].filter(Boolean);

  if (output.property) {
    errors.push(
      ...collect(
        checkEnum(output.property, 'asset_type', ['office', 'multifamily', 'industrial', 'retail', 'hospitality', 'mixed_use']),
        checkRange(output.property, 'occupancy', 0, 1),
      ),
    );
  }

  if (output.derived_metrics) {
    errors.push(
      ...collect(
        checkRange(output.derived_metrics, 'cap_rate', 0, 0.30),
        checkRange(output.derived_metrics, 'dscr', 0, 5),
        checkRange(output.derived_metrics, 'ltv', 0, 1.5),
        checkRange(output.derived_metrics, 'debt_yield', 0, 0.30),
      ),
    );
  }

  return { valid: errors.length === 0, errors };
}

export function validateUnderwrite(output) {
  const errors = [
    ...checkRequired(output, ['skill', 'loan_sizing', 'underwriting_metrics', 'risk_flags']),
    checkType(output, 'loan_sizing', 'object'),
    checkType(output, 'underwriting_metrics', 'object'),
    checkType(output, 'sensitivity_matrix', 'object'),
    checkType(output, 'risk_flags', 'array'),
    checkRange(output, 'risk_score', 1, 10),
  ].filter(Boolean);

  if (output.loan_sizing) {
    errors.push(
      ...collect(
        checkEnum(output.loan_sizing, 'binding_constraint', ['ltv', 'dscr', 'debt_yield']),
        checkRange(output.loan_sizing, 'max_loan', 0, 10_000_000_000),
      ),
    );
  }

  if (output.underwriting_metrics) {
    errors.push(
      ...collect(
        checkRange(output.underwriting_metrics, 'dscr', 0, 5),
        checkRange(output.underwriting_metrics, 'ltv', 0, 1.5),
        checkRange(output.underwriting_metrics, 'debt_yield', 0, 0.30),
        checkRange(output.underwriting_metrics, 'break_even_occupancy', 0, 1.5),
      ),
    );
  }

  return { valid: errors.length === 0, errors };
}

export function validateCreditCommittee(output) {
  const decisions = ['approve', 'approve_with_conditions', 'restructure', 'decline', 'watchlist', 'distressed_opportunity'];

  const errors = [
    ...checkRequired(output, ['skill', 'decision', 'confidence', 'risk_flags', 'rationale']),
    checkEnum(output, 'decision', decisions),
    checkRange(output, 'confidence', 0, 1),
    checkType(output, 'risk_flags', 'array'),
    checkType(output, 'conditions', 'array'),
  ].filter(Boolean);

  if (output.confidence_breakdown) {
    errors.push(
      ...collect(
        checkRange(output.confidence_breakdown, 'data_completeness', 0, 1),
        checkRange(output.confidence_breakdown, 'financial_strength', 0, 1),
        checkRange(output.confidence_breakdown, 'market_position', 0, 1),
        checkRange(output.confidence_breakdown, 'sponsor_quality', 0, 1),
        checkRange(output.confidence_breakdown, 'structural_protections', 0, 1),
      ),
    );
  }

  return { valid: errors.length === 0, errors };
}

export function validateDistressAnalyzer(output) {
  const errors = [
    ...checkRequired(output, ['skill', 'distress_summary', 'resolution_paths', 'recommendation']),
    checkType(output, 'distress_summary', 'object'),
    checkType(output, 'resolution_paths', 'array'),
    checkType(output, 'risk_flags', 'array'),
  ].filter(Boolean);

  if (output.distress_summary) {
    errors.push(
      ...collect(
        checkRange(output.distress_summary, 'loss_severity', 0, 1),
        checkType(output.distress_summary, 'distress_triggers', 'array'),
      ),
    );
  }

  if (output.pricing_analysis) {
    errors.push(
      ...collect(
        checkRange(output.pricing_analysis, 'fair_value_cents', 0, 1.2),
      ),
    );
  }

  return { valid: errors.length === 0, errors };
}

export function validateCapStackBuilder(output) {
  const errors = [
    ...checkRequired(output, ['skill', 'total_capitalization', 'layers', 'blended_metrics']),
    checkType(output, 'layers', 'array'),
    checkType(output, 'blended_metrics', 'object'),
    checkType(output, 'risk_flags', 'array'),
  ].filter(Boolean);

  if (output.blended_metrics) {
    errors.push(
      ...collect(
        checkRange(output.blended_metrics, 'wacc', 0, 0.30),
        checkRange(output.blended_metrics, 'senior_ltv', 0, 1),
        checkRange(output.blended_metrics, 'combined_ltv', 0, 1),
      ),
    );
  }

  if (Array.isArray(output.layers)) {
    const validLayers = ['senior', 'mezzanine', 'preferred_equity', 'lp_equity', 'gp_equity'];
    output.layers.forEach((layer, i) => {
      const e = checkEnum(layer, 'layer', validLayers);
      if (e) errors.push(`layers[${i}]: ${e}`);
      const r = checkRange(layer, 'pct_of_stack', 0, 1);
      if (r) errors.push(`layers[${i}]: ${r}`);
    });
  }

  return { valid: errors.length === 0, errors };
}

export function validateWaterfallModel(output) {
  const errors = [
    ...checkRequired(output, ['skill', 'equity_invested', 'waterfall_tiers', 'return_metrics']),
    checkType(output, 'equity_invested', 'object'),
    checkType(output, 'waterfall_tiers', 'array'),
    checkType(output, 'return_metrics', 'object'),
  ].filter(Boolean);

  if (output.return_metrics) {
    errors.push(
      ...collect(
        checkRange(output.return_metrics, 'equity_multiple', 0, 10),
        checkRange(output.return_metrics, 'lp_multiple', 0, 10),
        checkRange(output.return_metrics, 'gp_multiple', 0, 50),
      ),
    );
  }

  if (output.gp_economics) {
    errors.push(
      ...collect(
        checkRange(output.gp_economics, 'effective_ownership', 0, 1),
      ),
    );
  }

  return { valid: errors.length === 0, errors };
}

export function validateLoanWorkout(output) {
  const strategies = ['modification', 'ab_split', 'dpo', 'forbearance', 'foreclosure'];

  const errors = [
    ...checkRequired(output, ['skill', 'current_loan', 'workout_options', 'recommended_strategy']),
    checkType(output, 'current_loan', 'object'),
    checkType(output, 'workout_options', 'array'),
    checkType(output, 'risk_flags', 'array'),
  ].filter(Boolean);

  if (output.current_loan) {
    errors.push(
      ...collect(
        checkEnum(output.current_loan, 'recourse', ['full', 'partial', 'non_recourse']),
        checkEnum(output.current_loan, 'loan_type', ['balance_sheet', 'cmbs', 'agency', 'bank']),
        checkRange(output.current_loan, 'current_dscr', 0, 5),
        checkRange(output.current_loan, 'current_ltv', 0, 3),
      ),
    );
  }

  if (Array.isArray(output.workout_options)) {
    output.workout_options.forEach((opt, i) => {
      const e = checkEnum(opt, 'strategy', strategies);
      if (e) errors.push(`workout_options[${i}]: ${e}`);
      const r = checkRange(opt, 'recovery_rate', 0, 1.5);
      if (r) errors.push(`workout_options[${i}]: ${r}`);
    });
  }

  return { valid: errors.length === 0, errors };
}

// --- Dispatch ---

export const VALIDATORS = {
  deal_packet: validateDealPacket,
  underwrite: validateUnderwrite,
  credit_committee: validateCreditCommittee,
  distress_analyzer: validateDistressAnalyzer,
  cap_stack_builder: validateCapStackBuilder,
  waterfall_model: validateWaterfallModel,
  loan_workout: validateLoanWorkout,
};

export function validateSkillOutput(name, output) {
  const validator = VALIDATORS[name];
  if (!validator) return { valid: true, errors: ['no validator for skill'] };
  return validator(output);
}
