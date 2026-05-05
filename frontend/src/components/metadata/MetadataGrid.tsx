import { useMemo } from "react";
import { Button, Group, Stack, Text } from "@mantine/core";
import { IconUpload } from "@tabler/icons-react";
import { DataTable } from "mantine-datatable";
import type { AppMetadataLocalization, AppMetadataSnapshot } from "@/types";
import { localeLabel } from "@/components/metadata/localeLabel";

interface MetadataGridProps {
  snapshot: AppMetadataSnapshot;
  onRowClick: (locale: string) => void;
  onOpenBulk: () => void;
}

interface GridRow {
  locale: string;
  name: string;
  subtitle: string;
  keywords: string;
  promotional_text: string;
}

function truncate(s: string | null, max = 60): string {
  if (!s) return "";
  return s.length > max ? `${s.slice(0, max)}…` : s;
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
        keywords: "",
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
    row.keywords = r.keywords ?? "";
    row.promotional_text = r.promotional_text ?? "";
  }
  return Array.from(map.values()).sort((a, b) => a.locale.localeCompare(b.locale));
}

export default function MetadataGrid({
  snapshot,
  onRowClick,
  onOpenBulk,
}: MetadataGridProps) {
  const records = useMemo(() => buildRows(snapshot), [snapshot]);

  return (
    <Stack gap="md" mt="md">
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          {records.length} locales · click a row to edit it in the Single locale tab.
        </Text>
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
            width: 200,
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
            render: (r) => <Text size="sm">{truncate(r.name)}</Text>,
          },
          {
            accessor: "subtitle",
            title: "Subtitle",
            render: (r) => <Text size="sm">{truncate(r.subtitle)}</Text>,
          },
          {
            accessor: "keywords",
            title: "Keywords",
            render: (r) => <Text size="sm">{truncate(r.keywords)}</Text>,
          },
          {
            accessor: "promotional_text",
            title: "Promo text",
            render: (r) => <Text size="sm">{truncate(r.promotional_text)}</Text>,
          },
        ]}
      />
    </Stack>
  );
}
