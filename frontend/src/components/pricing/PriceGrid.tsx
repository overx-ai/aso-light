import { useMemo, useState } from "react";
import {
  ActionIcon,
  Badge,
  NumberInput,
  TextInput,
  Group,
  Text,
  Paper,
} from "@mantine/core";
import { IconSearch, IconPin, IconPinFilled } from "@tabler/icons-react";
import { DataTable, type DataTableSortStatus } from "mantine-datatable";
import PriceDiffBadge from "@/components/pricing/PriceDiffBadge";
import type { PricePoint, PricePreviewItem } from "@/types";

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
}

interface PriceGridProps {
  prices: PricePoint[];
  previewItems: PricePreviewItem[] | null;
  isLoading: boolean;
  manualTerritories?: Set<string>;
  onToggleManual?: (territoryCode: string) => void;
  onManualPriceChange?: (territoryCode: string, price: number) => void;
}

function buildRows(
  prices: PricePoint[],
  previewItems: PricePreviewItem[] | null,
  manualTerritories?: Set<string>,
): PriceGridRow[] {
  const previewMap = new Map<string, PricePreviewItem>();
  if (previewItems) {
    for (const item of previewItems) {
      previewMap.set(item.territory_code, item);
    }
  }

  // If we have prices, merge with preview
  if (prices.length > 0) {
    return prices.map((p) => {
      const preview = previewMap.get(p.territory_code);
      const hasChange =
        preview !== undefined &&
        preview.diff_percent !== null &&
        Math.abs(preview.diff_percent) > 0.01;

      return {
        territory_code: p.territory_code,
        territory_name: p.territory_name,
        currency_code: p.currency_code,
        current_price: p.customer_price,
        proceeds: p.proceeds,
        suggested_price: preview?.nearest_apple_price ?? preview?.suggested_price ?? null,
        nearest_apple_price: preview?.nearest_apple_price ?? null,
        diff_percent: preview?.diff_percent ?? null,
        price_point_id: preview?.price_point_id ?? p.price_point_id,
        has_change: hasChange,
        would_be_skipped: preview?.would_be_skipped ?? false,
        is_manual: manualTerritories?.has(p.territory_code) ?? false,
      };
    });
  }

  // If only preview (no current prices yet)
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
}: PriceGridProps) {
  const [search, setSearch] = useState("");
  const [sortStatus, setSortStatus] = useState<DataTableSortStatus<PriceGridRow>>({
    columnAccessor: "territory_code",
    direction: "asc",
  });

  const hasPreview = previewItems !== null && previewItems.length > 0;

  const rows = useMemo(
    () => buildRows(prices, previewItems, manualTerritories),
    [prices, previewItems, manualTerritories],
  );

  const filteredRows = useMemo(() => {
    if (!search.trim()) return rows;
    const lower = search.toLowerCase();
    return rows.filter(
      (r) =>
        r.territory_code.toLowerCase().includes(lower) ||
        r.territory_name.toLowerCase().includes(lower) ||
        r.currency_code.toLowerCase().includes(lower),
    );
  }, [rows, search]);

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
        <Text size="xs" c="dimmed">
          {sortedRows.length} territories
          {hasPreview &&
            ` | ${rows.filter((r) => r.has_change && !r.would_be_skipped).length} with changes`}
          {hasPreview &&
            rows.filter((r) => r.would_be_skipped).length > 0 &&
            ` | ${rows.filter((r) => r.would_be_skipped).length} skipped`}
          {rows.filter((r) => r.is_manual).length > 0 &&
            ` | ${rows.filter((r) => r.is_manual).length} manual`}
        </Text>
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
          if (row.would_be_skipped) {
            return {
              backgroundColor: "var(--mantine-color-orange-0)",
              opacity: 0.7,
            };
          }
          if (row.has_change) {
            return { backgroundColor: "var(--mantine-color-yellow-0)" };
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
            render: (row: PriceGridRow) => (
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
        ]}
        noRecordsText="No pricing data available"
      />
    </Paper>
  );
}
