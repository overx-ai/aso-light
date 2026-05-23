import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Container,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconArrowRight,
  IconBulb,
  IconCoin,
  IconInfoCircle,
} from "@tabler/icons-react";
import { useApp, useGrowthRecommendations } from "@/lib/hooks";
import type {
  GrowthRecommendation,
  GrowthRecommendationCategory,
  GrowthRecommendationSeverity,
} from "@/types";

type CategoryFilter = "all" | GrowthRecommendationCategory;

const CATEGORY_LABEL: Record<GrowthRecommendationCategory, string> = {
  pricing: "Pricing",
  metadata: "Metadata",
  keywords: "Keywords",
  visibility: "Visibility",
  reviews: "Reviews",
  paid_search: "Paid Search",
  availability: "Availability",
};

const SEVERITY_COLOR: Record<GrowthRecommendationSeverity, string> = {
  critical: "red",
  warning: "yellow",
  info: "blue",
};

function RecommendationCard({
  item,
  onOpen,
}: {
  item: GrowthRecommendation;
  onOpen: (path: string) => void;
}) {
  return (
    <Paper withBorder radius="sm" p="md">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" gap="sm">
          <Group gap="xs">
            <ThemeIcon variant="light" color="blue" size="sm">
              {item.category === "pricing" ? (
                <IconCoin size={14} />
              ) : (
                <IconBulb size={14} />
              )}
            </ThemeIcon>
            <Badge variant="light" color="gray" size="sm">
              {CATEGORY_LABEL[item.category]}
            </Badge>
          </Group>
          <Badge
            variant="light"
            color={SEVERITY_COLOR[item.severity]}
            size="sm"
          >
            {item.severity}
          </Badge>
        </Group>

        <Stack gap={4}>
          <Title order={4}>{item.title}</Title>
          <Text size="sm" c="dimmed">
            {item.description}
          </Text>
          <Text size="sm">{item.impact}</Text>
        </Stack>

        {item.evidence.length > 0 && (
          <Stack gap={4}>
            {item.evidence.map((entry) => (
              <Group
                key={`${item.id}-${entry.label}`}
                gap="xs"
                align="flex-start"
              >
                <Text size="xs" c="dimmed" w={130}>
                  {entry.label}
                </Text>
                <Text size="xs" style={{ flex: 1, minWidth: 0 }}>
                  {entry.value}
                </Text>
              </Group>
            ))}
          </Stack>
        )}

        <Group justify="flex-end">
          <Button
            size="xs"
            rightSection={<IconArrowRight size={14} />}
            onClick={() => onOpen(item.cta_path)}
          >
            {item.cta_label}
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

export default function GrowthPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const navigate = useNavigate();
  const { data: app } = useApp(id ?? "");
  const recommendationsQuery = useGrowthRecommendations(appId);
  const [category, setCategory] = useState<CategoryFilter>("all");

  const filtered = useMemo(() => {
    const items = recommendationsQuery.data?.items ?? [];
    if (category === "all") return items;
    return items.filter((item) => item.category === category);
  }, [recommendationsQuery.data, category]);

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  const summary = recommendationsQuery.data?.summary;

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-lg)" }}>
        <Group gap="sm" align="center">
          <IconBulb size={22} />
          <Title order={2}>{app?.name ?? "App"} - Growth Advisor</Title>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Prioritized fixes from synced storefront, keyword, visibility, and
          pricing data.
        </Text>
      </div>

      {recommendationsQuery.isLoading ? (
        <Group justify="center" py="xl">
          <Loader />
        </Group>
      ) : recommendationsQuery.error ? (
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Could not load growth recommendations.
        </Alert>
      ) : (
        <Stack gap="md">
          <Group gap="sm" wrap="wrap">
            <Paper withBorder radius="sm" p="xs" px="md">
              <Group gap="xs">
                <IconInfoCircle
                  size={16}
                  color="var(--mantine-color-blue-6)"
                />
                <Text size="sm">{summary?.total ?? 0} recommendations</Text>
              </Group>
            </Paper>
            <Paper withBorder radius="sm" p="xs" px="md">
              <Group gap="xs">
                <IconCoin size={16} color="var(--mantine-color-green-6)" />
                <Text size="sm">{summary?.pricing ?? 0} pricing</Text>
              </Group>
            </Paper>
          </Group>

          <SegmentedControl
            size="xs"
            value={category}
            onChange={(value) => setCategory(value as CategoryFilter)}
            data={[
              { value: "all", label: "All" },
              { value: "pricing", label: "Pricing" },
            ]}
          />

          {filtered.length === 0 ? (
            <Paper withBorder radius="sm" p="lg">
              <Text size="sm" c="dimmed">
                Nothing to show for this filter.
              </Text>
            </Paper>
          ) : (
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
              {filtered.map((item) => (
                <RecommendationCard
                  key={item.id}
                  item={item}
                  onOpen={navigate}
                />
              ))}
            </SimpleGrid>
          )}
        </Stack>
      )}
    </Container>
  );
}
