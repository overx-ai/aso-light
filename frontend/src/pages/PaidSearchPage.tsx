import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Container,
  Drawer,
  Group,
  Loader,
  Modal,
  Paper,
  Radio,
  Select,
  Skeleton,
  Stack,
  Tabs,
  Text,
  Title,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconCash,
  IconChartBar,
  IconCheck,
  IconKeyboard,
  IconSearch,
  IconTargetArrow,
} from "@tabler/icons-react";
import { DataTable } from "mantine-datatable";
import {
  useASACampaigns,
  useASACredentials,
  useASAAdGroups,
  useASAAdGroupKeywords,
  useASANegativeKeywords,
  useASANegativeCandidates,
  useASAOrganicCandidates,
  useASAPerformanceReport,
  useASASearchTermReport,
  useAddKeyword,
  useAddNegativeKeywords,
  useApp,
  useRemoveNegativeKeyword,
  type ASACampaignOut,
  type ASAKeywordOut,
  type ASANegativeCandidate,
  type ASANegativeKeywordOut,
  type ASAOrganicCandidate,
  type ASAPerformanceReportRow,
  type ASASearchTermReportRow,
} from "@/lib/hooks";
import {
  buildCampaignDrilldown,
  buildPerformanceMap,
  type CampaignDrilldownRow,
} from "@/pages/paid-search/campaignDrilldown";

// ----- Helpers -----

const CAMPAIGN_BREAKDOWN_WINDOW_DAYS = 30;

