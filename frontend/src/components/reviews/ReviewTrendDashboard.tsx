import { useMemo } from "react";
import {
  Alert,
  Badge,
  Group,
  Loader,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
} from "@mantine/core";
import { LineChart } from "@mantine/charts";
import {
  IconAlertCircle,
  IconArrowDownRight,
  IconArrowUpRight,
  IconChartLine,
} from "@tabler/icons-react";
import { useReviewTrends } from "@/lib/hooks";
import type { ReviewThemeTrend, ReviewTrendInsight } from "@/types";

const WINDOW_OPTIONS = [
  { value: "7", label: "7 days" },
  { value: "14", label: "14 days" },
  { value: "30", label: "30 days" },
  { value: "60", label: "60 days" },
  { value: "90", label: "90 days" },
];

const THEME_COLORS = ["blue", "orange", "grape", "teal", "red"];

interface ReviewTrendDashboardProps {
  appId: number;
  territory?: string;
  days: number;
  onDaysChange: (days: number) => void;
}

interface ChartRow {
  date: string;
  [key: string]: string | number | null;
}

function formatDay(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return new Date(year, month - 1, day).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

function formatAverage(value: number | null): string {
  return value == null ? "-" : value.toFixed(2);
}

function insightText(insight: ReviewTrendInsight): string {
  const delta = insight.change > 0 ? `+${insight.change}` : `${insight.change}`;
  return `${insight.metric}: ${delta} on ${formatDay(insight.date)}`;
}

function buildThemeChartRows(
  themes: ReviewThemeTrend[],
  dates: string[],
): ChartRow[] {
  const themeMaps = themes.map((theme) => ({
    theme: theme.theme,
    points: new Map(theme.points.map((point) => [point.date, point.count])),
  }));

  return dates.map((date) => {
    const row: ChartRow = { date: formatDay(date) };
    for (const theme of themeMaps) {
      row[theme.theme] = theme.points.get(date) ?? 0;
    }
    return row;
  });
}

export default function ReviewTrendDashboard({
  appId,
  territory,
  days,
  onDaysChange,
}: ReviewTrendDashboardProps) {
  const trendsQuery = useReviewTrends(appId, { territory, days });
  const trends = trendsQuery.data;
  const topThemes = useMemo(
    () => trends?.themes.slice(0, 4) ?? [],
    [trends?.themes],
  );

  const volumeChartData = useMemo<ChartRow[]>(
    () =>
      (trends?.points ?? []).map((point) => ({
        date: formatDay(point.date),
        "All reviews": point.total,
        "Low ratings": point.low_rating,
      })),
    [trends?.points],
  );

  const themeChartData = useMemo<ChartRow[]>(
    () =>
      buildThemeChartRows(
        topThemes,
        (trends?.points ?? []).map((point) => point.date),
      ),
    [topThemes, trends?.points],
  );

  return (
    <Paper withBorder p="md" radius="sm">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" gap="sm">
          <Group gap="sm" align="center">
            <IconChartLine size={20} />
            <div>
              <Text fw={600} size="sm">
                Sentiment trends
              </Text>
              <Text c="dimmed" size="xs">
                {territory ? `${territory} - ` : ""}Low ratings are 1-2 stars.
              </Text>
            </div>
          </Group>
          <Select
            label="Window"
            data={WINDOW_OPTIONS}
            value={String(days)}
            onChange={(value) => onDaysChange(Number(value ?? "30"))}
            size="xs"
            allowDeselect={false}
            w={120}
          />
        </Group>

        {trendsQuery.error ? (
          <Alert color="red" icon={<IconAlertCircle size={16} />}>
            Could not load review trends.
            {(trendsQuery.error as Error).message
              ? ` ${(trendsQuery.error as Error).message}`
              : ""}
          </Alert>
        ) : trendsQuery.isLoading && !trends ? (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm" c="dimmed">
              Loading review trends...
            </Text>
          </Group>
        ) : trends ? (
          <>
            <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
              <div>
                <Text size="xs" c="dimmed">
                  Reviews
                </Text>
                <Text fw={700} size="xl">
                  {trends.total_reviews}
                </Text>
              </div>
              <div>
                <Text size="xs" c="dimmed">
                  Low ratings
                </Text>
                <Text fw={700} size="xl" c="red.7">
                  {trends.low_rating_total}
                </Text>
              </div>
              <div>
                <Text size="xs" c="dimmed">
                  Average rating
                </Text>
                <Text fw={700} size="xl">
                  {formatAverage(trends.average_rating)}
                </Text>
              </div>
              <div>
                <Text size="xs" c="dimmed">
                  Top theme
                </Text>
                <Text fw={700} size="xl" truncate>
                  {topThemes[0]?.theme ?? "-"}
                </Text>
              </div>
            </SimpleGrid>

            <Group gap="xs" wrap="wrap">
              {trends.insights.length > 0 ? (
                trends.insights.map((insight) => (
                  <Badge
                    key={`${insight.metric}-${insight.kind}-${insight.date}`}
                    color={insight.kind === "spike" ? "red" : "green"}
                    variant="light"
                    leftSection={
                      insight.kind === "spike" ? (
                        <IconArrowUpRight size={12} />
                      ) : (
                        <IconArrowDownRight size={12} />
                      )
                    }
                    style={{ textTransform: "none" }}
                  >
                    {insightText(insight)}
                  </Badge>
                ))
              ) : (
                <Text size="xs" c="dimmed">
                  No daily spikes or drops in this window.
                </Text>
              )}
            </Group>

            {trends.truncated && (
              <Alert color="yellow" icon={<IconAlertCircle size={16} />}>
                Trend data reached the ASC page cap; this high-volume window may
                be incomplete.
              </Alert>
            )}

            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
              <Stack gap="xs">
                <Group gap="xs">
                  <Text fw={600} size="sm">
                    Daily review volume
                  </Text>
                  <Badge size="xs" color="gray" variant="light">
                    All
                  </Badge>
                  <Badge size="xs" color="red" variant="light">
                    Low
                  </Badge>
                </Group>
                <LineChart
                  h={230}
                  data={volumeChartData}
                  dataKey="date"
                  series={[
                    { name: "All reviews", color: "gray" },
                    { name: "Low ratings", color: "red" },
                  ]}
                  curveType="monotone"
                  withDots
                  dotProps={{ r: 3 }}
                  tooltipAnimationDuration={150}
                />
              </Stack>

              <Stack gap="xs">
                <Group gap="xs" wrap="wrap">
                  <Text fw={600} size="sm">
                    Theme volume
                  </Text>
                  {topThemes.map((theme, index) => (
                    <Badge
                      key={theme.theme}
                      size="xs"
                      color={THEME_COLORS[index]}
                      variant="light"
                      style={{ textTransform: "none" }}
                    >
                      {theme.theme}
                    </Badge>
                  ))}
                </Group>
                {topThemes.length > 0 ? (
                  <LineChart
                    h={230}
                    data={themeChartData}
                    dataKey="date"
                    series={topThemes.map((theme, index) => ({
                      name: theme.theme,
                      color: THEME_COLORS[index],
                    }))}
                    curveType="monotone"
                    connectNulls
                    withDots
                    dotProps={{ r: 3 }}
                    tooltipAnimationDuration={150}
                  />
                ) : (
                  <Text size="sm" c="dimmed">
                    No classified themes in this window.
                  </Text>
                )}
              </Stack>
            </SimpleGrid>
          </>
        ) : null}
      </Stack>
    </Paper>
  );
}
