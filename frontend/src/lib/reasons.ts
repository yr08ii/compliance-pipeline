/** Controlled vocabulary for decision reason codes.
 *
 *  Shared rather than local to the decision panel, because the code an analyst
 *  picks is later read back on the case board and in exports. Keeping the
 *  labels in one place is what stops a screen from showing the raw
 *  STRUCTURING_CONFIRMED to someone who never saw the picker. */

export const TRUE_REASONS: [string, string][] = [
  ["STRUCTURING_CONFIRMED", "Structuring — amounts split to stay under thresholds"],
  ["UNEXPLAINED_ACTIVITY", "Merchant could not explain the activity"],
  ["MCC_MISMATCH", "Activity inconsistent with the declared business"],
  ["REFUND_ABUSE", "Refunds used to move value"],
  ["SUSPECTED_LAUNDERING", "Pattern consistent with laundering"],
  ["OTHER_CONFIRMED", "Other — described in notes"],
];

export const FALSE_REASONS: [string, string][] = [
  ["SEASONAL_PROMOTION", "Seasonal or promotional surge"],
  ["VERIFIED_BUSINESS_EXPANSION", "Genuine business growth, verified"],
  ["LEGITIMATE_LARGE_SALE", "One-off legitimate large transaction"],
  ["KNOWN_CUSTOMER_PATTERN", "Known and expected customer behaviour"],
  ["DATA_QUALITY", "Data quality issue, not merchant behaviour"],
  ["OTHER_CLEARED", "Other — described in notes"],
];

const LABELS = new Map([...TRUE_REASONS, ...FALSE_REASONS]);

/** Falls back to the code itself: a reason recorded before a rename should
 *  still be legible rather than rendering blank. */
export function reasonLabel(code: string): string {
  return LABELS.get(code) ?? code;
}