function formatNumber(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function formatMoney(
  amount: string | number | null | undefined,
  currency: string | null,
): string {
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  const cur = currency ?? "USD";
  try {
    return n.toLocaleString(undefined, {
      style: "currency",
      currency: cur,
      maximumFractionDigits: 2,
    });
  } catch {
    return `${n.toFixed(2)} ${cur}`;
  }
}

function statusColor(status: string): string {
  const s = status.toUpperCase();
  if (s === "ENABLED" || s === "ACTIVE" || s === "RUNNING") return "green";
  if (s === "PAUSED") return "yellow";
  return "gray";
}

function formatPercent(
  value: number | string | null | undefined,
  digits = 1,
): string {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

// ----- Empty state shown when no ASA credentials connected -----

function NoASAState() {
  return (
    <Alert color="blue" icon={<IconAlertCircle size={16} />}>
      <Text size="sm">
        Connect Apple Search Ads in <strong>Settings</strong> to see paid data.
      </Text>
    </Alert>
  );
}

// ----- Overview tab -----

function OverviewTab({ appId }: { appId: number }) {
  const perf = useASAPerformanceReport(appId, "CAMPAIGN", 90);
  const organicCandidates = useASAOrganicCandidates(appId, 30, 20);
  const negativeCandidates = useASANegativeCandidates(appId, 30, 10, 0.005);
  const addKeyword = useAddKeyword();
  const addNegatives = useAddNegativeKeywords();
  const campaigns = useASACampaigns(appId);

  const totals = useMemo(() => {
    const rows = perf.data?.rows ?? [];
    let impressions = 0;
    let taps = 0;
    let installs = 0;
    let spend = 0;
    let currency: string | null = null;
    for (const r of rows) {
      impressions += r.impressions;
      taps += r.taps;
      installs += r.installs;
      spend += parseFloat(r.spend_amount);
      currency = currency ?? r.spend_currency;
    }
    return { impressions, taps, installs, spend, currency };
  }, [perf.data]);

  const trendByDate = useMemo(() => {
    const map = new Map<
      string,
      { date: string; impressions: number; taps: number; installs: number; spend: number }
    >();
    for (const r of perf.data?.rows ?? []) {
      const k = r.date;
      const existing = map.get(k) ?? {
        date: k,
        impressions: 0,
        taps: 0,
        installs: 0,
        spend: 0,
      };
      existing.impressions += r.impressions;
      existing.taps += r.taps;
      existing.installs += r.installs;
      existing.spend += parseFloat(r.spend_amount);
      map.set(k, existing);
    }
    return Array.from(map.values()).sort((a, b) =>
      a.date < b.date ? 1 : -1,
    );
  }, [perf.data]);

  // Pick a default campaign/ad-group to apply negatives against — first ENABLED campaign.
  const defaultCampaignId = useMemo(() => {
    const list = campaigns.data ?? [];
    const enabled = list.find((c) => c.status.toUpperCase() === "ENABLED");
    return enabled?.id ?? list[0]?.id ?? null;
  }, [campaigns.data]);

  const handleApplyNegative = (text: string) => {
    if (!defaultCampaignId) return;
    addNegatives.mutate({
      app_id: appId,
      body: {
        scope: "CAMPAIGN",
        scope_id: defaultCampaignId,
        keywords: [{ text, match_type: "EXACT" }],
      },
    });
  };

  const handleTrackOrganic = (text: string) => {
    addKeyword.mutate({
      appId: String(appId),
      text,
      locale: "en-US",
    });
  };

  if (perf.isLoading) {
    return (
      <Stack gap="md">
        <Skeleton height={80} />
        <Skeleton height={200} />
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <Group gap="md" wrap="wrap">
        <Paper withBorder p="md" miw={170}>
          <Text size="xs" c="dimmed">
            Spend (90d)
          </Text>
          <Title order={3}>{formatMoney(totals.spend, totals.currency)}</Title>
        </Paper>
        <Paper withBorder p="md" miw={170}>
          <Text size="xs" c="dimmed">
            Installs (90d)
          </Text>
          <Title order={3}>{formatNumber(totals.installs)}</Title>
        </Paper>
        <Paper withBorder p="md" miw={170}>
          <Text size="xs" c="dimmed">
            Taps (90d)
          </Text>
          <Title order={3}>{formatNumber(totals.taps)}</Title>
        </Paper>
        <Paper withBorder p="md" miw={170}>
          <Text size="xs" c="dimmed">
            Impressions (90d)
          </Text>
          <Title order={3}>{formatNumber(totals.impressions)}</Title>
        </Paper>
      </Group>

      <Paper withBorder radius="md">
        <Group p="md" pb="xs">
          <Title order={5}>Daily trend</Title>
          <Text size="xs" c="dimmed">
            Aggregated across all campaigns
          </Text>
        </Group>
        <DataTable
          withTableBorder={false}
          striped
          highlightOnHover
          minHeight={trendByDate.length === 0 ? 160 : undefined}
          records={trendByDate}
          idAccessor="date"
          noRecordsText="No paid activity in the last 90 days."
          columns={[
            { accessor: "date", title: "Date", width: 110 },
            {
              accessor: "impressions",
              title: "Impressions",
              textAlign: "right" as const,
              render: (r) => formatNumber(r.impressions),
            },
            {
              accessor: "taps",
              title: "Taps",
              textAlign: "right" as const,
              render: (r) => formatNumber(r.taps),
            },
            {
              accessor: "installs",
              title: "Installs",
              textAlign: "right" as const,
              render: (r) => formatNumber(r.installs),
            },
            {
              accessor: "spend",
              title: "Spend",
              textAlign: "right" as const,
              render: (r) => formatMoney(r.spend, totals.currency),
            },
          ]}
        />
      </Paper>

      <Group gap="md" wrap="nowrap" align="flex-start">
        <Paper withBorder radius="md" style={{ flex: 1 }}>
          <Group p="md" pb="xs">
            <IconTargetArrow size={16} color="var(--mantine-color-blue-6)" />
            <Title order={5}>Organic-tracking candidates</Title>
          </Group>
          <Text size="xs" c="dimmed" px="md" mb="xs">
            Search terms with high paid taps that you don't track organically yet.
          </Text>
          <DataTable<ASAOrganicCandidate>
            withTableBorder={false}
            striped
            minHeight={(organicCandidates.data ?? []).length === 0 ? 120 : undefined}
            records={(organicCandidates.data ?? []).slice(0, 5)}
            idAccessor="text"
            noRecordsText="Nothing to suggest yet."
            columns={[
              { accessor: "text", title: "Term" },
              {
                accessor: "taps",
                title: "Taps",
                width: 80,
                textAlign: "right" as const,
                render: (r) => formatNumber(r.taps),
              },
              {
                accessor: "installs",
                title: "Installs",
                width: 90,
                textAlign: "right" as const,
                render: (r) => formatNumber(r.installs),
              },
              {
                accessor: "actions",
                title: "",
                width: 110,
                render: (r) => (
                  <Button
                    size="xs"
                    variant="light"
                    onClick={() => handleTrackOrganic(r.text)}
                    loading={
                      addKeyword.isPending &&
                      addKeyword.variables?.text === r.text
                    }
                  >
                    Track
                  </Button>
                ),
              },
            ]}
          />
        </Paper>
        <Paper withBorder radius="md" style={{ flex: 1 }}>
          <Group p="md" pb="xs">
            <IconAlertCircle size={16} color="var(--mantine-color-red-6)" />
            <Title order={5}>Negative-keyword candidates</Title>
          </Group>
          <Text size="xs" c="dimmed" px="md" mb="xs">
            Search terms with paid spend but very low conversion — consider blocking.
          </Text>
          <DataTable<ASANegativeCandidate>
            withTableBorder={false}
            striped
            minHeight={(negativeCandidates.data ?? []).length === 0 ? 120 : undefined}
            records={(negativeCandidates.data ?? []).slice(0, 5)}
            idAccessor="search_term_id"
            noRecordsText="Nothing to suggest yet."
            columns={[
              { accessor: "text", title: "Term" },
              {
                accessor: "spend",
                title: "Spend",
                width: 90,
                textAlign: "right" as const,
                render: (r) => formatMoney(r.spend, null),
              },
              {
                accessor: "conversion_rate",
                title: "CR",
                width: 70,
                textAlign: "right" as const,
                render: (r) => `${(r.conversion_rate * 100).toFixed(2)}%`,
              },
              {
                accessor: "actions",
                title: "",
                width: 110,
                render: (r) => (
                  <Button
                    size="xs"
                    variant="light"
                    color="red"
                    onClick={() => handleApplyNegative(r.text)}
                    disabled={defaultCampaignId === null}
                    loading={
                      addNegatives.isPending &&
                      addNegatives.variables?.body.keywords[0]?.text === r.text
                    }
                  >
                    Block
                  </Button>
                ),
              },
            ]}
          />
        </Paper>
      </Group>
    </Stack>
  );
}

// ----- Campaigns tab -----

function CampaignsTab({ appId }: { appId: number }) {
  const { data, isLoading } = useASACampaigns(appId);
  const [selectedCampaign, setSelectedCampaign] = useState<ASACampaignOut | null>(
    null,
  );
  const campaignPerformance = useASAPerformanceReport(
    appId,
    "CAMPAIGN",
    CAMPAIGN_BREAKDOWN_WINDOW_DAYS,
  );
  const adGroupPerformance = useASAPerformanceReport(
    appId,
    "AD_GROUP",
    CAMPAIGN_BREAKDOWN_WINDOW_DAYS,
  );

  const campaignMetrics = useMemo(
    () => buildPerformanceMap(campaignPerformance.data?.rows ?? []),
    [campaignPerformance.data?.rows],
  );

  return (
    <>
      <Paper withBorder radius="md">
        <Group justify="space-between" p="md" pb="xs" wrap="wrap">
          <div>
            <Title order={5}>Campaign performance</Title>
            <Text size="xs" c="dimmed">
              Click any campaign to inspect its ad groups without leaving the paid-search page. Performance reflects the last{" "}
              {CAMPAIGN_BREAKDOWN_WINDOW_DAYS} days.
            </Text>
          </div>
          {(campaignPerformance.isLoading || adGroupPerformance.isLoading) && (
            <Loader size="xs" />
          )}
        </Group>

        <DataTable<ASACampaignOut>
          withTableBorder={false}
          striped
          highlightOnHover
          fetching={isLoading || campaignPerformance.isLoading}
          minHeight={(data ?? []).length === 0 ? 200 : undefined}
          records={data ?? []}
          idAccessor="id"
          noRecordsText="No ASA campaigns linked to this app yet."
          onRowClick={({ record }) => setSelectedCampaign(record)}
          columns={[
            {
              accessor: "name",
              title: "Campaign",
              render: (r) => (
                <Stack gap={0}>
                  <Text size="sm" fw={500}>
                    {r.name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    Adam ID {r.app_adam_id}
                    {r.archived_at ? " · archived" : ""}
                  </Text>
                </Stack>
              ),
            },
            {
              accessor: "status",
              title: "Status",
              width: 110,
              render: (r) => (
                <Badge variant="light" color={statusColor(r.status)} size="sm">
                  {r.status}
                </Badge>
              ),
            },
            {
              accessor: "spend_30d",
              title: "Spend (30d)",
              width: 120,
              textAlign: "right" as const,
              render: (r) => {
                const metrics = campaignMetrics.get(r.id);
                return metrics ? (
                  <Text size="sm">
                    {formatMoney(metrics.spend, metrics.spendCurrency)}
                  </Text>
                ) : (
                  <Text size="sm" c="dimmed">
                    —
                  </Text>
                );
              },
            },
            {
              accessor: "installs_30d",
              title: "Installs",
              width: 90,
              textAlign: "right" as const,
              render: (r) => {
                const metrics = campaignMetrics.get(r.id);
                return (
                  <Text size="sm" c={metrics ? undefined : "dimmed"}>
                    {metrics ? formatNumber(metrics.installs) : "—"}
                  </Text>
                );
              },
            },
            {
              accessor: "taps_30d",
              title: "Taps",
              width: 90,
              textAlign: "right" as const,
              render: (r) => {
                const metrics = campaignMetrics.get(r.id);
                return (
                  <Text size="sm" c={metrics ? undefined : "dimmed"}>
                    {metrics ? formatNumber(metrics.taps) : "—"}
                  </Text>
                );
              },
            },
            {
              accessor: "daily_budget",
              title: "Daily budget",
              width: 140,
              render: (r) => (
                <Text size="sm" c={r.daily_budget_amount ? undefined : "dimmed"}>
                  {formatMoney(r.daily_budget_amount, r.daily_budget_currency)}
                </Text>
              ),
            },
          ]}
        />
      </Paper>

      <CampaignDrilldownDrawer
        appId={appId}
        campaign={selectedCampaign}
        performanceLoading={adGroupPerformance.isLoading}
        performanceRows={adGroupPerformance.data?.rows ?? []}
        onClose={() => setSelectedCampaign(null)}
      />
    </>
  );
}

function CampaignDrilldownDrawer({
  appId,
  campaign,
  performanceLoading,
  performanceRows,
  onClose,
}: {
  appId: number;
  campaign: ASACampaignOut | null;
  performanceLoading: boolean;
  performanceRows: ASAPerformanceReportRow[];
  onClose: () => void;
}) {
  const adGroups = useASAAdGroups(appId, campaign?.id ?? 0);

  const drilldown = useMemo(
    () => buildCampaignDrilldown(adGroups.data ?? [], performanceRows),
    [adGroups.data, performanceRows],
  );

  const loading = performanceLoading || adGroups.isLoading;

  return (
    <Drawer
      opened={campaign !== null}
      onClose={onClose}
      position="right"
      size="xl"
      title={
        campaign ? (
          <Group gap="xs" wrap="wrap">
            <Text fw={600} size="sm">
              {campaign.name}
            </Text>
            <Badge variant="light" color={statusColor(campaign.status)} size="sm">
              {campaign.status}
            </Badge>
            <Text size="xs" c="dimmed">
              Ad-group breakdown · last {CAMPAIGN_BREAKDOWN_WINDOW_DAYS} days
            </Text>
          </Group>
        ) : (
          "Campaign breakdown"
        )
      }
    >
      {!campaign ? null : (
        <Stack gap="md">
          {loading ? (
            <Group gap="xs">
              <Loader size="sm" />
              <Text size="sm" c="dimmed">
                Loading ad-group performance…
              </Text>
            </Group>
          ) : (
            <Group gap="md" wrap="wrap">
              <Paper withBorder p="md" miw={140}>
                <Text size="xs" c="dimmed">
                  Spend (30d)
                </Text>
                <Text fw={600}>
                  {formatMoney(
                    drilldown.totals.spend,
                    drilldown.totals.spendCurrency,
                  )}
                </Text>
              </Paper>
              <Paper withBorder p="md" miw={140}>
                <Text size="xs" c="dimmed">
                  Installs
                </Text>
                <Text fw={600}>{formatNumber(drilldown.totals.installs)}</Text>
              </Paper>
              <Paper withBorder p="md" miw={140}>
                <Text size="xs" c="dimmed">
                  Taps
                </Text>
                <Text fw={600}>{formatNumber(drilldown.totals.taps)}</Text>
              </Paper>
              <Paper withBorder p="md" miw={140}>
                <Text size="xs" c="dimmed">
                  Ad groups
                </Text>
                <Text fw={600}>{formatNumber(drilldown.rows.length)}</Text>
              </Paper>
            </Group>
          )}

          <Paper withBorder radius="md">
            <DataTable<CampaignDrilldownRow>
              withTableBorder={false}
              striped
              highlightOnHover
              fetching={loading}
              minHeight={drilldown.rows.length === 0 ? 200 : undefined}
              records={drilldown.rows}
              idAccessor="id"
              noRecordsText="No ad groups linked to this campaign yet."
              columns={[
                {
                  accessor: "name",
                  title: "Ad group",
                  render: (r) => (
                    <Stack gap={0}>
                      <Text size="sm" fw={500}>
                        {r.name}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {[r.deviceClass, r.gender].filter(Boolean).join(" · ") ||
                          `ASA ID ${r.asaAdGroupId}`}
                      </Text>
                    </Stack>
                  ),
                },
                {
                  accessor: "status",
                  title: "Status",
                  width: 110,
                  render: (r) => (
                    <Badge
                      variant="light"
                      color={r.archivedAt ? "gray" : statusColor(r.status)}
                      size="sm"
                    >
                      {r.archivedAt ? "ARCHIVED" : r.status}
                    </Badge>
                  ),
                },
                {
                  accessor: "defaultBidAmount",
                  title: "Bid",
                  width: 110,
                  render: (r) => (
                    <Text
                      size="sm"
                      c={r.defaultBidAmount ? undefined : "dimmed"}
                    >
                      {formatMoney(r.defaultBidAmount, r.defaultBidCurrency)}
                    </Text>
                  ),
                },
                {
                  accessor: "spend",
                  title: "Spend",
                  width: 100,
                  textAlign: "right" as const,
                  render: (r) => formatMoney(r.spend, r.spendCurrency),
                },
                {
                  accessor: "installs",
                  title: "Installs",
                  width: 90,
                  textAlign: "right" as const,
                  render: (r) => formatNumber(r.installs),
                },
                {
                  accessor: "taps",
                  title: "Taps",
                  width: 90,
                  textAlign: "right" as const,
                  render: (r) => formatNumber(r.taps),
                },
                {
                  accessor: "impressions",
                  title: "Imp.",
                  width: 90,
                  textAlign: "right" as const,
                  render: (r) => formatNumber(r.impressions),
                },
                {
                  accessor: "conversionRate",
                  title: "CR",
                  width: 80,
                  textAlign: "right" as const,
                  render: (r) => formatPercent(r.conversionRate),
                },
                {
                  accessor: "avgCpt",
                  title: "CPT",
                  width: 100,
                  textAlign: "right" as const,
                  render: (r) => formatMoney(r.avgCpt, r.spendCurrency),
                },
              ]}
            />
          </Paper>
        </Stack>
      )}
    </Drawer>
  );
}

// ----- Add-negative modal (used by Keywords + SearchTerms tabs) -----

interface NegModalState {
  defaultText: string;
  defaultMatchType: "BROAD" | "EXACT";
}

function AddNegativeModal({
  appId,
  state,
  campaigns,
  onClose,
}: {
  appId: number;
  state: NegModalState | null;
  campaigns: ASACampaignOut[];
  onClose: () => void;
}) {
  const [scope, setScope] = useState<"CAMPAIGN" | "AD_GROUP">("CAMPAIGN");
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [adGroupId, setAdGroupId] = useState<string | null>(null);
  const [matchType, setMatchType] = useState<"BROAD" | "EXACT">(
    state?.defaultMatchType ?? "EXACT",
  );

  const adGroups = useASAAdGroups(
    appId,
    scope === "AD_GROUP" && campaignId ? Number(campaignId) : 0,
  );

  const addNegatives = useAddNegativeKeywords();

  const handleSubmit = () => {
    if (!state) return;
    const scopeIdRaw = scope === "CAMPAIGN" ? campaignId : adGroupId;
    if (!scopeIdRaw) return;
    addNegatives.mutate(
      {
        app_id: appId,
        body: {
          scope,
          scope_id: Number(scopeIdRaw),
          keywords: [{ text: state.defaultText, match_type: matchType }],
        },
      },
      {
        onSuccess: () => onClose(),
      },
    );
  };

  const opened = state !== null;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Add negative keyword"
      centered
    >
      <Stack gap="sm">
        <Text size="sm">
          Block <strong>"{state?.defaultText ?? ""}"</strong> from triggering
          ads.
        </Text>
        <Radio.Group
          label="Scope"
          value={scope}
          onChange={(v) => setScope(v as "CAMPAIGN" | "AD_GROUP")}
        >
          <Group mt="xs">
            <Radio value="CAMPAIGN" label="Campaign" />
            <Radio value="AD_GROUP" label="Ad group" />
          </Group>
        </Radio.Group>

        <Select
          label="Campaign"
          data={campaigns.map((c) => ({
            value: String(c.id),
            label: c.name,
          }))}
          value={campaignId}
          onChange={setCampaignId}
          searchable
          required
        />

        {scope === "AD_GROUP" && (
          <Select
            label="Ad group"
            data={(adGroups.data ?? []).map((ag) => ({
              value: String(ag.id),
              label: ag.name,
            }))}
            value={adGroupId}
            onChange={setAdGroupId}
            searchable
            required
            disabled={!campaignId}
          />
        )}

        <Radio.Group
          label="Match type"
          value={matchType}
          onChange={(v) => setMatchType(v as "BROAD" | "EXACT")}
        >
          <Group mt="xs">
            <Radio value="EXACT" label="Exact" />
            <Radio value="BROAD" label="Broad" />
          </Group>
        </Radio.Group>

        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            loading={addNegatives.isPending}
            disabled={
              !campaignId || (scope === "AD_GROUP" && !adGroupId) || !state
            }
          >
            Add negative
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// ----- Keywords tab -----

function KeywordsTab({ appId }: { appId: number }) {
  const campaigns = useASACampaigns(appId);
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [adGroupId, setAdGroupId] = useState<string | null>(null);
  const [negState, setNegState] = useState<NegModalState | null>(null);

  const adGroups = useASAAdGroups(appId, campaignId ? Number(campaignId) : 0);
  const keywords = useASAAdGroupKeywords(
    appId,
    adGroupId ? Number(adGroupId) : 0,
  );

  return (
    <Stack gap="md">
      <Group gap="md">
        <Select
          label="Campaign"
          placeholder="Pick a campaign"
          data={(campaigns.data ?? []).map((c) => ({
            value: String(c.id),
            label: c.name,
          }))}
          value={campaignId}
          onChange={(v) => {
            setCampaignId(v);
            setAdGroupId(null);
          }}
          searchable
          style={{ minWidth: 240 }}
        />
        <Select
          label="Ad group"
          placeholder={campaignId ? "Pick an ad group" : "Pick a campaign first"}
          data={(adGroups.data ?? []).map((ag) => ({
            value: String(ag.id),
            label: ag.name,
          }))}
          value={adGroupId}
          onChange={setAdGroupId}
          searchable
          disabled={!campaignId}
          style={{ minWidth: 240 }}
        />
      </Group>

      <Paper withBorder radius="md">
        <DataTable<ASAKeywordOut>
          withTableBorder={false}
          striped
          highlightOnHover
          fetching={keywords.isLoading}
          minHeight={(keywords.data ?? []).length === 0 ? 200 : undefined}
          records={keywords.data ?? []}
          idAccessor="id"
          noRecordsText={
            adGroupId
              ? "No keywords in this ad group."
              : "Pick a campaign and ad group to list keywords."
          }
          columns={[
            { accessor: "text", title: "Keyword" },
            {
              accessor: "match_type",
              title: "Match",
              width: 90,
              render: (r) => (
                <Badge size="xs" variant="light" color="gray">
                  {r.match_type}
                </Badge>
              ),
            },
            {
              accessor: "bid",
              title: "Bid",
              width: 110,
              render: (r) =>
                r.bid_amount ? (
                  <Text size="sm">
                    {formatMoney(r.bid_amount, r.bid_currency)}
                  </Text>
                ) : (
                  <Text size="sm" c="dimmed">
                    —
                  </Text>
                ),
            },
            {
              accessor: "status",
              title: "Status",
              width: 100,
              render: (r) => (
                <Badge variant="light" color={statusColor(r.status)} size="sm">
                  {r.status}
                </Badge>
              ),
            },
            {
              accessor: "actions",
              title: "",
              width: 150,
              render: (r) => (
                <Button
                  size="xs"
                  variant="subtle"
                  color="red"
                  onClick={() =>
                    setNegState({
                      defaultText: r.text,
                      defaultMatchType: r.match_type,
                    })
                  }
                >
                  Add as negative
                </Button>
              ),
            },
          ]}
        />
      </Paper>

      <AddNegativeModal
        appId={appId}
        state={negState}
        campaigns={campaigns.data ?? []}
        onClose={() => setNegState(null)}
      />
    </Stack>
  );
}

// ----- Search terms tab -----

function SearchTermsTab({ appId }: { appId: number }) {
  const campaigns = useASACampaigns(appId);
  const [days, setDays] = useState<string>("30");
  const report = useASASearchTermReport(appId, Number(days));
  const [negState, setNegState] = useState<NegModalState | null>(null);
  const addKeyword = useAddKeyword();

  return (
    <Stack gap="md">
      <Group>
        <Select
          label="Range"
          data={[
            { value: "7", label: "Last 7 days" },
            { value: "30", label: "Last 30 days" },
            { value: "90", label: "Last 90 days" },
          ]}
          value={days}
          onChange={(v) => setDays(v ?? "30")}
          allowDeselect={false}
          w={160}
        />
      </Group>

      <Paper withBorder radius="md">
        <DataTable<ASASearchTermReportRow>
          withTableBorder={false}
          striped
          highlightOnHover
          fetching={report.isLoading}
          minHeight={(report.data?.rows ?? []).length === 0 ? 200 : undefined}
          records={report.data?.rows ?? []}
          idAccessor="search_term_id"
          noRecordsText="No paid search-term data in the selected range."
          columns={[
            { accessor: "text", title: "Search term" },
            {
              accessor: "match_type",
              title: "Match",
              width: 90,
              render: (r) => (
                <Badge size="xs" variant="light" color="gray">
                  {r.match_type}
                </Badge>
              ),
            },
            {
              accessor: "impressions",
              title: "Imp",
              width: 80,
              textAlign: "right" as const,
              render: (r) => formatNumber(r.impressions),
            },
            {
              accessor: "taps",
              title: "Taps",
              width: 80,
              textAlign: "right" as const,
              render: (r) => formatNumber(r.taps),
            },
            {
              accessor: "installs",
              title: "Inst.",
              width: 80,
              textAlign: "right" as const,
              render: (r) => formatNumber(r.installs),
            },
            {
              accessor: "spend",
              title: "Spend",
              width: 100,
              textAlign: "right" as const,
              render: (r) => formatMoney(r.spend, r.spend_currency),
            },
            {
              accessor: "actions",
              title: "",
              width: 220,
              render: (r) => (
                <Group gap={4} wrap="nowrap">
                  <Button
                    size="xs"
                    variant="subtle"
                    onClick={() =>
                      addKeyword.mutate({
                        appId: String(appId),
                        text: r.text,
                        locale: "en-US",
                      })
                    }
                    loading={
                      addKeyword.isPending &&
                      addKeyword.variables?.text === r.text
                    }
                  >
                    Track
                  </Button>
                  <Button
                    size="xs"
                    variant="subtle"
                    color="red"
                    onClick={() =>
                      setNegState({
                        defaultText: r.text,
                        defaultMatchType: "EXACT",
                      })
                    }
                  >
                    Block
                  </Button>
                </Group>
              ),
            },
          ]}
        />
      </Paper>

      <AddNegativeModal
        appId={appId}
        state={negState}
        campaigns={campaigns.data ?? []}
        onClose={() => setNegState(null)}
      />
    </Stack>
  );
}

// ----- Negatives tab -----

function NegativesTab({ appId }: { appId: number }) {
  const campaigns = useASACampaigns(appId);
  const [scope, setScope] = useState<"CAMPAIGN" | "AD_GROUP">("CAMPAIGN");
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [adGroupId, setAdGroupId] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");
  const [bulkMatch, setBulkMatch] = useState<"BROAD" | "EXACT">("EXACT");

  const adGroups = useASAAdGroups(appId, campaignId ? Number(campaignId) : 0);

  const scopeId =
    scope === "CAMPAIGN"
      ? campaignId
        ? Number(campaignId)
        : null
      : adGroupId
        ? Number(adGroupId)
        : null;

  const negatives = useASANegativeKeywords(appId, scope, scopeId);
  const addNegatives = useAddNegativeKeywords();
  const removeNegative = useRemoveNegativeKeyword();

  const handleBulkSubmit = () => {
    if (!scopeId) return;
    const lines = bulkText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (lines.length === 0) return;
    addNegatives.mutate(
      {
        app_id: appId,
        body: {
          scope,
          scope_id: scopeId,
          keywords: lines.map((l) => ({ text: l, match_type: bulkMatch })),
        },
      },
      {
        onSuccess: () => {
          setBulkOpen(false);
          setBulkText("");
        },
      },
    );
  };

  return (
    <Stack gap="md">
      <Group gap="md" wrap="wrap">
        <Radio.Group
          label="Scope"
          value={scope}
          onChange={(v) => {
            setScope(v as "CAMPAIGN" | "AD_GROUP");
            setAdGroupId(null);
          }}
        >
          <Group mt="xs">
            <Radio value="CAMPAIGN" label="Campaign" />
            <Radio value="AD_GROUP" label="Ad group" />
          </Group>
        </Radio.Group>

        <Select
          label="Campaign"
          data={(campaigns.data ?? []).map((c) => ({
            value: String(c.id),
            label: c.name,
          }))}
          value={campaignId}
          onChange={(v) => {
            setCampaignId(v);
            setAdGroupId(null);
          }}
          searchable
          style={{ minWidth: 240 }}
        />

        {scope === "AD_GROUP" && (
          <Select
            label="Ad group"
            data={(adGroups.data ?? []).map((ag) => ({
              value: String(ag.id),
              label: ag.name,
            }))}
            value={adGroupId}
            onChange={setAdGroupId}
            searchable
            disabled={!campaignId}
            style={{ minWidth: 240 }}
          />
        )}

        <Button
          mt="xl"
          size="sm"
          onClick={() => setBulkOpen(true)}
          disabled={!scopeId}
        >
          Bulk add
        </Button>
      </Group>

      <Paper withBorder radius="md">
        <DataTable<ASANegativeKeywordOut>
          withTableBorder={false}
          striped
          highlightOnHover
          fetching={negatives.isLoading}
          minHeight={(negatives.data ?? []).length === 0 ? 200 : undefined}
          records={negatives.data ?? []}
          idAccessor="id"
          noRecordsText={
            scopeId
              ? "No negatives at this scope yet."
              : "Pick a scope to see existing negatives."
          }
          columns={[
            { accessor: "text", title: "Keyword" },
            {
              accessor: "match_type",
              title: "Match",
              width: 90,
              render: (r) => (
                <Badge size="xs" variant="light" color="gray">
                  {r.match_type}
                </Badge>
              ),
            },
            {
              accessor: "scope",
              title: "Scope",
              width: 110,
              render: (r) => (
                <Badge size="xs" variant="light">
                  {r.scope}
                </Badge>
              ),
            },
            {
              accessor: "actions",
              title: "",
              width: 110,
              textAlign: "right" as const,
              render: (r) => (
                <Button
                  size="xs"
                  variant="subtle"
                  color="red"
                  onClick={() =>
                    removeNegative.mutate({
                      app_id: appId,
                      negative_id: r.id,
                    })
                  }
                  loading={
                    removeNegative.isPending &&
                    removeNegative.variables?.negative_id === r.id
                  }
                >
                  Remove
                </Button>
              ),
            },
          ]}
        />
      </Paper>

      <Modal
        opened={bulkOpen}
        onClose={() => setBulkOpen(false)}
        title="Bulk-add negatives"
        centered
      >
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            One keyword per line. Match type applies to all entries.
          </Text>
          <textarea
            rows={8}
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"crypto wallet\nfree gems\n..."}
            style={{
              width: "100%",
              fontFamily: "monospace",
              fontSize: 13,
              padding: 8,
              borderRadius: 6,
              border: "1px solid var(--mantine-color-gray-4)",
              resize: "vertical",
            }}
          />
          <Radio.Group
            label="Match type"
            value={bulkMatch}
            onChange={(v) => setBulkMatch(v as "BROAD" | "EXACT")}
          >
            <Group mt="xs">
              <Radio value="EXACT" label="Exact" />
              <Radio value="BROAD" label="Broad" />
            </Group>
          </Radio.Group>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setBulkOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleBulkSubmit}
              loading={addNegatives.isPending}
              disabled={!bulkText.trim() || !scopeId}
              leftSection={<IconCheck size={14} />}
            >
              Add
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

// ----- Page -----

export default function PaidSearchPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const { data: app } = useApp(id ?? "");
  const creds = useASACredentials();

  const noASA =
    !creds.isLoading && (creds.data ?? []).length === 0;

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-md)" }}>
        <Group gap="sm" align="center">
          <IconCash size={22} />
          <Title order={2}>{app?.name ?? "App"} — Paid Search</Title>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Apple Search Ads campaigns, keywords, search-terms and negatives —
          plus rule-based suggestions for what to track and what to block.
        </Text>
      </div>

      {noASA ? (
        <NoASAState />
      ) : (
        <Tabs defaultValue="overview">
          <Tabs.List>
            <Tabs.Tab
              value="overview"
              leftSection={<IconChartBar size={16} />}
            >
              Overview
            </Tabs.Tab>
            <Tabs.Tab
              value="campaigns"
              leftSection={<IconTargetArrow size={16} />}
            >
              Campaigns
            </Tabs.Tab>
            <Tabs.Tab
              value="keywords"
              leftSection={<IconKeyboard size={16} />}
            >
              Keywords
            </Tabs.Tab>
            <Tabs.Tab
              value="search-terms"
              leftSection={<IconSearch size={16} />}
            >
              Search terms
            </Tabs.Tab>
            <Tabs.Tab
              value="negatives"
              leftSection={<IconAlertCircle size={16} />}
            >
              Negatives
            </Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="overview" pt="md">
            <OverviewTab appId={appId} />
          </Tabs.Panel>
          <Tabs.Panel value="campaigns" pt="md">
            <CampaignsTab appId={appId} />
          </Tabs.Panel>
          <Tabs.Panel value="keywords" pt="md">
            <KeywordsTab appId={appId} />
          </Tabs.Panel>
          <Tabs.Panel value="search-terms" pt="md">
            <SearchTermsTab appId={appId} />
          </Tabs.Panel>
          <Tabs.Panel value="negatives" pt="md">
            <NegativesTab appId={appId} />
          </Tabs.Panel>
        </Tabs>
      )}
    </Container>
  );
}
