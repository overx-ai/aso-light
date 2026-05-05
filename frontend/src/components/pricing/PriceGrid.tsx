import { useMemo, useState } from "react";
import {
  ActionIcon,
  Badge,
  NumberInput,
  Switch,
  TextInput,
  Group,
  Text,
  Paper,
  Tooltip,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconBolt,
  IconPin,
  IconPinFilled,
  IconSearch,
} from "@tabler/icons-react";
import { DataTable, type DataTableSortStatus } from "mantine-datatable";
import PriceDiffBadge from "@/components/pricing/PriceDiffBadge";
import type {
  IntroOffer,
  IntroOfferDuration,
  PricePoint,
  PricePreviewItem,
} from "@/types";

const INTRO_DURATION_LABEL: Record<IntroOfferDuration, string> = {
  THREE_DAYS: "3d",
  ONE_WEEK: "1w",
  TWO_WEEKS: "2w",
  ONE_MONTH: "1mo",
  TWO_MONTHS: "2mo",
  THREE_MONTHS: "3mo",
  SIX_MONTHS: "6mo",
  ONE_YEAR: "1y",
};

function formatIntroLabel(offer: IntroOffer): string {
  const period = INTRO_DURATION_LABEL[offer.duration] ?? offer.duration;
  if (offer.offer_mode === "FREE_TRIAL") {
    const reps =
      offer.number_of_periods > 1 ? ` × ${offer.number_of_periods}` : "";
    return `Free ${period}${reps}`;
  }
  if (offer.offer_mode === "PAY_UP_FRONT") {
    return `Up-front ${period}`;
  }
  return `Pay/${period} × ${offer.number_of_periods}`;
}

interface PriceGridRow {
  territory_code: string;
  territory_name: string;
  currency_code: string;
  current_price: number | null;
  proceeds: number | null;
  suggested_price: number | null;
  nearest_apple_price: number | null;
  diff_percent: number | null;
  price_point_id: string | null;
  has_change: boolean;
  would_be_skipped: boolean;
  is_manual: boolean;
  is_forced: boolean;
  /** Territory is in the sub's availability list but has no price. */
  is_missing_price: boolean;
}

interface PriceGridProps {
  prices: PricePoint[];
  previewItems: PricePreviewItem[] | null;
  isLoading: boolean;
  manualTerritories?: Set<string>;
  onToggleManual?: (territoryCode: string) => void;
  onManualPriceChange?: (territoryCode: string, price: number) => void;
  forcedTerritories?: Set<string>;
  onToggleForce?: (territoryCode: string) => void;
  /** Subscription introductory offers, keyed by alpha-2 territory_code on render. */
  introOffers?: IntroOffer[];
  /** Alpha-2 codes the sub is available in. Drives missing-price flagging. */
  availableTerritories?: string[];
  /** alpha-2 → display name; used to render rows for unpriced territories. */
  territoryNameMap?: Map<string, string>;
}

