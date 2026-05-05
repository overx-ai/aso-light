import { Badge, Button, Group, Stack, Text, Title, Tooltip } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import type { App, AppMetadataState } from "@/types";
import { relativeTime } from "@/components/metadata/fieldConfig";

interface MetadataHeaderProps {
  app: App | undefined;
  state: AppMetadataState;
  syncing: boolean;
  onSync: () => void;
}

// Editable version states surfaced by the backend snapshot's editable_fields
// — anything outside of these is treated as read-only or promo-only.
const PROMO_ONLY_STATE = "READY_FOR_DISTRIBUTION";

function stateBadge(state: AppMetadataState): { color: string; label: string } {
  if (!state.editable_version_state) {
    return { color: "gray", label: "No editable version" };
  }
  if (state.editable_version_state === PROMO_ONLY_STATE) {
    return { color: "yellow", label: "Promo only (live)" };
  }
  return { color: "green", label: state.editable_version_state };
}

export default function MetadataHeader({
  app,
  state,
  syncing,
  onSync,
}: MetadataHeaderProps) {
  const badge = stateBadge(state);
  return (
    <Stack gap={4}>
      <Group justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Title order={2}>{app?.name ?? "App"} - Metadata</Title>
          <Group gap="xs">
            <Badge color={badge.color} variant="light">
              {badge.label}
            </Badge>
            <Text c="dimmed" size="sm">
              Synced {relativeTime(state.last_synced_at)}
            </Text>
          </Group>
        </Stack>
        <Tooltip label="Pull latest metadata from App Store Connect" withArrow>
          <Button
            variant="light"
            leftSection={<IconRefresh size={16} />}
            loading={syncing}
            onClick={onSync}
          >
            Sync from ASC
          </Button>
        </Tooltip>
      </Group>
    </Stack>
  );
}
