import { useMemo } from "react";
import {
  Badge,
  Drawer,
  Group,
  Image,
  Loader,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import { useVisibilitySnapshots } from "@/lib/hooks";
import type { VisibilityWatchOut } from "@/types";

interface VisibilityDrawerProps {
  appId: number;
  watch: VisibilityWatchOut | null;
  opened: boolean;
  onClose: () => void;
}

export default function VisibilityDrawer({
  appId,
  watch,
  opened,
  onClose,
}: VisibilityDrawerProps) {
  const snapshotsQuery = useVisibilitySnapshots(appId, watch?.id ?? 0, 30);

  const latestResults = watch?.latest_snapshot?.results ?? [];

  const positionsByTrack = useMemo(() => {
    const map = new Map<string, number[]>();
    if (!snapshotsQuery.data) return map;
    for (const snap of snapshotsQuery.data.items) {
      for (const result of snap.results) {
        const arr = map.get(result.track_id) ?? [];
        arr.push(result.position);
        map.set(result.track_id, arr);
      }
    }
    return map;
  }, [snapshotsQuery.data]);

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size="lg"
      title={
        watch ? (
          <Group gap="xs">
            <Text fw={600} size="sm">
              {watch.text}
            </Text>
            <Badge size="xs" variant="light" color="gray">
              {watch.country.toUpperCase()}
            </Badge>
            {watch.last_polled_at && (
              <Text size="xs" c="dimmed">
                last poll {new Date(watch.last_polled_at).toLocaleString()}
              </Text>
            )}
          </Group>
        ) : (
          "Visibility"
        )
      }
    >
      {!watch ? null : (
        <Stack gap="md">
          <div>
            <Text size="xs" fw={600} c="dimmed" tt="uppercase" mb={6}>
              Latest top {latestResults.length}
            </Text>
            {latestResults.length === 0 ? (
              <Text size="sm" c="dimmed">
                No snapshot yet. Click "Poll now" on the watch row.
              </Text>
            ) : (
              <Stack gap={4}>
                {latestResults.map((r) => {
                  const history = positionsByTrack.get(r.track_id) ?? [];
                  const best = history.length ? Math.min(...history) : r.position;
                  const worst = history.length ? Math.max(...history) : r.position;
                  return (
                    <Group key={r.track_id} gap="sm" wrap="nowrap">
                      <Text size="xs" w={32} ta="right" fw={600} c="dimmed">
                        #{r.position}
                      </Text>
                      <Image
                        src={r.icon_url}
                        w={32}
                        h={32}
                        radius="sm"
                        fallbackSrc="https://placehold.co/32?text=?"
                      />
                      <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                        <Text size="sm" fw={500} truncate>
                          {r.name}
                        </Text>
                        <Text size="xs" c="dimmed" truncate>
                          {r.bundle_id || "—"}
                        </Text>
                      </Stack>
                      {history.length > 0 && (
                        <Tooltip
                          withArrow
                          label={`Seen ${history.length} times · range #${best}–#${worst}`}
                        >
                          <Badge
                            size="xs"
                            variant="light"
                            color={best <= 3 ? "green" : "blue"}
                          >
                            best #{best}
                          </Badge>
                        </Tooltip>
                      )}
                    </Group>
                  );
                })}
              </Stack>
            )}
          </div>

          <div>
            <Group justify="space-between" mb={6}>
              <Text size="xs" fw={600} c="dimmed" tt="uppercase">
                Snapshots (last 30 days)
              </Text>
              {snapshotsQuery.isLoading && <Loader size="xs" />}
            </Group>
            <Stack gap={2}>
              {(snapshotsQuery.data?.items ?? []).map((snap) => (
                <Group
                  key={snap.id}
                  gap="xs"
                  justify="space-between"
                  wrap="nowrap"
                >
                  <Text size="xs">
                    {new Date(snap.polled_at).toLocaleString()}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {snap.results_count} results
                  </Text>
                </Group>
              ))}
              {snapshotsQuery.data?.items?.length === 0 && (
                <Text size="xs" c="dimmed">
                  No history yet.
                </Text>
              )}
            </Stack>
          </div>
        </Stack>
      )}
    </Drawer>
  );
}
