import { useNavigate } from "react-router-dom";
import {
  Container,
  Title,
  Text,
  Button,
  Group,
  SimpleGrid,
  Card,
  Image,
  Badge,
  Stack,
  Skeleton,
} from "@mantine/core";
import {
  IconRefresh,
  IconApps,
  IconDeviceMobile,
  IconPlus,
} from "@tabler/icons-react";
import { useApps, useSyncApps } from "@/lib/hooks";

const PLATFORM_COLORS: Record<string, string> = {
  IOS: "blue",
  MAC_OS: "grape",
  APPLE_TV: "violet",
  VISION_OS: "cyan",
};

function AppCard({
  app,
  onClick,
}: {
  app: { id: number; name: string; bundle_id: string; platform: string; icon_url: string | null };
  onClick: () => void;
}) {
  return (
    <Card
      shadow="sm"
      padding="lg"
      radius="md"
      withBorder
      style={{ cursor: "pointer" }}
      onClick={onClick}
    >
      <Card.Section p="md" pb={0}>
        <Group justify="center">
          {app.icon_url ? (
            <Image
              src={app.icon_url}
              alt={app.name}
              w={64}
              h={64}
              radius="md"
            />
          ) : (
            <div
              style={{
                width: 64,
                height: 64,
                borderRadius: "var(--mantine-radius-md)",
                backgroundColor: "var(--mantine-color-gray-2)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <IconDeviceMobile
                size={32}
                color="var(--mantine-color-dimmed)"
              />
            </div>
          )}
        </Group>
      </Card.Section>

      <Stack gap="xs" mt="md" align="center">
        <Text fw={600} ta="center" lineClamp={2}>
          {app.name}
        </Text>
        <Text size="xs" c="dimmed" ta="center">
          {app.bundle_id}
        </Text>
        <Badge
          variant="light"
          color={PLATFORM_COLORS[app.platform] ?? "gray"}
          size="sm"
        >
          {app.platform}
        </Badge>
      </Stack>
    </Card>
  );
}

function SkeletonCards() {
  return (
    <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i} shadow="sm" padding="lg" radius="md" withBorder>
          <Card.Section p="md" pb={0}>
            <Group justify="center">
              <Skeleton height={64} width={64} radius="md" />
            </Group>
          </Card.Section>
          <Stack gap="xs" mt="md" align="center">
            <Skeleton height={20} width="60%" />
            <Skeleton height={14} width="80%" />
            <Skeleton height={22} width={60} radius="xl" />
          </Stack>
        </Card>
      ))}
    </SimpleGrid>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { data: apps, isLoading } = useApps();
  const syncMutation = useSyncApps();

  return (
    <Container size="lg">
      <Group justify="space-between" mb="lg">
        <div>
          <Title order={2}>Your Apps</Title>
          <Text c="dimmed" size="sm" mt={4}>
            Apps synced from App Store Connect.
          </Text>
        </div>
        <Button
          leftSection={<IconRefresh size={16} />}
          onClick={() => syncMutation.mutate()}
          loading={syncMutation.isPending}
        >
          Sync Apps
        </Button>
      </Group>

      {isLoading ? (
        <SkeletonCards />
      ) : !apps || apps.length === 0 ? (
        <Card withBorder p="xl" ta="center" radius="md">
          <Stack align="center" gap="sm">
            <IconApps size={48} color="var(--mantine-color-dimmed)" />
            <Title order={4} c="dimmed">
              No apps yet
            </Title>
            <Text c="dimmed" size="sm" maw={400}>
              Add credentials and sync your apps from App Store Connect to get
              started.
            </Text>
            <Group mt="sm">
              <Button
                variant="light"
                leftSection={<IconPlus size={16} />}
                onClick={() => navigate("/credentials")}
              >
                Add Credentials
              </Button>
              <Button
                leftSection={<IconRefresh size={16} />}
                onClick={() => syncMutation.mutate()}
                loading={syncMutation.isPending}
              >
                Sync Apps
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
          {apps.map((app) => (
            <AppCard
              key={app.id}
              app={app}
              onClick={() => navigate(`/apps/${app.id}/pricing`)}
            />
          ))}
        </SimpleGrid>
      )}
    </Container>
  );
}
