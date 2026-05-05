import { Group, HoverCard, Stack, Text, Badge } from "@mantine/core";
import type { KeywordCoverageItem, KeywordPlacement } from "@/types";

interface KeywordCoverageBadgeProps {
  items: KeywordCoverageItem[];
}

const PLACEMENT_COLOR: Record<KeywordPlacement, string> = {
  title: "green",
  subtitle: "orange",
  keywords: "yellow",
  none: "gray",
};

const PLACEMENT_LABEL: Record<KeywordPlacement, string> = {
  title: "Title",
  subtitle: "Subtitle",
  keywords: "Keywords",
  none: "Missing",
};

/**
 * One coloured dot per tracked keyword that appears (or is missing) in the
 * locale's metadata. Hover surfaces the keyword + placement breakdown.
 */
export default function KeywordCoverageBadge({ items }: KeywordCoverageBadgeProps) {
  if (items.length === 0) return null;
  return (
    <HoverCard width={240} shadow="md" withArrow position="top">
      <HoverCard.Target>
        <Group gap={3} wrap="nowrap" style={{ cursor: "default" }}>
          {items.map((it) => (
            <span
              key={`${it.keyword}-${it.placement}`}
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: `var(--mantine-color-${PLACEMENT_COLOR[it.placement]}-6)`,
                display: "inline-block",
              }}
            />
          ))}
        </Group>
      </HoverCard.Target>
      <HoverCard.Dropdown>
        <Stack gap={4}>
          <Text size="xs" fw={600}>
            Tracked keyword coverage
          </Text>
          {items.map((it) => (
            <Group key={`${it.keyword}-${it.placement}`} gap="xs" justify="space-between">
              <Text size="xs">{it.keyword}</Text>
              <Badge size="xs" color={PLACEMENT_COLOR[it.placement]} variant="light">
                {PLACEMENT_LABEL[it.placement]}
              </Badge>
            </Group>
          ))}
        </Stack>
      </HoverCard.Dropdown>
    </HoverCard>
  );
}
