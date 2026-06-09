import {
  Alert,
  Badge,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
} from "@mantine/core";
import { IconAlertCircle } from "@tabler/icons-react";

import type { KeywordTrackingResponse } from "@/types";

import KeywordIntelBadge, {
  getKeywordIntelState,
  type KeywordIntelState,
} from "./keywordIntel";

export interface TrackedKeywordIntelItem {
  id: number;
  text: string;
  locale: string;
  popularity: number | null;
  updatedAt: string | null;
  intel: KeywordIntelState;
}

export function buildTrackedKeywordIntelItems(
  trackings: KeywordTrackingResponse[] | undefined,
  now = Date.now(),
): TrackedKeywordIntelItem[] {
  return [...(trackings ?? [])]
    .map((tracking) => ({
      id: tracking.id,
      text: tracking.keyword.text,
      locale: tracking.keyword.locale,
      popularity: tracking.keyword.popularity,
      updatedAt: tracking.keyword.popularity_updated_at,
      intel: getKeywordIntelState(
        tracking.keyword.popularity,
        tracking.keyword.popularity_updated_at,
        now,
      ),
    }))
    .sort(
      (a, b) =>
        a.text.localeCompare(b.text) || a.locale.localeCompare(b.locale),
    );
}

interface TrackedKeywordIntelPanelProps {
  trackings: KeywordTrackingResponse[] | undefined;
  isLoading?: boolean;
  isError?: boolean;
  title?: string;
  hideWhenEmpty?: boolean;
  emptyMessage?: string;
}

function TrackedKeywordIntelChip({
  item,
}: {
  item: TrackedKeywordIntelItem;
}) {
  return (
    <Group gap={4} wrap="wrap">
      <Badge
        size="sm"
        radius="sm"
        variant="light"
        color="gray"
        style={{
          textTransform: "none",
          maxWidth: 180,
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {item.text}
      </Badge>
      <Badge size="xs" radius="sm" variant="outline" color="gray">
        {item.locale}
      </Badge>
      <KeywordIntelBadge
        popularity={item.popularity}
        updatedAt={item.updatedAt}
      />
    </Group>
  );
}

export default function TrackedKeywordIntelPanel({
  trackings,
  isLoading = false,
  isError = false,
  title = "Tracked keyword intel",
  hideWhenEmpty = false,
  emptyMessage = "Track a keyword to see cached keyword intel here.",
}: TrackedKeywordIntelPanelProps) {
  const items = buildTrackedKeywordIntelItems(trackings);

  if (!isLoading && !isError && hideWhenEmpty && items.length === 0) {
    return null;
  }

  return (
    <Paper withBorder p="xs">
      <Stack gap="xs">
        <Group justify="space-between" gap="xs">
          <Text size="xs" fw={600} c="dimmed" tt="uppercase">
            {title}
          </Text>
          <Text size="xs" c="dimmed">
            {isError
              ? "Unavailable"
              : isLoading
                ? "Loading…"
                : `${items.length} keyword${items.length === 1 ? "" : "s"}`}
          </Text>
        </Group>

        {isError ? (
          <Alert color="yellow" icon={<IconAlertCircle size={16} />}>
            Tracked keyword intel is temporarily unavailable. Try again in a
            moment.
          </Alert>
        ) : isLoading ? (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm" c="dimmed">
              Loading cached keyword intel…
            </Text>
          </Group>
        ) : items.length === 0 ? (
          <Text size="sm" c="dimmed">
            {emptyMessage}
          </Text>
        ) : (
          <Group gap="xs">
            {items.map((item) => (
              <TrackedKeywordIntelChip key={item.id} item={item} />
            ))}
          </Group>
        )}
      </Stack>
    </Paper>
  );
}
