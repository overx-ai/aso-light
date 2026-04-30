import { useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconRefresh,
  IconSearch,
} from "@tabler/icons-react";
import { DataTable, type DataTableSortStatus } from "mantine-datatable";
import { useGDPData, useRefreshGDP } from "@/lib/hooks";
import type { GDPBracketConfig, GDPDataRow, GDPTier } from "@/types";

interface GDPBracketEditorProps {
  opened: boolean;
  onClose: () => void;
  value: GDPBracketConfig;
  onChange: (next: GDPBracketConfig) => void;
}

const TIERS: readonly GDPTier[] = ["top", "mid", "low", "special"];

const TIER_LABELS: Record<GDPTier, string> = {
  top: "Top",
  mid: "Mid",
  low: "Low",
  special: "Special",
};

const TIER_COLORS: Record<GDPTier, string> = {
  top: "blue",
  mid: "teal",
  low: "gray",
  special: "violet",
};

const OVERRIDE_OPTIONS = [
  { value: "_auto", label: "Auto" },
  ...TIERS.map((tier) => ({ value: tier, label: TIER_LABELS[tier] })),
];

function resolveTier(row: GDPDataRow, config: GDPBracketConfig): GDPTier {
  if (config.special_territories.includes(row.territory_code)) return "special";
  const manual = config.manual_overrides[row.territory_code];
  if (manual) return manual;
  const gdp = row.gdp_per_capita_ppp;
  if (gdp == null) return "low";
  if (gdp >= config.tier_thresholds_usd.top_min) return "top";
  if (gdp >= config.tier_thresholds_usd.mid_min) return "mid";
  return "low";
}

function formatGDP(value: number | null): string {
  return value == null ? "—" : `$${Math.round(value).toLocaleString()}`;
}

