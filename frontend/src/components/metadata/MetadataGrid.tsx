import { useMemo } from "react";
import { Badge, Button, Group, Stack, Text, Tooltip } from "@mantine/core";
import { IconCheck, IconPlus, IconUpload } from "@tabler/icons-react";
import { DataTable } from "mantine-datatable";
import {
  useAddKeyword,
  useTrackedKeywords,
} from "@/lib/hooks";
import type {
  AppMetadataLocalization,
  AppMetadataSnapshot,
  KeywordTrackingResponse,
} from "@/types";
import { localeLabel } from "@/components/metadata/localeLabel";

interface MetadataGridProps {
  appId: number;
  snapshot: AppMetadataSnapshot;
  onRowClick: (locale: string) => void;
  onOpenBulk: () => void;
}

interface GridRow {
  locale: string;
  name: string;
  subtitle: string;
  keywordList: string[];
  promotional_text: string;
}

function truncate(s: string | null, max = 60): string {
  if (!s) return "";
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

function parseKeywords(s: string | null): string[] {
  if (!s) return [];
  return s
    .split(",")
    .map((k) => k.trim())
    .filter((k) => k.length > 0);
}

function buildRows(snapshot: AppMetadataSnapshot): GridRow[] {
  const map = new Map<string, GridRow>();

  const ensure = (locale: string): GridRow => {
    let row = map.get(locale);
    if (!row) {
      row = {
        locale,
        name: "",
        subtitle: "",
        keywordList: [],
        promotional_text: "",
      };
      map.set(locale, row);
    }
    return row;
  };

  for (const r of snapshot.app_info as AppMetadataLocalization[]) {
    const row = ensure(r.locale);
    row.name = r.name ?? "";
    row.subtitle = r.subtitle ?? "";
  }
  for (const r of snapshot.versions as AppMetadataLocalization[]) {
    const row = ensure(r.locale);
    row.keywordList = parseKeywords(r.keywords);
    row.promotional_text = r.promotional_text ?? "";
  }
  return Array.from(map.values()).sort((a, b) =>
    a.locale.localeCompare(b.locale),
  );
}

function trackedKey(locale: string, text: string): string {
  return `${locale.toLowerCase()}:${text.toLowerCase().trim()}`;
}

function buildTrackedSet(
  tracked: KeywordTrackingResponse[] | undefined,
): Set<string> {
  const out = new Set<string>();
  if (!tracked) return out;
  for (const t of tracked) {
    out.add(trackedKey(t.keyword.locale, t.keyword.text));
  }
  return out;
}

interface KeywordChipProps {
  text: string;
  isTracked: boolean;
  isPending: boolean;
  onAdd: () => void;
}

function KeywordChip({ text, isTracked, isPending, onAdd }: KeywordChipProps) {
  const tooltip = isTracked
    ? "Already tracked"
    : isPending
      ? "Adding…"
      : "Click to add to monitoring";
  return (
    <Tooltip label={tooltip} withArrow openDelay={300}>
      <Badge
        size="sm"
        radius="sm"
        variant={isTracked ? "filled" : "light"}
        color={isTracked ? "green" : "blue"}
        leftSection={
          isTracked ? <IconCheck size={10} /> : <IconPlus size={10} />
        }
        style={{
          cursor: isTracked || isPending ? "default" : "pointer",
          textTransform: "none",
          opacity: isPending ? 0.6 : 1,
        }}
        onClick={(e) => {
          e.stopPropagation();
          if (!isTracked && !isPending) onAdd();
        }}
      >
        {text}
      </Badge>
    </Tooltip>
  );
}

export default function MetadataGrid({
  appId,
  snapshot,
  onRowClick,
  onOpenBulk,
}: MetadataGridProps) {
  const records = useMemo(() => buildRows(snapshot), [snapshot]);
  const { data: tracked } = useTrackedKeywords(String(appId));
  const trackedSet = useMemo(() => buildTrackedSet(tracked), [tracked]);
  const addKeyword = useAddKeyword();

  const totalKeywords = useMemo(
    () => records.reduce((sum, r) => sum + r.keywordList.length, 0),
    [records],
  );
  const trackedKeywordsHere = useMemo(
    () =>
      records.reduce(
        (sum, r) =>
          sum +
          r.keywordList.filter((kw) => trackedSet.has(trackedKey(r.locale, kw)))
            .length,
        0,
      ),
    [records, trackedSet],
  );

  return (
    <Stack gap="md" mt="md">
      <Group justify="space-between">
        <Group gap="xs">
          <Text size="sm" c="dimmed">
            {records.length} locales · click a row to edit it.
          </Text>
          <Badge variant="light" color="green" size="sm">
            {trackedKeywordsHere}/{totalKeywords} tracked
          </Badge>
        </Group>
        <Button
          variant="light"
          leftSection={<IconUpload size={16} />}
          onClick={onOpenBulk}
        >
          Bulk fan-out
        </Button>
      </Group>
      <DataTable<GridRow>
        striped
        highlightOnHover
        withTableBorder
        records={records}
        idAccessor="locale"
        onRowClick={({ record }) => onRowClick(record.locale)}
        columns={[
          {
            accessor: "locale",
            title: "Locale",
            width: 180,
            render: (r) => (
              <Stack gap={0}>
                <Text size="sm" fw={500}>
                  {localeLabel(r.locale)}
                </Text>
                <Text size="xs" c="dimmed">
                  {r.locale}
                </Text>
              </Stack>
            ),
          },
          {
            accessor: "name",
            title: "Name",
            width: 200,
            render: (r) => (
              <Text size="xs" lineClamp={2}>
                {truncate(r.name, 80)}
              </Text>
            ),
          },
          {
            accessor: "subtitle",
            title: "Subtitle",
            width: 200,
            render: (r) => (
              <Text size="xs" lineClamp={2} c="dimmed">
                {truncate(r.subtitle, 80)}
              </Text>
            ),
          },
          {
            accessor: "keywords",
            title: "Keywords",
            render: (r) => {
              if (r.keywordList.length === 0) {
                return (
                  <Text size="xs" c="dimmed">
                    —
                  </Text>
                );
              }
              return (
                <Group gap={4}>
                  {r.keywordList.map((kw) => {
                    const key = trackedKey(r.locale, kw);
                    const isTracked = trackedSet.has(key);
                    const isPending =
                      addKeyword.isPending &&
                      addKeyword.variables?.appId === String(appId) &&
                      addKeyword.variables?.text === kw &&
                      addKeyword.variables?.locale === r.locale;
                    return (
                      <KeywordChip
                        key={kw}
                        text={kw}
                        isTracked={isTracked}
                        isPending={isPending}
                        onAdd={() =>
                          addKeyword.mutate({
                            appId: String(appId),
                            text: kw,
                            locale: r.locale,
                          })
                        }
                      />
                    );
                  })}
                </Group>
              );
            },
          },
          {
            accessor: "promotional_text",
            title: "Promo text",
            width: 240,
            render: (r) => (
              <Text size="xs" lineClamp={3} c="dimmed">
                {truncate(r.promotional_text, 140)}
              </Text>
            ),
          },
        ]}
      />
    </Stack>
  );
}