function buildRows(
  prices: PricePoint[],
  previewItems: PricePreviewItem[] | null,
  manualTerritories?: Set<string>,
  forcedTerritories?: Set<string>,
  availableTerritories?: string[],
  territoryNameMap?: Map<string, string>,
): PriceGridRow[] {
  const previewMap = new Map<string, PricePreviewItem>();
  if (previewItems) {
    for (const item of previewItems) {
      previewMap.set(item.territory_code, item);
    }
  }
  const priceMap = new Map<string, PricePoint>();
  for (const p of prices) {
    priceMap.set(p.territory_code, p);
  }
  const availSet = new Set(availableTerritories ?? []);

  // Union of territories from prices, preview, and the sub's availability.
  // Including availability surfaces "available but unpriced" rows so the
  // user can see gaps that previously hid silently.
  const allCodes = new Set<string>();
  for (const code of priceMap.keys()) allCodes.add(code);
  for (const code of previewMap.keys()) allCodes.add(code);
  for (const code of availSet) allCodes.add(code);

  if (allCodes.size > 0) {
    const rows: PriceGridRow[] = [];
    for (const code of allCodes) {
      const p = priceMap.get(code);
      const preview = previewMap.get(code);
      const territoryName =
        p?.territory_name ??
        preview?.territory_name ??
        territoryNameMap?.get(code) ??
        code;
      const currency =
        p?.currency_code ?? preview?.currency_code ?? "";
      const hasChange =
        preview !== undefined &&
        preview.diff_percent !== null &&
        Math.abs(preview.diff_percent) > 0.01;
      const isMissingPrice = availSet.has(code) && p === undefined;
      rows.push({
        territory_code: code,
        territory_name: territoryName,
        currency_code: currency,
        current_price: p?.customer_price ?? null,
        proceeds: p?.proceeds ?? null,
        suggested_price:
          preview?.nearest_apple_price ?? preview?.suggested_price ?? null,
        nearest_apple_price: preview?.nearest_apple_price ?? null,
        diff_percent: preview?.diff_percent ?? null,
        price_point_id: preview?.price_point_id ?? p?.price_point_id ?? null,
        has_change: hasChange,
        would_be_skipped: preview?.would_be_skipped ?? false,
        is_manual: manualTerritories?.has(code) ?? false,
        is_forced: forcedTerritories?.has(code) ?? false,
        is_missing_price: isMissingPrice,
      });
    }
    return rows;
  }

  // Empty state — keep the legacy fall-through for safety.
  if (previewItems) {
    return previewItems.map((item) => ({
      territory_code: item.territory_code,
      territory_name: item.territory_name,
      currency_code: item.currency_code,
      current_price: item.current_price,
      proceeds: null,
      suggested_price: item.nearest_apple_price ?? item.suggested_price,
      nearest_apple_price: item.nearest_apple_price,
      diff_percent: item.diff_percent,
      price_point_id: item.price_point_id,
      has_change:
        item.diff_percent !== null && Math.abs(item.diff_percent) > 0.01,
      would_be_skipped: item.would_be_skipped,
      is_manual: manualTerritories?.has(item.territory_code) ?? false,
      is_forced: forcedTerritories?.has(item.territory_code) ?? false,
      is_missing_price: false,
    }));
  }

  return [];
}

function formatPrice(value: number | null, currency: string): string {
  if (value === null) return "-";
  return `${currency} ${value.toFixed(2)}`;
}

type SortableField = keyof PriceGridRow;

function sortRows(
  rows: PriceGridRow[],
  sortStatus: DataTableSortStatus<PriceGridRow>,
): PriceGridRow[] {
  const { columnAccessor, direction } = sortStatus;
  const field = columnAccessor as SortableField;
  const sorted = [...rows].sort((a, b) => {
    const aVal = a[field];
    const bVal = b[field];

    if (aVal === null && bVal === null) return 0;
    if (aVal === null) return 1;
    if (bVal === null) return -1;

    if (typeof aVal === "string" && typeof bVal === "string") {
      return aVal.localeCompare(bVal);
    }
    if (typeof aVal === "number" && typeof bVal === "number") {
      return aVal - bVal;
    }
    if (typeof aVal === "boolean" && typeof bVal === "boolean") {
      return aVal === bVal ? 0 : aVal ? -1 : 1;
    }
    return 0;
  });

  return direction === "desc" ? sorted.reverse() : sorted;
}

