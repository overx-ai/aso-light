import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  Container,
  Group,
  Loader,
  Paper,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import {
  IconArrowRight,
  IconBulb,
  IconCheck,
  IconFileDescription,
  IconKeyboard,
  IconMessage,
  IconRefresh,
  IconSparkles,
  IconTargetArrow,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys, useGrowthRecommendations } from "@/lib/hooks";
import type {
  GrowthCategory,
  GrowthPriority,
  GrowthRecommendationOut,
} from "@/types";

const CATEGORY_META: Record<
  GrowthCategory,
  { label: string; color: string; icon: typeof IconSparkles }
> = {
  setup: { label: "Setup", color: "gray", icon: IconSparkles },
  metadata: { label: "Metadata", color: "indigo", icon: IconFileDescription },
  keywords: { label: "Keywords", color: "teal", icon: IconKeyboard },
  paid_search: { label: "Paid search", color: "blue", icon: IconTargetArrow },
  reviews: { label: "Reviews", color: "red", icon: IconMessage },
  pricing: { label: "Pricing", color: "orange", icon: IconBulb },
};

const PRIORITY_COLOR: Record<GrowthPriority, string> = {
  high: "red",
  medium: "yellow",
  low: "gray",
};

const PRIORITY_SCORE: Record<GrowthPriority, number> = {
  high: 100,
  medium: 62,
  low: 34,
};

function labelize(key: string) {
  return key.replace(/_/g, " ");
}

function formatEvidenceValue(value: unknown) {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value == null) return "none";
  return String(value);
}

function RecommendationCard({
  rec,
  onOpen,
}: {
  rec: GrowthRecommendationOut;
  onOpen: (path: string) => void;
}) {
  const meta = CATEGORY_META[rec.category];
  const Icon = meta.icon;
  const evidence = Object.entries(rec.evidence).slice(0, 4);

  return (
    <Card withBorder radius="sm" p="md">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Group gap="xs" wrap="nowrap">
            <Paper
              withBorder
              radius="sm"
              p={6}
              style={{ color: `var(--mantine-color-${meta.color}-6)` }}
            >
              <Icon size={18} />
            </Paper>
            <div>
              <Group gap={6}>
                <Badge color={meta.color} variant="light" size="sm">
                  {meta.label}
                </Badge>
                <Badge
                  color={PRIORITY_COLOR[rec.priority]}
                  variant="filled"
                  size="sm"
                >
                  {rec.priority}
                </Badge>
              </Group>
              <Title order={4} mt={6} style={{ lineHeight: 1.2 }}>
                {rec.title}
              </Title>
            </div>
          </Group>
          <Tooltip label={`Confidence: ${rec.confidence} · Effort: ${rec.effort}`}>
            <Progress
              w={78}
              size={8}
              mt={6}
              value={PRIORITY_SCORE[rec.confidence]}
              color={PRIORITY_COLOR[rec.confidence]}
            />
          </Tooltip>
        </Group>

        <Text size="sm" c="dimmed">
          {rec.detail}
        </Text>

        {evidence.length > 0 && (
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
            {evidence.map(([key, value]) => (
              <Paper key={key} withBorder radius="sm" p="xs">
                <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
                  {labelize(key)}
                </Text>
                <Text size="sm" fw={600} lineClamp={2}>
                  {formatEvidenceValue(value)}
                </Text>
              </Paper>
            ))}
          </SimpleGrid>
        )}

        <Group justify="space-between" mt="xs">
          <Group gap={6}>
            <Badge variant="outline" color="gray">
              {rec.effort} effort
            </Badge>
            <Badge variant="outline" color={PRIORITY_COLOR[rec.confidence]}>
              {rec.confidence} confidence
            </Badge>
          </Group>
          <Button
            size="xs"
            variant="light"
            rightSection={<IconArrowRight size={14} />}
            onClick={() => onOpen(rec.cta_path)}
          >
            {rec.cta_label}
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

export default function GrowthPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const appId = Number(id);
  const { data, isLoading, isFetching, isError } = useGrowthRecommendations(appId);

  const counts = useMemo(() => {
    const items = data?.items ?? [];
    return {
      total: items.length,
      high: items.filter((item) => item.priority === "high").length,
      quick: items.filter((item) => item.effort === "low").length,
    };
  }, [data]);

  const refresh = () => {
    queryClient.invalidateQueries({
      queryKey: queryKeys.growthRecommendations(appId),
    });
  };

  return (
    <Container size="xl">
      <Group justify="space-between" mb="lg" align="flex-start">
        <div>
          <Group gap="xs">
            <IconSparkles size={24} color="var(--mantine-color-indigo-6)" />
            <Title order={2}>Growth</Title>
          </Group>
          <Text c="dimmed" size="sm" mt={4}>
            Prioritized actions from metadata, keywords, Search Ads, reviews,
            and pricing signals.
          </Text>
        </div>
        <Button
          variant="light"
          leftSection={isFetching ? <Loader size={14} /> : <IconRefresh size={16} />}
          onClick={refresh}
          disabled={isFetching}
        >
          Refresh
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 3 }} mb="lg">
        <Paper withBorder radius="sm" p="md">
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            Open actions
          </Text>
          <Title order={3}>{counts.total}</Title>
        </Paper>
        <Paper withBorder radius="sm" p="md">
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            High priority
          </Text>
          <Title order={3} c="red">
            {counts.high}
          </Title>
        </Paper>
        <Paper withBorder radius="sm" p="md">
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            Low effort
          </Text>
          <Title order={3} c="teal">
            {counts.quick}
          </Title>
        </Paper>
      </SimpleGrid>

      {isLoading ? (
        <Stack gap="md">
          <Card withBorder radius="sm" h={180} />
          <Card withBorder radius="sm" h={180} />
        </Stack>
      ) : isError ? (
        <Alert color="red" title="Recommendations unavailable">
          Could not load growth recommendations for this app.
        </Alert>
      ) : !data || data.items.length === 0 ? (
        <Alert color="green" icon={<IconCheck size={18} />} title="No open actions">
          ASO-Light did not find a high-signal action from the current local data.
        </Alert>
      ) : (
        <Stack gap="md">
          {data.items.map((rec) => (
            <RecommendationCard
              key={rec.id}
              rec={rec}
              onOpen={(path) => navigate(path)}
            />
          ))}
        </Stack>
      )}
    </Container>
  );
}
