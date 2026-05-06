import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Container,
  Group,
  Image,
  Loader,
  Paper,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { DataTable } from "mantine-datatable";
import {
  IconAlertCircle,
  IconChartBar,
  IconPlayerPlay,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import {
  useAddVisibilityWatch,
  useApp,
  useDeleteVisibilityWatch,
  usePollVisibilityWatch,
  useVisibilitySov,
  useVisibilityWatches,
} from "@/lib/hooks";
import type { VisibilityWatchOut } from "@/types";
import VisibilityDrawer from "@/components/visibility/VisibilityDrawer";

const COUNTRY_OPTIONS = [
  { value: "us", label: "US" },
  { value: "gb", label: "GB" },
  { value: "de", label: "DE" },
  { value: "fr", label: "FR" },
  { value: "es", label: "ES" },
  { value: "it", label: "IT" },
  { value: "jp", label: "JP" },
  { value: "kr", label: "KR" },
  { value: "cn", label: "CN" },
  { value: "ru", label: "RU" },
  { value: "br", label: "BR" },
  { value: "mx", label: "MX" },
  { value: "in", label: "IN" },
  { value: "au", label: "AU" },
  { value: "ca", label: "CA" },
];

export default function VisibilityPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const { data: app } = useApp(id ?? "");

  const watchesQuery = useVisibilityWatches(appId);
  const sovQuery = useVisibilitySov(appId, 30);
  const addMutation = useAddVisibilityWatch(appId);
  const deleteMutation = useDeleteVisibilityWatch(appId);
  const pollMutation = usePollVisibilityWatch(appId);

  const [newText, setNewText] = useState("");
  const [newCountry, setNewCountry] = useState("us");
  const [selected, setSelected] = useState<VisibilityWatchOut | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const watches = useMemo(
    () => watchesQuery.data?.items ?? [],
    [watchesQuery.data],
  );

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  const handleAdd = () => {
    if (!newText.trim()) return;
    addMutation.mutate(
      { text: newText.trim(), country: newCountry },
      {
        onSuccess: () => {
          setNewText("");
        },
      },
    );
  };

  const handleRowClick = (watch: VisibilityWatchOut) => {
    setSelected(watch);
    setDrawerOpen(true);
  };

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-md)" }}>
        <Group gap="sm" align="center">
          <IconChartBar size={22} />
          <Title order={2}>{app?.name ?? "App"} — Keyword Visibility</Title>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Track who shows up in iTunes search for the keywords you care about.
          Snapshots are organic top-20 results; SOV counts how often each app
          lands in the top 3 over the last 30 days.
        </Text>
      </div>

      <Stack gap="sm">
        <Paper withBorder p="xs">
          <Group gap="xs" align="flex-end">
            <TextInput
              label="New keyword"
              placeholder="e.g., meditation"
              value={newText}
              onChange={(e) => setNewText(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAdd();
              }}
              style={{ flex: 1 }}
              size="xs"
            />
            <Select
              label="Country"
              data={COUNTRY_OPTIONS}
              value={newCountry}
              onChange={(v) => setNewCountry(v ?? "us")}
              size="xs"
              style={{ width: 100 }}
              allowDeselect={false}
            />
            <Button
              leftSection={<IconPlus size={14} />}
              size="xs"
              onClick={handleAdd}
              loading={addMutation.isPending}
              disabled={!newText.trim()}
            >
              Watch
            </Button>
          </Group>
        </Paper>

        <DataTable<VisibilityWatchOut>
          withTableBorder
          highlightOnHover
          striped
          records={watches}
          idAccessor="id"
          fetching={watchesQuery.isLoading}
          minHeight={watches.length === 0 ? 160 : undefined}
          noRecordsText="No watches yet — add one above."
          onRowClick={({ record }) => handleRowClick(record)}
          columns={[
            {
              accessor: "text",
              title: "Keyword",
              render: (r) => (
                <Text size="sm" fw={500}>
                  {r.text}
                </Text>
              ),
            },
            {
              accessor: "country",
              title: "Country",
              width: 90,
              render: (r) => (
                <Badge size="xs" variant="light" color="gray">
                  {r.country.toUpperCase()}
                </Badge>
              ),
            },
            {
              accessor: "last_polled_at",
              title: "Last poll",
              width: 180,
              render: (r) => (
                <Text size="xs" c="dimmed">
                  {r.last_polled_at
                    ? new Date(r.last_polled_at).toLocaleString()
                    : "never"}
                </Text>
              ),
            },
            {
              accessor: "results_count",
              title: "Results",
              width: 90,
              render: (r) => (
                <Text size="xs" c="dimmed">
                  {r.latest_snapshot?.results_count ?? 0}
                </Text>
              ),
            },
            {
              accessor: "actions",
              title: "",
              width: 130,
              textAlign: "right" as const,
              render: (r) => (
                <Group gap="xs" justify="flex-end" wrap="nowrap">
                  <Tooltip label="Poll iTunes now" withArrow>
                    <ActionIcon
                      size="sm"
                      variant="light"
                      onClick={(e) => {
                        e.stopPropagation();
                        pollMutation.mutate(r.id);
                      }}
                      loading={
                        pollMutation.isPending &&
                        pollMutation.variables === r.id
                      }
                    >
                      <IconPlayerPlay size={14} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Stop watching" withArrow>
                    <ActionIcon
                      size="sm"
                      variant="subtle"
                      color="red"
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteMutation.mutate(r.id);
                      }}
                      loading={
                        deleteMutation.isPending &&
                        deleteMutation.variables === r.id
                      }
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Tooltip>
                </Group>
              ),
            },
          ]}
        />

        <Paper withBorder p="md">
          <Group justify="space-between" mb="sm">
            <Text size="sm" fw={600}>
              Share of voice — top 3 (last 30 days)
            </Text>
            {sovQuery.isLoading && <Loader size="xs" />}
          </Group>
          {sovQuery.data?.items?.length === 0 ? (
            <Text size="xs" c="dimmed">
              No data yet. Poll a watch a few times to start collecting.
            </Text>
          ) : (
            <Stack gap="md">
              {(sovQuery.data?.items ?? []).map((sov) => (
                <Stack key={sov.watch_id} gap={4}>
                  <Group gap="xs">
                    <Text size="xs" fw={600}>
                      {sov.text}
                    </Text>
                    <Badge size="xs" variant="light" color="gray">
                      {sov.country.toUpperCase()}
                    </Badge>
                    <Text size="xs" c="dimmed">
                      {sov.polls} poll{sov.polls === 1 ? "" : "s"}
                    </Text>
                  </Group>
                  {sov.entries.length === 0 ? (
                    <Text size="xs" c="dimmed">
                      No top-3 hits recorded yet.
                    </Text>
                  ) : (
                    <Stack gap={2}>
                      {sov.entries.map((e) => (
                        <Group key={e.track_id} gap="xs" wrap="nowrap">
                          <Image
                            src={e.icon_url}
                            w={20}
                            h={20}
                            radius="sm"
                            fallbackSrc="https://placehold.co/20?text=?"
                          />
                          <Text size="xs" fw={500} truncate style={{ width: 200 }}>
                            {e.name}
                          </Text>
                          <div
                            style={{
                              flex: 1,
                              height: 8,
                              borderRadius: 4,
                              background: "var(--mantine-color-gray-2)",
                              position: "relative",
                            }}
                          >
                            <div
                              style={{
                                width: `${e.sov_pct}%`,
                                height: "100%",
                                borderRadius: 4,
                                background:
                                  "var(--mantine-color-blue-5)",
                              }}
                            />
                          </div>
                          <Text size="xs" c="dimmed" w={60} ta="right">
                            {e.sov_pct}%
                          </Text>
                        </Group>
                      ))}
                    </Stack>
                  )}
                </Stack>
              ))}
            </Stack>
          )}
        </Paper>
      </Stack>

      <VisibilityDrawer
        appId={appId}
        watch={selected}
        opened={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      />
    </Container>
  );
}
