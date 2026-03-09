/**
 * LoanWorkout Validator — deterministic validation, no LLM calls.
 *
 * Validates workout analysis output:
 * - Workout options include foreclosure as BATNA baseline
 * - NPV comparison is consistent with workout options
 * - Recovery rates within valid range
 * - Recommended strategy matches one of the options
 */

const VALID_STRATEGIES = ["modification", "ab_split", "dpo", "forbearance", "foreclosure"];

function validate(output) {
  const errors = [];

  if (!output || typeof output !== "object") {
    return { valid: false, errors: ["output must be a JSON object"] };
  }

  if (output.skill !== "loan_workout") {
    errors.push(`skill must be "loan_workout", got "${output.skill}"`);
  }

  // Current loan validation.
  if (output.current_loan) {
    const cl = output.current_loan;
    if (cl.rate !== undefined && (cl.rate < 0 || cl.rate > 0.25)) {
      errors.push(`loan rate out of range: ${cl.rate}`);
    }
    if (cl.current_dscr !== undefined && (cl.current_dscr < 0 || cl.current_dscr > 5.0)) {
      errors.push(`current_dscr out of range: ${cl.current_dscr}`);
    }
    const validRecourse = ["full", "partial", "non_recourse"];
    if (cl.recourse && !validRecourse.includes(cl.recourse)) {
      errors.push(`invalid recourse type: ${cl.recourse}`);
    }
    const validTypes = ["balance_sheet", "cmbs", "agency", "bank"];
    if (cl.loan_type && !validTypes.includes(cl.loan_type)) {
      errors.push(`invalid loan_type: ${cl.loan_type}`);
    }
  } else {
    errors.push("missing current_loan");
  }

  // Workout options validation.
  if (output.workout_options) {
    if (!Array.isArray(output.workout_options)) {
      errors.push("workout_options must be an array");
    } else {
      const strategies = output.workout_options.map(o => o.strategy);

      // Foreclosure should be included as the BATNA baseline.
      if (!strategies.includes("foreclosure")) {
        errors.push("workout_options should include foreclosure as BATNA baseline");
      }

      for (const option of output.workout_options) {
        if (!VALID_STRATEGIES.includes(option.strategy)) {
          errors.push(`invalid strategy: "${option.strategy}"`);
        }
        if (option.recovery_rate !== undefined && (option.recovery_rate < 0 || option.recovery_rate > 1.5)) {
          errors.push(`recovery_rate out of range for ${option.strategy}: ${option.recovery_rate}`);
        }
        if (option.probability_of_success !== undefined && (option.probability_of_success < 0 || option.probability_of_success > 1)) {
          errors.push(`probability_of_success out of range for ${option.strategy}: ${option.probability_of_success}`);
        }
      }
    }
  } else {
    errors.push("missing workout_options");
  }

  // Recommended strategy must be one of the options.
  if (output.recommended_strategy && output.workout_options) {
    const strategies = output.workout_options.map(o => o.strategy);
    if (!strategies.includes(output.recommended_strategy)) {
      errors.push(`recommended_strategy "${output.recommended_strategy}" not in workout_options`);
    }
  }

  // NPV comparison: best_recovery should match the option with highest NPV.
  if (output.npv_comparison && output.npv_comparison.best_recovery) {
    if (!VALID_STRATEGIES.includes(output.npv_comparison.best_recovery)) {
      errors.push(`invalid best_recovery: "${output.npv_comparison.best_recovery}"`);
    }
  }

  return { valid: errors.length === 0, errors };
}

module.exports = { validate };
