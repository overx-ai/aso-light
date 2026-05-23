import type { ReplyTone, ReviewTheme } from "@/types";

export const REVIEW_THEME_LABELS: Record<ReviewTheme, string> = {
  bug: "Bug",
  performance: "Performance",
  feature_request: "Feature request",
  billing: "Billing",
  account: "Account",
  usability: "Usability",
  content: "Content",
  praise: "Praise",
  other: "General",
};

export const REVIEW_THEME_COLORS: Record<ReviewTheme, string> = {
  bug: "red",
  performance: "orange",
  feature_request: "blue",
  billing: "grape",
  account: "violet",
  usability: "cyan",
  content: "teal",
  praise: "green",
  other: "gray",
};

export const REVIEW_THEME_OPTIONS: { value: ReviewTheme; label: string }[] = [
  { value: "bug", label: REVIEW_THEME_LABELS.bug },
  { value: "performance", label: REVIEW_THEME_LABELS.performance },
  { value: "feature_request", label: REVIEW_THEME_LABELS.feature_request },
  { value: "billing", label: REVIEW_THEME_LABELS.billing },
  { value: "account", label: REVIEW_THEME_LABELS.account },
  { value: "usability", label: REVIEW_THEME_LABELS.usability },
  { value: "content", label: REVIEW_THEME_LABELS.content },
  { value: "praise", label: REVIEW_THEME_LABELS.praise },
  { value: "other", label: REVIEW_THEME_LABELS.other },
];

export const REVIEW_THEME_DEFAULT_TONE: Record<ReviewTheme, ReplyTone> = {
  bug: "apologetic",
  performance: "apologetic",
  feature_request: "appreciative",
  billing: "apologetic",
  account: "apologetic",
  usability: "neutral",
  content: "neutral",
  praise: "appreciative",
  other: "neutral",
};
