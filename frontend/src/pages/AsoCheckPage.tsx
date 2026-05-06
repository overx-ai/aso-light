import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Container,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Select,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { DataTable } from "mantine-datatable";
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconChecks,
  IconInfoCircle,
} from "@tabler/icons-react";
import { useApp, useAsoCheck } from "@/lib/hooks";
import type { AsoIssueOut, AsoIssueSeverity } from "@/types";

const SEV_COLOR: Record<AsoIssueSeverity, string> = {
  error: "red",
  warning: "yellow",
  info: "blue",
};

const SEV_LABEL: Record<AsoIssueSeverity, string> = {
  error: "Error",
  warning: "Warning",
  info: "Info",
};

function SevBadge({ severity }: { severity: AsoIssueSeverity }) {
  return (
    <Badge size="xs" color={SEV_COLOR[severity]} variant="light">
      {SEV_LABEL[severity]}
    </Badge>
  );
}

export default function AsoCheckPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const { data: app } = useApp(id ?? "");
  const auditQuery = useAsoCheck(appId);

  const [severity, setSeverity] = useState<"all" | AsoIssueSeverity>("all");
  const [locale, setLocale] = useState<string>("any");

  const localeOptions = useMemo(() => {
    if (!auditQuery.data) return [{ value: "any", label: "Any locale" }];
    const set = new Set<string>();
    for (const i of auditQuery.data.items) {
      if (i.locale) set.add(i.locale);
    }
    return [
      { value: "any", label: "Any locale" },
      ...Array.from(set)
        .sort()
        .map((l) => ({ value: l, label: l })),
    ];
  }, [auditQuery.data]);

  const filtered: AsoIssueOut[] = useMemo(() => {
    const items = auditQuery.data?.items ?? [];
    return items.filter(
      (i) =>
        (severity === "all" || i.severity === severity) &&
        (locale === "any" || i.locale === locale),
    );
  }, [auditQuery.data, severity, locale]);

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  const summary = auditQuery.data?.summary;

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-md)" }}>
        <Group gap="sm" align="center">
          <IconChecks size={22} />
          <Title order={2}>{app?.name ?? "App"} — ASO Check</Title>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Listing audit across every synced locale: empty fields, char-limit
          warnings, duplicate keywords, malformed URLs, and tracked keywords
          you forgot to place.
        </Text>
      </div>

      {auditQuery.isLoading ? (
        <Group justify="center" py="xl">
          <Loader />
        </Group>
      ) : auditQuery.error ? (
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Could not run audit.
        </Alert>
      ) : (
        <Stack gap="sm">
          <Group gap="md" wrap="wrap">
            <Paper withBorder p="xs" px="md">
              <Group gap="xs">
                <IconAlertCircle size={16} color="var(--mantine-color-red-6)" />
                <Text size="sm">{summary?.errors ?? 0} errors</Text>
              </Group>
            </Paper>
            <Paper withBorder p="xs" px="md">
              <Group gap="xs">
                <IconAlertTriangle
                  size={16}
                  color="var(--mantine-color-yellow-6)"
                />
                <Text size="sm">{summary?.warnings ?? 0} warnings</Text>
              </Group>
            </Paper>
            <Paper withBorder p="xs" px="md">
              <Group gap="xs">
                <IconInfoCircle size={16} color="var(--mantine-color-blue-6)" />
                <Text size="sm">{summary?.infos ?? 0} info</Text>
              </Group>
            </Paper>
            <Paper withBorder p="xs" px="md">
              <Text size="sm" c="dimmed">
                {summary?.locales_audited ?? 0} locales audited
              </Text>
            </Paper>
          </Group>

          <Paper withBorder p="xs">
            <Group gap="md" wrap="wrap" align="flex-end">
              <SegmentedControl
                size="xs"
                value={severity}
                onChange={(v) => setSeverity(v as typeof severity)}
                data={[
                  { value: "all", label: "All" },
                  { value: "error", label: "Errors" },
                  { value: "warning", label: "Warnings" },
                  { value: "info", label: "Info" },
                ]}
              />
              <Select
                size="xs"
                label="Locale"
                data={localeOptions}
                value={locale}
                onChange={(v) => setLocale(v ?? "any")}
                style={{ width: 160 }}
              />
              <Text c="dimmed" size="xs" mt="xl">
                {filtered.length} of {auditQuery.data?.items.length ?? 0}
              </Text>
            </Group>
          </Paper>

          <DataTable<AsoIssueOut>
            withTableBorder
            highlightOnHover
            striped
            records={filtered}
            idAccessor={(r) => `${r.code}-${r.locale}-${r.field}-${r.message}`}
            minHeight={filtered.length === 0 ? 200 : undefined}
            noRecordsText="Nothing flagged at this filter."
            columns={[
              {
                accessor: "severity",
                title: "Severity",
                width: 100,
                render: (r) => <SevBadge severity={r.severity} />,
              },
              {
                accessor: "locale",
                title: "Locale",
                width: 100,
                render: (r) =>
                  r.locale ? (
                    <Badge size="xs" variant="light" color="gray">
                      {r.locale}
                    </Badge>
                  ) : (
                    <Text size="xs" c="dimmed">
                      global
                    </Text>
                  ),
              },
              {
                accessor: "field",
                title: "Field",
                width: 140,
                render: (r) => (
                  <Text size="xs" c="dimmed">
                    {r.field ?? "—"}
                  </Text>
                ),
              },
              {
                accessor: "message",
                title: "Issue",
                render: (r) => (
                  <Stack gap={0}>
                    <Text size="sm">{r.message}</Text>
                    {r.suggestion && (
                      <Text size="xs" c="dimmed" mt={2}>
                        → {r.suggestion}
                      </Text>
                    )}
                  </Stack>
                ),
              },
              {
                accessor: "code",
                title: "Code",
                width: 140,
                render: (r) => (
                  <Text size="xs" c="dimmed" ff="monospace">
                    {r.code}
                  </Text>
                ),
              },
            ]}
          />
        </Stack>
      )}
    </Container>
  );
}
