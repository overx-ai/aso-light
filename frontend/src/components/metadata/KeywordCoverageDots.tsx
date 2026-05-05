import { Box, Group, Stack, Text, Tooltip } from "@mantine/core";
import type { KeywordPlacement } from "@/types";

const PLACEMENT_COLOR: Record<KeywordPlacement, string> = {
  title: "green",
  subtitle: "orange",
  keywords: "yellow",
  none: "gray",
};

interface Props {
  placements: Array<{ locale: string; placement: KeywordPlacement }>;
}

/**
 * Renders one colored dot per locale where a tracked keyword appears in some
 * metadata field. Color encodes placement (title / subtitle / keywords).
 */
export default function KeywordCoverageDots({ placements }: Props) {
  if (placements.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        --
      </Text>
    );
  }
  return (
    <Tooltip
      withArrow
      label={
        <Stack gap={2}>
          {placements.map((p) => (
            <Text key={`${p.locale}-${p.placement}`} size="xs">
              {p.locale}: {p.placement}
            </Text>
          ))}
        </Stack>
      }
    >
      <Group gap={4} wrap="wrap">
        {placements.map((p) => (
          <Box
            key={`${p.locale}-${p.placement}`}
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: `var(--mantine-color-${PLACEMENT_COLOR[p.placement]}-6)`,
            }}
          />
        ))}
      </Group>
    </Tooltip>
  );
}
