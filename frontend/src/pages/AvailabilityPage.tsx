import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Container,
  Group,
  Paper,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Tooltip,
  ActionIcon,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconCircleCheck,
  IconCircleX,
  IconDeviceFloppy,
  IconRotate,
  IconSearch,
} from "@tabler/icons-react";
import { DataTable, type DataTableSortStatus } from "mantine-datatable";
import {
  useAppAvailability,
  useApps,
  useUpdateAppAvailability,
} from "@/lib/hooks";
import type { TerritoryAvailability } from "@/types";

export default function AvailabilityPage() {
  const { id: appId } = useParams<{ id: string }>();
  const { data: apps } = useApps();
  const app = apps?.find((a) => String(a.id) === appId);

  const { data, isLoading, error, refetch, isFetching } = useAppAvailability(
    appId ?? "",
  );
  const update = useUpdateAppAvailability();

  // Local working copy: server state + user toggles since last save.
  const [disabled, setDisabled] = useState<Set<string>>(new Set());
  const [availableInNew, setAvailableInNew] = useState(true);
  const [search, setSearch] = useState("");
  const [sortStatus, setSortStatus] = useState<
    DataTableSortStatus<TerritoryAvailability>
  >({ columnAccessor: "territory_name", direction: "asc" });

  // Reset local state whenever server data arrives.
  useEffect(() => {
    if (!data) return;
    setDisabled(
      new Set(
        data.territories.filter((t) => !t.available).map((t) => t.territory_code),
      ),
    );
    setAvailableInNew(data.available_in_new_territories);
  }, [data]);

  const baselineDisabled = useMemo(() => {
    if (!data) return new Set<string>();
    return new Set(
      data.territories.filter((t) => !t.available).map((t) => t.territory_code),
    );
  }, [data]);

  const dirty = useMemo(() => {
    if (!data) return false;
    if (availableInNew !== data.available_in_new_territories) return true;
    if (disabled.size !== baselineDisabled.size) return true;
    for (const c of disabled) if (!baselineDisabled.has(c)) return true;
    return false;
  }, [data, disabled, availableInNew, baselineDisabled]);

  const handleToggle = (code: string) => {
    setDisabled((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const handleReset = () => {
    if (!data) return;
    setDisabled(
      new Set(
        data.territories.filter((t) => !t.available).map((t) => t.territory_code),
      ),
    );
    setAvailableInNew(data.available_in_new_territories);
  };

  const handleEnableAll = () => setDisabled(new Set());

  const handleDisableAll = () => {
    if (!data) return;
    setDisabled(new Set(data.territories.map((t) => t.territory_code)));
  };

  const handleSave = () => {
    if (!appId) return;
    update.mutate({
      appId,
      data: {
        available_in_new_territories: availableInNew,
        disabled_territories: Array.from(disabled),
      },
    });
  };

  const filteredRows = useMemo(() => {
    if (!data) return [] as TerritoryAvailability[];
    const q = search.trim().toLowerCase();
    let rows = data.territories.map((t) => ({
      ...t,
      available: !disabled.has(t.territory_code),
    }));
    if (q) {
      rows = rows.filter(
        (r) =>
          r.territory_code.toLowerCase().includes(q) ||
          r.territory_name.toLowerCase().includes(q),
      );
    }
    const acc = sortStatus.columnAccessor as keyof TerritoryAvailability;
    rows = [...rows].sort((a, b) => {
      const av = a[acc];
      const bv = b[acc];
      if (typeof av === "boolean" && typeof bv === "boolean") {
        return av === bv ? 0 : av ? -1 : 1;
      }
      return String(av).localeCompare(String(bv));
    });
    if (sortStatus.direction === "desc") rows.reverse();
    return rows;
  }, [data, disabled, search, sortStatus]);

  const availableCount = data
    ? data.territories.length - disabled.size
    : 0;
  const disabledCount = disabled.size;
  const baseDisabledList = useMemo(
    () => Array.from(baselineDisabled).sort(),
    [baselineDisabled],
  );

  return (
    <Container size="xl">
      <Stack gap="md">
        <Group justify="space-between" align="flex-end">
          <div>
            <Title order={2}>App Availability</Title>
            {app && (
              <Text c="dimmed" size="sm">
                {app.name} · toggle which territories carry the app on the
                App Store.
              </Text>
            )}
          </div>
          <Group>
            <Tooltip label="Re-fetch from Apple">
              <ActionIcon
                variant="subtle"
                onClick={() => refetch()}
                loading={isFetching}
              >
                <IconRotate size={18} />
              </ActionIcon>
            </Tooltip>
            <Button
              variant="subtle"
              onClick={handleReset}
              disabled={!dirty || update.isPending}
            >
              Reset
            </Button>
            <Button
              leftSection={<IconDeviceFloppy size={16} />}
              color="green"
              onClick={handleSave}
              loading={update.isPending}
              disabled={!dirty}
            >
              Save changes
            </Button>
          </Group>
        </Group>

        {error && (
          <Alert color="red" icon={<IconAlertCircle size={16} />}>
            Failed to load availability from Apple.
          </Alert>
        )}

        <Paper withBorder p="md" radius="md">
          <Group justify="space-between" align="center">
            <Group gap="md">
              <Badge color="green" variant="light" size="lg">
                {availableCount} available
              </Badge>
              <Badge color="red" variant="light" size="lg">
                {disabledCount} disabled
              </Badge>
              {dirty && (
                <Badge color="yellow" variant="filled" size="lg">
                  Unsaved changes
                </Badge>
              )}
            </Group>
            <Group gap="xs">
              <Button
                size="xs"
                variant="light"
                color="green"
                onClick={handleEnableAll}
                disabled={disabledCount === 0}
              >
                Enable all
              </Button>
              <Button
                size="xs"
                variant="subtle"
                color="red"
                onClick={handleDisableAll}
                disabled={availableCount === 0}
              >
                Disable all
              </Button>
              <Switch
                label="Auto-enable new territories Apple adds"
                checked={availableInNew}
                onChange={(e) => setAvailableInNew(e.currentTarget.checked)}
              />
            </Group>
          </Group>
          {baseDisabledList.length > 0 && !dirty && (
            <Text size="xs" c="dimmed" mt="xs">
              Currently off: {baseDisabledList.join(", ")}
            </Text>
          )}
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Stack gap="sm">
            <TextInput
              placeholder="Search territory by name or code..."
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
              leftSection={<IconSearch size={14} />}
              size="sm"
              w={300}
            />
            <DataTable
              minHeight={400}
              fetching={isLoading}
              records={filteredRows}
              idAccessor="territory_code"
              sortStatus={sortStatus}
              onSortStatusChange={setSortStatus}
              striped
              highlightOnHover
              rowStyle={(row) =>
                row.available
                  ? undefined
                  : {
                      backgroundColor: "var(--mantine-color-red-0)",
                      borderLeft: "3px solid var(--mantine-color-red-6)",
                    }
              }
              columns={[
                {
                  accessor: "territory_code",
                  title: "Code",
                  sortable: true,
                  width: 80,
                },
                {
                  accessor: "territory_name",
                  title: "Territory",
                  sortable: true,
                },
                {
                  accessor: "available",
                  title: "Status",
                  sortable: true,
                  width: 130,
                  render: (row) =>
                    row.available ? (
                      <Badge
                        color="green"
                        variant="light"
                        leftSection={<IconCircleCheck size={12} />}
                      >
                        Available
                      </Badge>
                    ) : (
                      <Badge
                        color="red"
                        variant="filled"
                        leftSection={<IconCircleX size={12} />}
                      >
                        Disabled
                      </Badge>
                    ),
                },
                {
                  accessor: "_toggle",
                  title: "",
                  width: 110,
                  render: (row) => (
                    <Button
                      size="xs"
                      variant={row.available ? "subtle" : "light"}
                      color={row.available ? "red" : "green"}
                      onClick={() => handleToggle(row.territory_code)}
                    >
                      {row.available ? "Disable" : "Enable"}
                    </Button>
                  ),
                },
              ]}
            />
          </Stack>
        </Paper>
      </Stack>
    </Container>
  );
}
