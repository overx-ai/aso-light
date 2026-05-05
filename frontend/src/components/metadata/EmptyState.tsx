import { Button, Paper, Stack, Text, Title } from "@mantine/core";
import { IconCloudDownload } from "@tabler/icons-react";

interface EmptyStateProps {
  onSync: () => void;
  loading: boolean;
}

export default function EmptyState({ onSync, loading }: EmptyStateProps) {
  return (
    <Paper withBorder p="xl" ta="center" radius="md">
      <Stack align="center" gap="md">
        <IconCloudDownload size={48} color="var(--mantine-color-dimmed)" />
        <Title order={4} c="dimmed">
          No metadata synced yet
        </Title>
        <Text c="dimmed" size="sm" maw={420}>
          Click "Sync from ASC" to fetch the current app metadata across all
          locales from App Store Connect. You can edit and bulk-update once
          synced.
        </Text>
        <Button
          leftSection={<IconCloudDownload size={16} />}
          onClick={onSync}
          loading={loading}
        >
          Sync from ASC
        </Button>
      </Stack>
    </Paper>
  );
}
