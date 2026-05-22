import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Container,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  Title,
} from "@mantine/core";
import { DataTable } from "mantine-datatable";
import {
  IconAlertCircle,
  IconCheck,
  IconMessage,
  IconStarFilled,
} from "@tabler/icons-react";
import { useApp, useReviews, useReviewTrend } from "@/lib/hooks";
import type { ReviewOut } from "@/types";
import ReviewDrawer from "@/components/reviews/ReviewDrawer";
import ReviewTrendDashboard from "@/components/reviews/ReviewTrendDashboard";

const RATING_OPTIONS = [
  { value: "any", label: "Any" },
  { value: "1", label: "1 star" },
  { value: "2", label: "2 stars" },
  { value: "3", label: "3 stars" },
  { value: "4", label: "4 stars" },
  { value: "5", label: "5 stars" },
];

// Top territories — Apple returns alpha-3 codes.
const TERRITORY_OPTIONS = [
  { value: "any", label: "Any country" },
  { value: "USA", label: "USA" },
  { value: "GBR", label: "GBR" },
  { value: "DEU", label: "DEU" },
  { value: "FRA", label: "FRA" },
  { value: "ESP", label: "ESP" },
  { value: "ITA", label: "ITA" },
  { value: "JPN", label: "JPN" },
  { value: "KOR", label: "KOR" },
  { value: "CHN", label: "CHN" },
  { value: "RUS", label: "RUS" },
  { value: "BRA", label: "BRA" },
  { value: "MEX", label: "MEX" },
  { value: "IND", label: "IND" },
  { value: "AUS", label: "AUS" },
  { value: "CAN", label: "CAN" },
];

export default function ReviewsPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const { data: app } = useApp(id ?? "");

  const [territory, setTerritory] = useState<string>("any");
  const [rating, setRating] = useState<string>("any");
  const [needsReply, setNeedsReply] = useState(false);
  const [trendDays, setTrendDays] = useState("30");
  const [selected, setSelected] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const filters = useMemo(
    () => ({
      territory: territory === "any" ? undefined : territory,
      rating: rating === "any" ? undefined : Number(rating),
      has_response: needsReply ? false : undefined,
    }),
    [territory, rating, needsReply],
  );

  const reviewsQuery = useReviews(appId, filters);
  const trendQuery = useReviewTrend(appId, {
    territory: territory === "any" ? undefined : territory,
    days: Number(trendDays),
    low_rating_max: 2,
  });

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  const records = reviewsQuery.data?.items ?? [];

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-md)" }}>
        <Group gap="sm" align="center">
          <IconMessage size={22} />
          <Title order={2}>{app?.name ?? "App"} — Reviews</Title>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Spot rating regressions, then read, AI-draft, translate, and post
          replies straight to App Store Connect.
        </Text>
      </div>

      <Stack gap="sm">
        <Paper withBorder p="xs">
          <Group gap="md" wrap="wrap" align="flex-end">
            <Select
              label="Country"
              data={TERRITORY_OPTIONS}
              value={territory}
              onChange={(v) => setTerritory(v ?? "any")}
              size="xs"
              style={{ minWidth: 160 }}
            />
            <Text c="dimmed" size="xs" mt="xl">
              Trend follows the country filter.
            </Text>
          </Group>
        </Paper>

        <ReviewTrendDashboard
          trend={trendQuery.data ?? null}
          days={trendDays}
          isLoading={trendQuery.isLoading}
          errorMessage={
            trendQuery.error instanceof Error ? trendQuery.error.message : null
          }
          onDaysChange={setTrendDays}
        />

        <Paper withBorder p="xs">
          <Group gap="md" wrap="wrap" align="flex-end">
            <Select
              label="Rating"
              data={RATING_OPTIONS}
              value={rating}
              onChange={(v) => setRating(v ?? "any")}
              size="xs"
              style={{ minWidth: 140 }}
            />
            <Switch
              label="Needs reply"
              checked={needsReply}
              onChange={(e) => setNeedsReply(e.currentTarget.checked)}
              size="sm"
              mt="xl"
            />
            {reviewsQuery.isLoading && <Loader size="xs" mt="xl" />}
            <Text c="dimmed" size="xs" mt="xl">
              {records.length} review{records.length === 1 ? "" : "s"}
            </Text>
          </Group>
        </Paper>

        {reviewsQuery.error ? (
          <Alert color="red" icon={<IconAlertCircle size={16} />}>
            Could not load reviews.
            {(reviewsQuery.error as Error).message
              ? ` ${(reviewsQuery.error as Error).message}`
              : ""}
          </Alert>
        ) : (
          <DataTable<ReviewOut>
            withTableBorder
            highlightOnHover
            striped
            records={records}
            idAccessor="id"
            minHeight={records.length === 0 ? 200 : undefined}
            noRecordsText={
              reviewsQuery.isLoading
                ? "Loading…"
                : "No reviews match the current filters"
            }
            onRowClick={({ record }) => {
              setSelected(record.id);
              setDrawerOpen(true);
            }}
            columns={[
              {
                accessor: "rating",
                title: "★",
                width: 80,
                render: (r) => (
                  <Group gap={2} wrap="nowrap">
                    <IconStarFilled
                      size={14}
                      style={{ color: "var(--mantine-color-yellow-6)" }}
                    />
                    <Text size="sm" fw={600}>
                      {r.rating}
                    </Text>
                  </Group>
                ),
              },
              {
                accessor: "territory",
                title: "Country",
                width: 80,
                render: (r) => (
                  <Badge size="xs" variant="light" color="gray">
                    {r.territory ?? "—"}
                  </Badge>
                ),
              },
              {
                accessor: "reviewer_nickname",
                title: "Reviewer",
                width: 140,
                render: (r) => (
                  <Text size="xs" c="dimmed" truncate>
                    {r.reviewer_nickname ?? "—"}
                  </Text>
                ),
              },
              {
                accessor: "title",
                title: "Title",
                width: 200,
                render: (r) => (
                  <Text size="sm" fw={500} lineClamp={1}>
                    {r.title ?? "—"}
                  </Text>
                ),
              },
              {
                accessor: "body",
                title: "Body",
                render: (r) => (
                  <Text size="xs" c="dimmed" lineClamp={2}>
                    {r.body ?? "—"}
                  </Text>
                ),
              },
              {
                accessor: "created_date",
                title: "Date",
                width: 100,
                render: (r) => (
                  <Text size="xs" c="dimmed">
                    {r.created_date
                      ? new Date(r.created_date).toLocaleDateString()
                      : "—"}
                  </Text>
                ),
              },
              {
                accessor: "response",
                title: "Reply",
                width: 80,
                textAlign: "center" as const,
                render: (r) =>
                  r.response ? (
                    <Badge
                      size="xs"
                      color="green"
                      variant="filled"
                      leftSection={<IconCheck size={10} />}
                    >
                      Replied
                    </Badge>
                  ) : (
                    <Text size="xs" c="dimmed">
                      —
                    </Text>
                  ),
              },
            ]}
          />
        )}
      </Stack>

      <ReviewDrawer
        appId={appId}
        reviewId={selected}
        opened={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      />
    </Container>
  );
}
