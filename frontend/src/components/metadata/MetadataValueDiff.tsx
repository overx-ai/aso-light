import { Box, Text } from "@mantine/core";
import { diffWordsWithSpace } from "diff";

export type MetadataDiffSegmentKind = "unchanged" | "added" | "removed";

export interface MetadataDiffSegment {
  kind: MetadataDiffSegmentKind;
  text: string;
}

interface MetadataValueDiffProps {
  before: string | null | undefined;
  after: string | null | undefined;
  multiline?: boolean;
}

function normalizeValue(value: string | null | undefined): string {
  return value ?? "";
}

export function buildMetadataDiffSegments(
  before: string | null | undefined,
  after: string | null | undefined,
): MetadataDiffSegment[] {
  const oldValue = normalizeValue(before);
  const newValue = normalizeValue(after);

  if (oldValue === newValue) {
    return [{ kind: "unchanged", text: oldValue }];
  }

  return diffWordsWithSpace(oldValue, newValue)
    .filter((part) => part.value.length > 0)
    .map((part) => ({
      kind: part.added ? "added" : part.removed ? "removed" : "unchanged",
      text: part.value,
    }));
}

function segmentStyle(kind: MetadataDiffSegmentKind) {
  if (kind === "added") {
    return {
      backgroundColor: "var(--mantine-color-green-0)",
      color: "var(--mantine-color-green-9)",
      textDecoration: "none",
    };
  }

  if (kind === "removed") {
    return {
      backgroundColor: "var(--mantine-color-red-0)",
      color: "var(--mantine-color-red-9)",
      textDecoration: "line-through",
    };
  }

  return {
    backgroundColor: "transparent",
    color: "inherit",
    textDecoration: "none",
  };
}

export default function MetadataValueDiff({
  before,
  after,
  multiline = false,
}: MetadataValueDiffProps) {
  const oldValue = normalizeValue(before);
  const newValue = normalizeValue(after);
  const segments = buildMetadataDiffSegments(oldValue, newValue);
  const hasChanges = oldValue !== newValue;

  if (!hasChanges) {
    return (
      <Box>
        <Text size="xs" c="dimmed">
          No change
        </Text>
        <Text
          size="xs"
          c={oldValue.length === 0 ? "dimmed" : undefined}
          fs={oldValue.length === 0 ? "italic" : undefined}
          style={{ whiteSpace: multiline ? "pre-wrap" : "normal" }}
        >
          {oldValue.length > 0 ? oldValue : "empty"}
        </Text>
      </Box>
    );
  }

  return (
    <Text
      component="div"
      size="xs"
      style={{
        lineHeight: 1.55,
        whiteSpace: multiline ? "pre-wrap" : "normal",
        wordBreak: "break-word",
      }}
    >
      {segments.map((segment, index) => (
        <Box
          component="span"
          key={`${segment.kind}-${index}-${segment.text}`}
          px={segment.kind === "unchanged" ? 0 : 2}
          style={segmentStyle(segment.kind)}
        >
          {segment.text}
        </Box>
      ))}
    </Text>
  );
}
