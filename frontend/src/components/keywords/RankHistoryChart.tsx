import { useMemo } from "react";
import { Paper, Text, Stack } from "@mantine/core";
import { LineChart } from "@mantine/charts";
import type { KeywordRankingHistory } from "@/types";

interface RankHistoryChartProps {
  histories: KeywordRankingHistory[];
  isLoading: boolean;
}

interface ChartDataPoint {
  date: string;
  [key: string]: string | number | null;
}

const TERRITORY_COLORS: Record<string, string> = {
  US: "blue",
  GB: "teal",
  DE: "orange",
  FR: "grape",
  JP: "red",
  CN: "yellow",
  KR: "cyan",
  BR: "green",
  AU: "indigo",
  CA: "pink",
};

function getColor(territoryCode: string): string {
  return TERRITORY_COLORS[territoryCode] ?? "gray";
}

export default function RankHistoryChart({
  histories,
  isLoading,
}: RankHistoryChartProps) {
  const { chartData, series } = useMemo(() => {
    if (!histories || histories.length === 0) {
      return { chartData: [], series: [] };
    }

    // Collect all dates across all territories
    const dateMap = new Map<string, ChartDataPoint>();

    for (const history of histories) {
      for (const point of history.data_points) {
        const dateKey = new Date(point.date).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
        });
        if (!dateMap.has(dateKey)) {
          dateMap.set(dateKey, { date: dateKey });
        }
        const entry = dateMap.get(dateKey)!;
        entry[history.territory_code] = point.rank;
      }
    }

    const data = Array.from(dateMap.values());
    const seriesList = histories.map((h) => ({
      name: h.territory_code,
      color: getColor(h.territory_code),
    }));

    return { chartData: data, series: seriesList };
  }, [histories]);

  if (isLoading) {
    return (
      <Paper withBorder p="md" radius="md">
        <Text size="sm" c="dimmed">
          Loading ranking history...
        </Text>
      </Paper>
    );
  }

  if (chartData.length === 0) {
    return (
      <Paper withBorder p="md" radius="md">
        <Text size="sm" c="dimmed" ta="center">
          No ranking history available yet. Refresh rankings to start
          collecting data.
        </Text>
      </Paper>
    );
  }

  return (
    <Paper withBorder p="md" radius="md">
      <Stack gap="xs">
        <Text size="sm" fw={500}>
          Rank History
        </Text>
        <LineChart
          h={250}
          data={chartData}
          dataKey="date"
          series={series}
          curveType="monotone"
          connectNulls
          yAxisProps={{
            reversed: true,
            domain: [1, "auto"],
            label: { value: "Rank", position: "insideLeft" },
          }}
          tooltipAnimationDuration={200}
          withDots
          dotProps={{ r: 3 }}
        />
      </Stack>
    </Paper>
  );
}
