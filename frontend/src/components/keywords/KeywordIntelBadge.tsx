import { Badge, Tooltip } from "@mantine/core";
import type { KeywordIntelSummary } from "@/components/keywords/keywordIntel";

interface KeywordIntelBadgeProps {
  loading?: boolean;
  unavailable?: boolean;
  summary: KeywordIntelSummary | null;
}

export default function KeywordIntelBadge({
  loading = false,
  unavailable = false,
  summary,
}: KeywordIntelBadgeProps) {
  if (loading) {
    return (
      <Badge size="sm" variant="light" color="gray" style={{ textTransform: "none" }}>
        Loading…
      </Badge>
    );
  }

  if (unavailable) {
    return (
      <Badge size="sm" variant="light" color="gray" style={{ textTransform: "none" }}>
        Unavailable
      </Badge>
    );
  }

  if (summary == null) {
    return null;
  }

  return (
    <Tooltip
      label={summary.detail}
      withArrow
      multiline
      w={280}
      openDelay={250}
    >
      <Badge
        size="sm"
        variant="light"
        color={summary.color}
        style={{ textTransform: "none", cursor: "default" }}
      >
        {summary.label}
      </Badge>
    </Tooltip>
  );
}
