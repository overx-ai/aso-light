import type { ReactNode } from "react";
import {
  Alert,
  Badge,
  Group,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Skeleton,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import { LineChart } from "@mantine/charts";
import {
  IconAlertCircle,
  IconArrowDownRight,
  IconArrowUpRight,
  IconChartLine,
  IconMessageCircle,
  IconMoodSad,
  IconStarFilled,
} from "@tabler/icons-react";
import type { ReviewTrendOut } from "@/types";

const WINDOW_OPTIONS = [
  { value: "7", label: "7d" },
  { value: "14", label: "14d" },
  { value: "30", label: "30d" },
  { value: "90", label: "90d" },
];

function formatTrendDate(value: string | null): string {
  if (!value) return "—";
  return new Date(`${value}T12:00:00Z`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function formatAverageRating(value: number | null): string {
  if (value === null) return "—";
  return `${value.toFixed(1)} / 5`;
}

function largestSwing(summary: ReviewTrendOut["summary"]): {
  value: string;
  hint: string;
  color: string;
  icon: typeof IconArrowUpRight;
} {
  if (
    summary.biggest_spike_delta > 0 &&
    summary.biggest_spike_delta >= Math.abs(summary.biggest_drop_delta)
  ) {
    return {
      value: `+${summary.biggest_spike_delta}`,
      hint: `Spike on ${formatTrendDate(summary.biggest_spike_date)}`,
      color: "red",
      icon: IconArrowUpRight,
    };
  }

  if (summary.biggest_drop_delta < 0) {
    return {
      value: `${summary.biggest_drop_delta}`,
      hint: `Drop on ${formatTrendDate(summary.biggest_drop_date)}`,
      color: "teal",
      icon: IconArrowDownRight,
    };
  }

  return {
    value: "Flat",
    hint: "No sharp swing in this window",
    color: "gray",
    icon: IconArrowUpRight,
  };
}

function TrendStatCard({
  icon,
  color,
  label,
  value,
  hint,
}: {
  icon: ReactNode;
  color: string;
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <Paper withBorder p="md" radius="md">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            {label}
          </Text>
          <Text size="xl" fw={700} mt={6}>
            {value}
          </Text>
          <Text size="xs" c="dimmed" mt={4}>
            {hint}
          </Text>
        </div>
        <ThemeIcon color={color} variant="light" size={34} radius="xl">
          {icon}
        </ThemeIcon>
      </Group>
    </Paper>
  );
}

function DashboardSkeleton() {
  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="md">
        <Group justify="space-between">
          <div style={{ flex: 1 }}>
            <Skeleton height={18} width={180} mb={8} />
            <Skeleton height={12} width="60%" />
          </div>
          <Skeleton height={28} width={180} radius="xl" />
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} height={102} radius="md" />
          ))}
        </SimpleGrid>
        <Skeleton height={260} radius="md" />
      </Stack>
    </Paper>
  );
}

export default function ReviewTrendDashboard({
  trend,
  days,
  isLoading,
  errorMessage,
  onDaysChange,
}: {
  trend: ReviewTrendOut | null;
  days: string;
  isLoading: boolean;
  errorMessage: string | null;
  onDaysChange: (value: string) => void;
}) {
  if (isLoading) {
    return <DashboardSkeleton />;
  }

  if (errorMessage) {
    return (
      <Alert color="red" icon={<IconAlertCircle size={16} />}>
        Could not load review trend.
        {errorMessage ? ` ${errorMessage}` : ""}
      </Alert>
    );
  }

  if (!trend) {
    return null;
  }

  const swing = largestSwing(trend.summary);
  const SwingIcon = swing.icon;
  const chartData = trend.points.map((point) => ({
    label: formatTrendDate(point.date),
    "All reviews": point.total_reviews,
    "1-2 star reviews": point.low_rating_reviews,
  }));
  const noData = trend.summary.total_reviews === 0;

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <div>
            <Group gap="xs">
              <ThemeIcon color="red" variant="light" size={34} radius="xl">
                <IconChartLine size={18} />
              </ThemeIcon>
              <div>
                <Title order={4}>Review sentiment trend</Title>
                <Text size="sm" c="dimmed">
                  Daily volume for all reviews vs 1-2 star reviews so spikes
                  and drop-offs stand out before you open the queue.
                </Text>
              </div>
            </Group>
          </div>

          <SegmentedControl
            size="xs"
            value={days}
            onChange={onDaysChange}
            data={WINDOW_OPTIONS}
          />
        </Group>

        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
          <TrendStatCard
            label="All reviews"
            value={`${trend.summary.total_reviews}`}
            hint={`Latest day: ${trend.summary.latest_total_reviews}`}
            color="blue"
            icon={<IconMessageCircle size={18} />}
          />
          <TrendStatCard
            label="1-2 star reviews"
            value={`${trend.summary.low_rating_reviews}`}
            hint={`${trend.summary.low_rating_share_pct.toFixed(1)}% of window volume`}
            color="red"
            icon={<IconMoodSad size={18} />}
          />
          <TrendStatCard
            label="Average rating"
            value={formatAverageRating(trend.summary.average_rating)}
            hint={`${trend.summary.response_rate_pct.toFixed(1)}% replied`}
            color="yellow"
            icon={<IconStarFilled size={18} />}
          />
          <TrendStatCard
            label="Largest swing"
            value={swing.value}
            hint={swing.hint}
            color={swing.color}
            icon={<SwingIcon size={18} />}
          />
        </SimpleGrid>

        {trend.partial && (
          <Alert color="yellow" icon={<IconAlertCircle size={16} />}>
            Trend data is based on the newest slice of App Store reviews only.
            High-volume apps may have older days trimmed from Apple&apos;s live
            feed.
          </Alert>
        )}

        {noData ? (
          <Alert color="gray" icon={<IconAlertCircle size={16} />}>
            No reviews landed in the selected window.
          </Alert>
        ) : (
          <>
            <Group gap="xs">
              <Badge color="gray" variant="light">
                All reviews
              </Badge>
              <Badge color="red" variant="light">
                1-2 star reviews
              </Badge>
            </Group>

            <LineChart
              h={280}
              data={chartData}
              dataKey="label"
              series={[
                { name: "All reviews", color: "gray.5" },
                { name: "1-2 star reviews", color: "red.6" },
              ]}
              curveType="linear"
              withDots
              dotProps={{ r: 3 }}
              strokeWidth={2}
              tooltipAnimationDuration={200}
            />

            <Group gap="md" wrap="wrap">
              <Text size="xs" c="dimmed">
                Worst spike:{" "}
                {trend.summary.biggest_spike_delta > 0
                  ? `+${trend.summary.biggest_spike_delta} on ${formatTrendDate(trend.summary.biggest_spike_date)}`
                  : "none"}
              </Text>
              <Text size="xs" c="dimmed">
                Sharpest drop:{" "}
                {trend.summary.biggest_drop_delta < 0
                  ? `${trend.summary.biggest_drop_delta} on ${formatTrendDate(trend.summary.biggest_drop_date)}`
                  : "none"}
              </Text>
              <Text size="xs" c="dimmed">
                Queue filters below only change the table. This chart keeps the
                overall sentiment view intact.
              </Text>
            </Group>
          </>
        )}
      </Stack>
    </Paper>
  );
}
