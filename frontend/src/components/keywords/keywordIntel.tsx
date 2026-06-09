import { Badge, Tooltip } from "@mantine/core";

const DAY_MS = 24 * 60 * 60 * 1000;
const KEYWORD_INTEL_STALE_AFTER_MS = 14 * DAY_MS;

export type KeywordIntelKind = "fresh" | "stale" | "missing";

export interface KeywordIntelState {
  kind: KeywordIntelKind;
  label: string;
  color: "blue" | "yellow" | "gray";
  variant: "light" | "outline";
  tooltip: string;
}

function relativeTimeFromNow(iso: string, now: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown time";

  const diffSec = Math.round((date.getTime() - now) / 1000);
  const abs = Math.abs(diffSec);
  const fmt = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

  if (abs < 60) return fmt.format(diffSec, "second");
  if (abs < 3600) return fmt.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return fmt.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 30) return fmt.format(Math.round(diffSec / 86400), "day");
  if (abs < 86400 * 365) {
    return fmt.format(Math.round(diffSec / (86400 * 30)), "month");
  }
  return fmt.format(Math.round(diffSec / (86400 * 365)), "year");
}

export function getKeywordIntelState(
  popularity: number | null | undefined,
  updatedAt: string | null | undefined,
  now = Date.now(),
): KeywordIntelState {
  if (popularity == null) {
    return {
      kind: "missing",
      label: "No intel",
      color: "gray",
      variant: "outline",
      tooltip: "No cached keyword intel is available for this term yet.",
    };
  }

  if (!updatedAt) {
    return {
      kind: "stale",
      label: `${popularity} stale`,
      color: "yellow",
      variant: "outline",
      tooltip: `Last cached keyword intel score: ${popularity}. Refresh time is unknown.`,
    };
  }

  const updated = new Date(updatedAt);
  if (Number.isNaN(updated.getTime())) {
    return {
      kind: "stale",
      label: `${popularity} stale`,
      color: "yellow",
      variant: "outline",
      tooltip: `Last cached keyword intel score: ${popularity}. Refresh time is invalid.`,
    };
  }

  const ageMs = now - updated.getTime();
  if (ageMs > KEYWORD_INTEL_STALE_AFTER_MS) {
    return {
      kind: "stale",
      label: `${popularity} stale`,
      color: "yellow",
      variant: "outline",
      tooltip: `Last cached keyword intel score: ${popularity}. Updated ${relativeTimeFromNow(updatedAt, now)}.`,
    };
  }

  return {
    kind: "fresh",
    label: String(popularity),
    color: "blue",
    variant: "light",
    tooltip: `Keyword intel score: ${popularity}. Updated ${relativeTimeFromNow(updatedAt, now)}.`,
  };
}

interface KeywordIntelBadgeProps {
  popularity: number | null | undefined;
  updatedAt: string | null | undefined;
}

export default function KeywordIntelBadge({
  popularity,
  updatedAt,
}: KeywordIntelBadgeProps) {
  const intel = getKeywordIntelState(popularity, updatedAt);

  return (
    <Tooltip label={intel.tooltip} withArrow openDelay={300}>
      <Badge
        size="xs"
        radius="sm"
        color={intel.color}
        variant={intel.variant}
        style={{ textTransform: "none", whiteSpace: "nowrap" }}
      >
        {intel.label}
      </Badge>
    </Tooltip>
  );
}