export default function GDPBracketEditor({
  opened,
  onClose,
  value,
  onChange,
}: GDPBracketEditorProps) {
  const { data: gdpData = [], isLoading } = useGDPData();
  const refreshGDP = useRefreshGDP();
  const [search, setSearch] = useState("");
  const [sortStatus, setSortStatus] = useState<DataTableSortStatus<GDPDataRow>>({
    columnAccessor: "gdp_per_capita_ppp",
    direction: "desc",
  });

  const territoryOptions = useMemo(
    () =>
      gdpData.map((r) => ({
        value: r.territory_code,
        label: `${r.territory_code} - ${r.territory_name}`,
      })),
    [gdpData],
  );

  const filteredRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = q
      ? gdpData.filter(
          (r) =>
            r.territory_code.toLowerCase().includes(q) ||
            r.territory_name.toLowerCase().includes(q),
        )
      : gdpData;
    const accessor = sortStatus.columnAccessor as keyof GDPDataRow;
    const sorted = [...rows].sort((a, b) => {
      const av = a[accessor];
      const bv = b[accessor];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return av - bv;
      return String(av).localeCompare(String(bv));
    });
    if (sortStatus.direction === "desc") sorted.reverse();
    return sorted;
  }, [gdpData, search, sortStatus]);

  const tierCounts = useMemo(() => {
    const counts: Record<GDPTier, number> = { top: 0, mid: 0, low: 0, special: 0 };
    for (const row of gdpData) {
      counts[resolveTier(row, value)]++;
    }
    return counts;
  }, [gdpData, value]);

  function toNumber(input: number | string): number | null {
    const num = typeof input === "string" ? parseFloat(input) : input;
    return isNaN(num) ? null : num;
  }

  const updatePrice = (tier: GDPTier, price: number | string) => {
    const num = toNumber(price);
    if (num === null) return;
    onChange({
      ...value,
      tier_prices_usd: { ...value.tier_prices_usd, [tier]: num },
    });
  };

  const updateThreshold = (key: "top_min" | "mid_min", input: number | string) => {
    const num = toNumber(input);
    if (num === null) return;
    onChange({
      ...value,
      tier_thresholds_usd: { ...value.tier_thresholds_usd, [key]: num },
    });
  };

  const updateOverride = (territoryCode: string, tier: string | null) => {
    const next = { ...value.manual_overrides };
    if (!tier || tier === "_auto") {
      delete next[territoryCode];
    } else {
      next[territoryCode] = tier as GDPTier;
    }
    onChange({ ...value, manual_overrides: next });
  };

  const updateSpecial = (codes: string[]) => {
    onChange({ ...value, special_territories: codes });
  };

  const thresholdsValid =
    value.tier_thresholds_usd.top_min > value.tier_thresholds_usd.mid_min;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Configure GDP Brackets"
      size="xl"
      scrollAreaComponent={undefined}
    >
      <Stack gap="md">
        <Paper withBorder p="md" radius="md">
          <Stack gap="xs">
            <Text fw={600} size="sm">Tier Prices (USD)</Text>
            <Group grow>
              {TIERS.map((tier) => (
                <NumberInput
                  key={tier}
                  label={TIER_LABELS[tier]}
                  value={value.tier_prices_usd[tier]}
                  onChange={(v) => updatePrice(tier, v)}
                  min={0.01}
                  step={0.01}
                  decimalScale={2}
                  fixedDecimalScale
                  prefix="$"
                  size="sm"
                />
              ))}
            </Group>
          </Stack>
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Stack gap="xs">
            <Group justify="space-between">
              <Text fw={600} size="sm">GDP/capita PPP Thresholds (USD)</Text>
              <Group gap="xs">
                {TIERS.map((tier) => (
                  <Badge
                    key={tier}
                    color={TIER_COLORS[tier]}
                    variant="light"
                    size="sm"
                  >
                    {TIER_LABELS[tier]}: {tierCounts[tier]}
                  </Badge>
                ))}
              </Group>
            </Group>
            <Group grow>
              <NumberInput
                label="Top tier min"
                description="Territories with GDP ≥ this amount → Top"
                value={value.tier_thresholds_usd.top_min}
                onChange={(v) => updateThreshold("top_min", v)}
                min={0}
                step={1000}
                thousandSeparator=","
                prefix="$"
                size="sm"
              />
              <NumberInput
                label="Mid tier min"
                description="Territories with GDP ≥ this amount → Mid"
                value={value.tier_thresholds_usd.mid_min}
                onChange={(v) => updateThreshold("mid_min", v)}
                min={0}
                step={1000}
                thousandSeparator=","
                prefix="$"
                size="sm"
              />
            </Group>
            {!thresholdsValid && (
              <Alert color="red" icon={<IconAlertCircle size={16} />}>
                Top threshold must be greater than Mid threshold.
              </Alert>
            )}
          </Stack>
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Stack gap="xs">
            <Text fw={600} size="sm">Special Tier (overrides GDP)</Text>
            <MultiSelect
              data={territoryOptions}
              value={value.special_territories}
              onChange={updateSpecial}
              placeholder="Select territories for the special tier"
              searchable
              clearable
              size="sm"
            />
          </Stack>
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Stack gap="xs">
            <Group justify="space-between">
              <Text fw={600} size="sm">Territories</Text>
              <Group gap="xs">
                <TextInput
                  placeholder="Search..."
                  value={search}
                  onChange={(e) => setSearch(e.currentTarget.value)}
                  leftSection={<IconSearch size={14} />}
                  size="xs"
                  w={180}
                />
                <Button
                  leftSection={<IconRefresh size={14} />}
                  onClick={() => refreshGDP.mutate()}
                  loading={refreshGDP.isPending}
                  variant="light"
                  size="xs"
                >
                  Refresh GDP data
                </Button>
              </Group>
            </Group>
            {gdpData.length === 0 && !isLoading && (
              <Alert color="yellow" icon={<IconAlertCircle size={16} />}>
                No GDP data yet. Click "Refresh GDP data" to fetch from the
                World Bank.
              </Alert>
            )}
            <DataTable
              minHeight={300}
              height={400}
              fetching={isLoading}
              records={filteredRows}
              idAccessor="territory_code"
              sortStatus={sortStatus}
              onSortStatusChange={setSortStatus}
              striped
              highlightOnHover
              columns={[
                {
                  accessor: "territory_code",
                  title: "Code",
                  sortable: true,
                  width: 70,
                },
                {
                  accessor: "territory_name",
                  title: "Territory",
                  sortable: true,
                },
                {
                  accessor: "currency_code",
                  title: "Currency",
                  sortable: true,
                  width: 90,
                },
                {
                  accessor: "gdp_per_capita_ppp",
                  title: "GDP/capita (PPP)",
                  sortable: true,
                  textAlign: "right",
                  width: 140,
                  render: (r) => formatGDP(r.gdp_per_capita_ppp),
                },
                {
                  accessor: "_tier",
                  title: "Tier",
                  width: 110,
                  render: (r) => {
                    const tier = resolveTier(r, value);
                    return (
                      <Badge color={TIER_COLORS[tier]} variant="filled" size="sm">
                        {TIER_LABELS[tier]}
                      </Badge>
                    );
                  },
                },
                {
                  accessor: "_override",
                  title: "Override",
                  width: 130,
                  render: (r) => {
                    const isSpecial = value.special_territories.includes(
                      r.territory_code,
                    );
                    const current =
                      value.manual_overrides[r.territory_code] ?? "_auto";
                    return (
                      <Select
                        data={OVERRIDE_OPTIONS}
                        value={current}
                        onChange={(v) => updateOverride(r.territory_code, v)}
                        disabled={isSpecial}
                        size="xs"
                        comboboxProps={{ withinPortal: true }}
                      />
                    );
                  },
                },
              ]}
            />
          </Stack>
        </Paper>

        <Group justify="flex-end">
          <Button onClick={onClose} disabled={!thresholdsValid}>
            Done
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
