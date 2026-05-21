import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Container,
  Group,
  Image,
  Loader,
  Paper,
  Select,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconStarFilled,
  IconSwords,
} from "@tabler/icons-react";
import KeywordIntelBadge from "@/components/keywords/KeywordIntelBadge";
import { summarizeKeywordIntelForTrack } from "@/components/keywords/keywordIntel";
import { useApp, useAppClash, useVisibilitySov, useVisibilityWatches } from "@/lib/hooks";
import type { ClashRow } from "@/types";

const COUNTRY_OPTIONS = [
  { value: "us", label: "United States" },
  { value: "gb", label: "United Kingdom" },
  { value: "de", label: "Germany" },
  { value: "fr", label: "France" },
  { value: "es", label: "Spain" },
  { value: "it", label: "Italy" },
  { value: "jp", label: "Japan" },
  { value: "kr", label: "South Korea" },
  { value: "cn", label: "China" },
  { value: "ru", label: "Russia" },
  { value: "br", label: "Brazil" },
  { value: "mx", label: "Mexico" },
  { value: "in", label: "India" },
  { value: "au", label: "Australia" },
  { value: "ca", label: "Canada" },
];

function formatRating(rating: number | null, count: number | null): string {
  if (rating == null) return "—";
  if (!count) return rating.toFixed(2);
  if (count >= 1_000_000) return `${rating.toFixed(2)} (${(count / 1_000_000).toFixed(1)}M)`;
  if (count >= 1_000) return `${rating.toFixed(2)} (${(count / 1_000).toFixed(1)}K)`;
  return `${rating.toFixed(2)} (${count})`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

function ClashCell({
  label,
  children,
  highlight,
}: {
  label: string;
  children: React.ReactNode;
  highlight?: boolean;
}) {
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed" tt="uppercase">
        {label}
      </Text>
      <Text size="sm" fw={highlight ? 600 : 400}>
        {children}
      </Text>
    </Stack>
  );
}

function ClashCard({
  row,
  intel,
}: {
  row: ClashRow;
  intel: React.ReactNode;
}) {
  return (
    <Paper
      withBorder
      p="md"
      radius="md"
      style={{
        borderColor: row.is_self
          ? "var(--mantine-color-blue-5)"
          : undefined,
        borderWidth: row.is_self ? 2 : undefined,
      }}
    >
      <Stack gap="xs">
        <Group gap="sm" wrap="nowrap">
          <Image
            src={row.icon_url ?? undefined}
            w={56}
            h={56}
            radius="md"
            fallbackSrc="https://placehold.co/56?text=?"
          />
          <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
            <Group gap="xs">
              <Text size="md" fw={600} truncate>
                {row.name ?? "—"}
              </Text>
              {row.is_self && (
                <Badge size="xs" color="blue" variant="filled">
                  You
                </Badge>
              )}
            </Group>
            <Text size="xs" c="dimmed" truncate>
              {row.seller ?? "—"}
            </Text>
            <Text size="xs" c="dimmed" truncate>
              {row.bundle_id ?? "—"}
            </Text>
          </Stack>
        </Group>

        <Group gap="xl" wrap="wrap">
          <ClashCell label="Rating">
            <Group gap={4} wrap="nowrap">
              <IconStarFilled
                size={12}
                style={{ color: "var(--mantine-color-yellow-6)" }}
              />
              {formatRating(row.average_rating, row.rating_count)}
            </Group>
          </ClashCell>
          <ClashCell label="Genre">{row.primary_genre ?? "—"}</ClashCell>
          <ClashCell label="Price">{row.formatted_price ?? "—"}</ClashCell>
          <ClashCell label="Version">{row.version ?? "—"}</ClashCell>
          <ClashCell label="Released">{formatDate(row.release_date)}</ClashCell>
          <ClashCell label="Size">
            {row.file_size_mb != null ? `${row.file_size_mb} MB` : "—"}
          </ClashCell>
          <ClashCell label="Keyword intel">{intel}</ClashCell>
        </Group>

        {row.description_excerpt && (
          <Text size="xs" c="dimmed" lineClamp={3}>
            {row.description_excerpt}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

export default function AppClashPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const { data: app } = useApp(id ?? "");
  const [country, setCountry] = useState("us");
  const clashQuery = useAppClash(appId, country);
  const visibilityWatches = useVisibilityWatches(appId);
  const visibilitySov = useVisibilitySov(appId, 30);

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  const rows = clashQuery.data?.rows ?? [];
  const intelLoading =
    visibilityWatches.isLoading || visibilitySov.isLoading;
  const intelUnavailable =
    visibilityWatches.isError || visibilitySov.isError;
  const intelByTrackId = useMemo(() => {
    const summaries = new Map<string, ReturnType<typeof summarizeKeywordIntelForTrack>>();

    for (const row of rows) {
      summaries.set(
        row.track_id,
        summarizeKeywordIntelForTrack({
          country,
          trackId: row.track_id,
          watches: visibilityWatches.data?.items ?? [],
          sovItems: visibilitySov.data?.items ?? [],
        }),
      );
    }

    return summaries;
  }, [country, rows, visibilitySov.data?.items, visibilityWatches.data?.items]);

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-md)" }}>
        <Group gap="sm" align="center">
          <IconSwords size={22} />
          <Title order={2}>{app?.name ?? "App"} — App Clash</Title>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Side-by-side iTunes lookup of your app and every competitor you
          track. Switch country to see how each storefront prices and rates
          them.
        </Text>
      </div>

      <Stack gap="sm">
        <Paper withBorder p="xs">
          <Group gap="md" wrap="wrap" align="flex-end">
            <Select
              label="Storefront"
              data={COUNTRY_OPTIONS}
              value={country}
              onChange={(v) => setCountry(v ?? "us")}
              size="xs"
              style={{ minWidth: 200 }}
              allowDeselect={false}
            />
            {clashQuery.isLoading && <Loader size="xs" mt="xl" />}
            <Text size="xs" c="dimmed" mt="xl">
              {rows.length} app{rows.length === 1 ? "" : "s"}
            </Text>
          </Group>
        </Paper>

        {clashQuery.error ? (
          <Alert color="red" icon={<IconAlertCircle size={16} />}>
            Could not load clash data.
          </Alert>
        ) : rows.length === 0 ? (
          <Alert color="yellow" icon={<IconAlertCircle size={16} />}>
            No competitors yet — add some on the Keywords page → Competitors
            tab, then come back here.
          </Alert>
        ) : (
          <Stack gap="sm">
            {rows.map((r) => (
              <ClashCard
                key={r.track_id}
                row={r}
                intel={
                  <KeywordIntelBadge
                    loading={intelLoading}
                    unavailable={intelUnavailable}
                    summary={intelByTrackId.get(r.track_id) ?? null}
                  />
                }
              />
            ))}
          </Stack>
        )}
      </Stack>
    </Container>
  );
}
