/**
 * Shared option lists for subscription/intro-offer modals.
 *
 * `INTRO_DURATION_OPTIONS` is identical in IntroOffersModal and
 * PriceMultiplierPanel. `SUBSCRIPTION_PERIOD_OPTIONS` is currently only
 * consumed by SubscriptionFormModal but lives here for symmetry — both
 * lists are Apple-defined enums and edited together.
 */

import type { IntroOfferDuration, SubscriptionPeriod } from "@/types";

export const INTRO_DURATION_OPTIONS: {
  value: IntroOfferDuration;
  label: string;
}[] = [
  { value: "THREE_DAYS", label: "3 days" },
  { value: "ONE_WEEK", label: "1 week" },
  { value: "TWO_WEEKS", label: "2 weeks" },
  { value: "ONE_MONTH", label: "1 month" },
  { value: "TWO_MONTHS", label: "2 months" },
  { value: "THREE_MONTHS", label: "3 months" },
  { value: "SIX_MONTHS", label: "6 months" },
  { value: "ONE_YEAR", label: "1 year" },
];

export const SUBSCRIPTION_PERIOD_OPTIONS: {
  value: SubscriptionPeriod;
  label: string;
}[] = [
  { value: "ONE_WEEK", label: "1 week" },
  { value: "ONE_MONTH", label: "1 month" },
  { value: "TWO_MONTHS", label: "2 months" },
  { value: "THREE_MONTHS", label: "3 months" },
  { value: "SIX_MONTHS", label: "6 months" },
  { value: "ONE_YEAR", label: "1 year" },
];