export default function PriceGrid({
  prices,
  previewItems,
  isLoading,
  manualTerritories,
  onToggleManual,
  onManualPriceChange,
  forcedTerritories,
  onToggleForce,
  introOffers,
  availableTerritories,
  territoryNameMap,
}: PriceGridProps) {
  const [search, setSearch] = useState("");
  const [missingOnly, setMissingOnly] = useState(false);
  const [sortStatus, setSortStatus] = useState<DataTableSortStatus<PriceGridRow>>({
    columnAccessor: "territory_code",
    direction: "asc",
  });

  const introOfferByTerritory = useMemo(() => {
    const m = new Map<string, IntroOffer>();
    for (const offer of introOffers ?? []) {
      if (offer.territory_code) m.set(offer.territory_code, offer);
    }
    return m;
  }, [introOffers]);
  const hasIntroOffers = introOfferByTerritory.size > 0;

  const hasPreview = previewItems !== null && previewItems.length > 0;

  const rows = useMemo(
    () =>
      buildRows(
        prices,
        previewItems,
        manualTerritories,
        forcedTerritories,
        availableTerritories,
        territoryNameMap,
      ),
    [
      prices,
      previewItems,
      manualTerritories,
      forcedTerritories,
      availableTerritories,
      territoryNameMap,
    ],
  );

  const missingCount = useMemo(
    () => rows.filter((r) => r.is_missing_price).length,
    [rows],
  );

  const filteredRows = useMemo(() => {
    let filtered = rows;
    if (missingOnly) filtered = filtered.filter((r) => r.is_missing_price);
    if (!search.trim()) return filtered;
    const lower = search.toLowerCase();
    return filtered.filter(
      (r) =>
        r.territory_code.toLowerCase().includes(lower) ||
        r.territory_name.toLowerCase().includes(lower) ||
        r.currency_code.toLowerCase().includes(lower),
    );
  }, [rows, search, missingOnly]);

  const sortedRows = useMemo(
    () => sortRows(filteredRows, sortStatus),
    [filteredRows, sortStatus],
  );

  return (
    <Paper withBorder radius="md">
      <Group p="md" pb={0}>
        <TextInput
          placeholder="Search by territory or currency..."
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          style={{ flex: 1, maxWidth: 400 }}
          size="sm"
        />
        {missingCount > 0 && (
          <Switch
            size="sm"
            label={`Missing prices (${missingCount})`}
            color="red"
            checked={missingOnly}
            onChange={(e) => setMissingOnly(e.currentTarget.checked)}
          />
        )}
        <Group gap="xs">
          <Badge size="lg" variant="light" color="blue">
            {sortedRows.length === rows.length
              ? `${rows.length} territories`
              : `${sortedRows.length} of ${rows.length} territories`}
          </Badge>
          {missingCount > 0 && (
            <Badge size="lg" variant="light" color="red">
              {missingCount} missing
            </Badge>
          )}
          {hasPreview &&
            rows.filter((r) => r.has_change && !r.would_be_skipped).length >
              0 && (
              <Badge size="lg" variant="light" color="yellow">
                {
                  rows.filter((r) => r.has_change && !r.would_be_skipped)
                    .length
                }{" "}
                with changes
              </Badge>
            )}
          {hasPreview &&
            rows.filter((r) => r.would_be_skipped).length > 0 && (
              <Badge size="lg" variant="light" color="orange">
                {rows.filter((r) => r.would_be_skipped).length} skipped
              </Badge>
            )}
          {rows.filter((r) => r.is_manual).length > 0 && (
            <Badge size="lg" variant="light" color="grape">
              {rows.filter((r) => r.is_manual).length} manual
            </Badge>
          )}
        </Group>
      </Group>

      <DataTable
        withTableBorder={false}
        borderRadius="md"
        striped
        highlightOnHover
        minHeight={200}
        fetching={isLoading}
        records={sortedRows}
        idAccessor="territory_code"
        sortStatus={sortStatus}
        onSortStatusChange={setSortStatus}
        rowStyle={(row: PriceGridRow) => {
          if (row.is_manual) {
            return {
              backgroundColor: "var(--mantine-color-blue-0)",
              borderLeft: "3px solid var(--mantine-color-blue-5)",
            };
          }
          if (row.would_be_skipped && row.is_forced) {
            return {
              backgroundColor: "var(--mantine-color-red-0)",
              borderLeft: "3px solid var(--mantine-color-red-6)",
            };
          }
          if (row.would_be_skipped) {
            return {
              backgroundColor: "var(--mantine-color-orange-0)",
              opacity: 0.7,
            };
          }
          if (row.has_change) {
            return { backgroundColor: "var(--mantine-color-yellow-0)" };
          }
          if (row.is_missing_price) {
            return {
              backgroundColor: "var(--mantine-color-red-0)",
              borderLeft: "3px solid var(--mantine-color-red-6)",
            };
          }
          return undefined;
        }}
        columns={[
          ...(onToggleManual
            ? [
                {
                  accessor: "pin" as const,
                  title: "",
                  width: 36,
                  render: (row: PriceGridRow) => (
                    <ActionIcon
                      size="xs"
                      variant="subtle"
                      color={row.is_manual ? "blue" : "gray"}
                      onClick={() => onToggleManual(row.territory_code)}
                    >
                      {row.is_manual ? (
                        <IconPinFilled size={14} />
                      ) : (
                        <IconPin size={14} />
                      )}
                    </ActionIcon>
                  ),
                },
              ]
            : []),
          {
            accessor: "territory_code",
            title: "Territory",
            sortable: true,
            width: 200,
            render: (row: PriceGridRow) => (
              <Group gap="xs">
                <Text fw={500} size="sm">
                  {row.territory_code}
                </Text>
                <Text size="xs" c="dimmed">
                  {row.territory_name}
                </Text>
              </Group>
            ),
          },
          {
            accessor: "currency_code",
            title: "Currency",
            sortable: true,
            width: 90,
            render: (row: PriceGridRow) => (
              <Text size="sm">{row.currency_code}</Text>
            ),
          },
          {
            accessor: "current_price",
            title: "Current Price",
            sortable: true,
            textAlign: "right" as const,
            width: 130,
            render: (row: PriceGridRow) =>
              row.is_missing_price ? (
                <Tooltip
                  label="Sub is available here but no price set"
                  withArrow
                >
                  <Badge
                    size="sm"
                    variant="light"
                    color="red"
                    leftSection={<IconAlertTriangle size={12} />}
                  >
                    No price
                  </Badge>
                </Tooltip>
              ) : (
                <Text size="sm">
                  {formatPrice(row.current_price, row.currency_code)}
                </Text>
              ),
          },
          ...(hasPreview
            ? [
                {
                  accessor: "suggested_price" as const,
                  title: "Suggested Price",
                  sortable: true,
                  textAlign: "right" as const,
                  width: 160,
                  render: (row: PriceGridRow) =>
                    row.is_manual && onManualPriceChange ? (
                      <NumberInput
                        size="xs"
                        w={120}
                        min={0}
                        step={0.01}
                        decimalScale={2}
                        defaultValue={row.suggested_price ?? row.current_price ?? undefined}
                        onBlur={(e) => {
                          const val = parseFloat(e.currentTarget.value);
                          if (!isNaN(val) && val > 0) {
                            onManualPriceChange(row.territory_code, val);
                          }
                        }}
                        styles={{ input: { textAlign: "right" } }}
                      />
                    ) : (
                      <Text
                        size="sm"
                        fw={row.has_change ? 600 : undefined}
                        c={row.has_change ? "blue" : undefined}
                      >
                        {formatPrice(row.suggested_price, row.currency_code)}
                      </Text>
                    ),
                },
                {
                  accessor: "diff_percent" as const,
                  title: "Diff",
                  sortable: true,
                  textAlign: "center" as const,
                  width: 100,
                  render: (row: PriceGridRow) =>
                    row.would_be_skipped ? (
                      <Badge size="xs" color="orange" variant="light">
                        Skipped
                      </Badge>
                    ) : (
                      <PriceDiffBadge diffPercent={row.diff_percent} />
                    ),
                },
              ]
            : []),
          {
            accessor: "proceeds",
            title: "Proceeds",
            sortable: true,
            textAlign: "right" as const,
            width: 130,
            render: (row: PriceGridRow) => (
              <Text size="sm" c="dimmed">
                {formatPrice(row.proceeds, row.currency_code)}
              </Text>
            ),
          },
          ...(hasIntroOffers
            ? [
                {
                  accessor: "intro_offer" as const,
                  title: "Intro Offer",
                  textAlign: "right" as const,
                  width: 130,
                  render: (row: PriceGridRow) => {
                    const offer = introOfferByTerritory.get(row.territory_code);
                    if (!offer) {
                      return (
                        <Text size="xs" c="dimmed">
                          —
                        </Text>
                      );
                    }
                    return (
                      <Badge size="sm" variant="light" color="grape">
                        {formatIntroLabel(offer)}
                      </Badge>
                    );
                  },
                },
              ]
            : []),
          ...(onToggleForce
            ? [
                {
                  accessor: "force" as const,
                  title: "",
                  width: 44,
                  render: (row: PriceGridRow) => {
                    if (!row.would_be_skipped && !row.is_forced) return null;
                    return (
                      <Tooltip
                        label={
                          row.is_forced
                            ? "Force-applied: bypassing ±50% safety. Click to undo."
                            : "Skipped by ±50% safety. Click to force-apply."
                        }
                      >
                        <ActionIcon
                          size="sm"
                          variant={row.is_forced ? "filled" : "subtle"}
                          color={row.is_forced ? "red" : "gray"}
                          onClick={() => onToggleForce(row.territory_code)}
                        >
                          <IconBolt size={14} />
                        </ActionIcon>
                      </Tooltip>
                    );
                  },
                },
              ]
            : []),
        ]}
        noRecordsText="No pricing data available"
      />
    </Paper>
  );
}
