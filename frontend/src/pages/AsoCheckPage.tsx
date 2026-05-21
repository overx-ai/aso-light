import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Container,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { DataTable } from "mantine-datatable";
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconArrowRight,
  IconCash,
  IconChecks,
  IconCoin,
  IconInfoCircle,
} from "@tabler/icons-react";
import {
  useApp,
  useASACredentials,
  useAsoCheck,
  usePaidOrganicJoin,
} from "@/lib/hooks";
import type {
  AsoIssueOut,
  AsoIssueSeverity,
  AsoRecommendationOut,
  AsoRecommendationPriority,
} from "@/types";

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

const REC_PRIORITY_COLOR: Record<AsoRecommendationPriority, string> = {
  high: "red",
  medium: "yellow",
  low: "blue",
};

const REC_PRIORITY_LABEL: Record<AsoRecommendationPriority, string> = {
  high: "High priority",
  medium: "Medium priority",
  low: "Low priority",
};

function SevBadge({ severity }: { severity: AsoIssueSeverity }) {
  return (
    <Badge size="xs" color={SEV_COLOR[severity]} variant="light">
      {SEV_LABEL[severity]}
    </Badge>
  );
}

function GrowthRecommendationsSection({
  recommendations,
}: {
  recommendations: AsoRecommendationOut[];
}) {
  const pricingRecommendations = recommendations.filter(
    (item) => item.category === "pricing",
  );

  if (pricingRecommendations.length === 0) {
    return null;
  }

  return (
    <Stack gap="sm">
      <div>
        <Group gap="xs" mb={4}>
          <IconCoin size={18} color="var(--mantine-color-green-6)" />
          <Text fw={600} size="sm">
            Pricing Opportunities
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          Signals derived from cached storefront prices and territory economics.
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
        {pricingRecommendations.map((item) => (
          <Paper key={item.id} withBorder p="md" radius="md">
            <Stack gap="sm">
              <Group justify="space-between" align="flex-start" wrap="wrap">
                <Badge variant="light" color="green" size="sm">
                  Pricing
                </Badge>
                <Badge
                  variant="outline"
                  color={REC_PRIORITY_COLOR[item.priority]}
                  size="sm"
                >
                  {REC_PRIORITY_LABEL[item.priority]}
                </Badge>
              </Group>

              <div>
                <Text fw={600} size="sm">
                  {item.title}
                </Text>
                <Text size="sm" mt={6}>
                  {item.body}
                </Text>
              </div>

              {item.facts.length > 0 ? (
                <Stack gap={4}>
                  {item.facts.map((fact) => (
                    <Text key={fact} size="xs" c="dimmed">
                      {fact}
                    </Text>
                  ))}
                </Stack>
              ) : null}

              {item.cta_path && item.cta_label ? (
                <Group justify="flex-start">
                  <Button
                    component={Link}
                    to={item.cta_path}
                    size="xs"
                    variant="light"
                    rightSection={<IconArrowRight size={14} />}
                  >
                    {item.cta_label}
                  </Button>
                </Group>
              ) : null}
            </Stack>
          </Paper>
        ))}
      </SimpleGrid>
    </Stack>
  );
}

function PaidCoverageSection({ appId }: { appId: number }) {
  const creds = useASACredentials();
  const join = usePaidOrganicJoin(appId, 30);

  // Hide entirely if ASA isn't connected AND there's no paid signal at all.
  const credsLoaded = !creds.isLoading;
  const hasCreds = (creds.data ?? []).length > 0;
  const rows = join.data ?? [];
  const anyPaid = rows.some((r) => r.paid_impressions_30d > 0);

  if (credsLoaded && !hasCreds && !anyPaid) return null;
  if (join.isLoading) return null;

  const tracked = rows;
  const withPaid = tracked.filter((r) => r.paid_impressions_30d > 0);
  const withoutPaid = tracked.filter((r) => r.paid_impressions_30d === 0);

  return (
    <Paper withBorder p="md" radius="md">
      <Group gap="xs" mb="xs">
        <IconCash size={18} color="var(--mantine-color-blue-6)" />
        <Text fw={600} size="sm">
          ASA Paid Coverage
        </Text>
      </Group>
      <Group gap="md" wrap="wrap" mb="sm">
        <Badge variant="light" color="green" size="sm">
          {withPaid.length} tracked terms with paid bids
        </Badge>
        <Badge variant="light" color="yellow" size="sm">
          {withoutPaid.length} tracked terms without paid coverage
        </Badge>
      </Group>

      {withoutPaid.length === 0 ? (
        <Text size="sm" c="dimmed">
          Every tracked keyword is also covered by an ASA bid. Nice.
        </Text>
      ) : (
        <Stack gap="xs">
          <Text size="xs" c="dimmed">
            Consider bidding on these tracked terms in Apple Search Ads:
          </Text>
          <Group gap="xs" wrap="wrap">
            {withoutPaid.slice(0, 30).map((r) => (
              <Badge key={r.term} variant="outline" color="yellow" size="sm">
                {r.term}
                {r.organic_rank !== null && (
                  <Text component="span" ml={4} size="xs" c="dimmed">
                    #{r.organic_rank}
                  </Text>
                )}
              </Badge>
            ))}
            {withoutPaid.length > 30 && (
              <Text size="xs" c="dimmed">
                +{withoutPaid.length - 30} more
              </Text>
            )}
          </Group>
        </Stack>
      )}
    </Paper>
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
    const base = [{ value: "any", label: "Any locale" }];
    if (!auditQuery.data) return base;
    const locales = new Set<string>();
    for (const issue of auditQuery.data.items) {
      if (issue.locale) locales.add(issue.locale);
    }
    return [
      ...base,
      ...Array.from(locales)
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
  const recommendations = auditQuery.data?.recommendations ?? [];

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-md)" }}>
        <Group gap="sm" align="center">
          <IconChecks size={22} />
          <Title order={2}>{app?.name ?? "App"} — ASO Check</Title>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Listing audit across every synced locale, plus pricing opportunities
          derived from your cached storefront data.
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

          <GrowthRecommendationsSection recommendations={recommendations} />

          <PaidCoverageSection appId={appId} />

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
