import {
  Title,
  Text,
  Container,
  Paper,
  Stack,
  Group,
  Badge,
  Button,
  Table,
  Skeleton,
} from "@mantine/core";
import { IconRefresh, IconDatabase } from "@tabler/icons-react";
import { useIndexStatus, useRefreshIndices } from "@/lib/hooks";

const INDEX_LABELS: Record<string, string> = {
  ppp: "Purchasing Power Parity (PPP)",
  bigmac: "Big Mac Index",
  netflix: "Netflix Index",
  spotify: "Spotify Index",
};

function getIndexFreshness(lastRefresh: string | null): {
  color: string;
  label: string;
} {
  if (!lastRefresh) {
    return { color: "red", label: "Never" };
  }

  const refreshDate = new Date(lastRefresh);
  const daysSince = Math.floor(
    (Date.now() - refreshDate.getTime()) / (1000 * 60 * 60 * 24),
  );

  if (daysSince <= 30) {
    return { color: "green", label: "Fresh" };
  }
  if (daysSince <= 90) {
    return { color: "yellow", label: "Stale" };
  }
  return { color: "red", label: "Outdated" };
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "Never refreshed";
  return new Date(dateStr).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function EconomicIndicesSection() {
  const { data: status, isLoading } = useIndexStatus();
  const refreshMutation = useRefreshIndices();

  if (isLoading) {
    return (
      <Paper withBorder p="md" radius="md">
        <Stack>
          <Skeleton height={24} width={200} />
          <Skeleton height={40} />
          <Skeleton height={40} />
          <Skeleton height={40} />
          <Skeleton height={40} />
        </Stack>
      </Paper>
    );
  }

  const indexEntries = Object.entries(status ?? {});

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" p="md" pb={0}>
        <Group gap="xs">
          <IconDatabase size={20} color="var(--mantine-color-blue-6)" />
          <Title order={4}>Economic Indices</Title>
        </Group>
        <Button
          leftSection={<IconRefresh size={16} />}
          onClick={() => refreshMutation.mutate()}
          loading={refreshMutation.isPending}
          size="sm"
        >
          Refresh All Indices
        </Button>
      </Group>
      <Text c="dimmed" size="sm" px="md" mt={4} mb="md">
        Economic indices used for calculating territory-specific pricing.
      </Text>

      {indexEntries.length === 0 ? (
        <Text c="dimmed" size="sm" p="md" ta="center">
          No indices configured. Click "Refresh All Indices" to populate data.
        </Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Index</Table.Th>
              <Table.Th>Entries</Table.Th>
              <Table.Th>Last Refresh</Table.Th>
              <Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {indexEntries.map(([key, info]) => {
              const freshness = getIndexFreshness(info.last_refresh);
              return (
                <Table.Tr key={key}>
                  <Table.Td>
                    <Text fw={500} size="sm">
                      {INDEX_LABELS[key] ?? key}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="gray" size="sm">
                      {info.count}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {formatDate(info.last_refresh)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={freshness.color} size="sm">
                      {freshness.label}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
    </Paper>
  );
}

export default function SettingsPage() {
  return (
    <Container size="lg">
      <Title order={2} mb="md">
        Settings
      </Title>
      <Text c="dimmed" mb="lg">
        Application settings and preferences.
      </Text>

      <Stack gap="lg">
        <EconomicIndicesSection />
      </Stack>
    </Container>
  );
}
