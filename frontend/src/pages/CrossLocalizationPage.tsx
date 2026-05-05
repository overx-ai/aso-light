import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Box,
  Card,
  Container,
  Group,
  Loader,
  Stack,
  Switch,
  Table,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconAlertCircle, IconLanguage } from "@tabler/icons-react";
import { useAppMetadata, useCrossLocalizationGrid } from "@/lib/hooks";
import type {
  AppMetadataSnapshot,
  CrossLocalizationGridItem,
} from "@/types";

// ---- Helpers ----

interface TerritoryInfo {
  gdp: number | null;
  locales: string[];
}

/**
 * Pivot the flat grid items into territory rows. Each territory maps to its
 * GDP per capita (USD) and the set of locales indexed in that storefront.
 */
function pivotByTerritory(
  items: CrossLocalizationGridItem[],
): Map<string, TerritoryInfo> {
  const pivoted = new Map<string, TerritoryInfo>();
  for (const item of items) {
    const existing = pivoted.get(item.territory_code);
    if (existing) {
      if (!existing.locales.includes(item.locale)) {
        existing.locales.push(item.locale);
      }
    } else {
      pivoted.set(item.territory_code, {
        gdp: item.gdp_per_capita_usd,
        locales: [item.locale],
      });
    }
  }
  return pivoted;
}

const GDP_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function formatGdp(value: number): string {
  return GDP_FORMATTER.format(value);
}

/**
 * Collect every locale that has any kind of metadata filled (app_info or
 * appStoreVersion). Used to overlay coverage on the indexed-locale dots.
 */
function collectLocalesWithMetadata(
  snapshot: AppMetadataSnapshot | null | undefined,
): Set<string> {
  if (!snapshot) return new Set();
  const locales = new Set<string>();
  for (const row of snapshot.app_info) locales.add(row.locale);
  for (const row of snapshot.versions) locales.add(row.locale);
  return locales;
}

// ---- Page ----

export default function CrossLocalizationPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const inAppContext = appId > 0;

  const { data: grid, isLoading, error } = useCrossLocalizationGrid();
  // useAppMetadata gates on `enabled: !!appId`, so passing 0 is a no-op.
  const { data: snapshot } = useAppMetadata(appId);

  const [sortBy, setSortBy] = useState<"gdp" | "territory">("gdp");

  const { sortedTerritories, allLocales, localesWithMetadata } = useMemo(() => {
    const items = grid?.items ?? [];
    const territories = pivotByTerritory(items);
    const locales = Array.from(new Set(items.map((i) => i.locale))).sort();
    const sorted =
      sortBy === "gdp"
        ? Array.from(territories.entries()).sort(
            (a, b) => (b[1].gdp ?? -Infinity) - (a[1].gdp ?? -Infinity),
          )
        : Array.from(territories.entries()).sort((a, b) =>
            a[0].localeCompare(b[0]),
          );
    return {
      sortedTerritories: sorted,
      allLocales: locales,
      localesWithMetadata: collectLocalesWithMetadata(snapshot),
    };
  }, [grid, snapshot, sortBy]);

  return (
    <Container size="xl">
      <div style={{ marginBottom: "var(--mantine-spacing-lg)" }}>
        <Group gap="sm" align="center">
          <IconLanguage size={22} />
          <Title order={2}>Cross-Localization</Title>
          <Tooltip
            multiline
            w={320}
            label={
              "Apple indexes secondary-language content into related App Store " +
              "territories. Filling es-MX, for example, surfaces those keywords " +
              "in BR/AR/CL/CO/PE storefronts as well. This data is " +
              "community-derived and last verified 2026-05."
            }
          >
            <Badge color="yellow" variant="light" style={{ cursor: "help" }}>
              Community-derived data
            </Badge>
          </Tooltip>
        </Group>
        <Text c="dimmed" size="sm" mt={4}>
          Territories x indexed locales. Use this to plan which locales unlock
          the most storefronts before writing metadata.
        </Text>
      </div>

      {isLoading ? (
        <Group justify="center" py="xl">
          <Loader />
        </Group>
      ) : error ? (
        <Alert color="red" icon={<IconAlertCircle size={16} />} title="Failed to load">
          Could not load cross-localization data. Please refresh and try again.
        </Alert>
      ) : (
        <Stack gap="md">
          <Group justify="space-between">
            <Switch
              label="Sort by GDP per capita"
              checked={sortBy === "gdp"}
              onChange={(e) =>
                setSortBy(e.currentTarget.checked ? "gdp" : "territory")
              }
            />
            {inAppContext && (
              <Group gap="xs">
                <Group gap={4}>
                  <Box
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: "var(--mantine-color-green-6)",
                    }}
                  />
                  <Text size="xs" c="dimmed">
                    metadata filled
                  </Text>
                </Group>
                <Group gap={4}>
                  <Box
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: "var(--mantine-color-blue-3)",
                    }}
                  />
                  <Text size="xs" c="dimmed">
                    indexed, no metadata
                  </Text>
                </Group>
              </Group>
            )}
          </Group>

          <Card withBorder padding={0} radius="md">
            <Box style={{ overflowX: "auto" }}>
              <Table striped withTableBorder={false} highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ minWidth: 80 }}>Territory</Table.Th>
                    <Table.Th style={{ minWidth: 110 }}>GDP/cap</Table.Th>
                    {allLocales.map((loc) => (
                      <Table.Th
                        key={loc}
                        style={{
                          writingMode: "vertical-rl",
                          transform: "rotate(180deg)",
                          minWidth: 32,
                          padding: "8px 4px",
                          fontFamily: "var(--mantine-font-family-monospace)",
                          fontSize: 11,
                          textAlign: "center",
                        }}
                      >
                        {loc}
                      </Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {sortedTerritories.map(([code, info]) => (
                    <Table.Tr key={code}>
                      <Table.Td>
                        <Text fw={600} size="sm">
                          {code}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c="dimmed">
                          {info.gdp != null ? formatGdp(info.gdp) : "--"}
                        </Text>
                      </Table.Td>
                      {allLocales.map((loc) => {
                        const indexed = info.locales.includes(loc);
                        if (!indexed) {
                          return (
                            <Table.Td
                              key={loc}
                              style={{ background: "var(--mantine-color-gray-0)" }}
                            />
                          );
                        }
                        const hasMeta = localesWithMetadata.has(loc);
                        const tooltipLabel = inAppContext
                          ? `${loc} indexed in ${code} - ${
                              hasMeta ? "metadata filled" : "no metadata yet"
                            }`
                          : `${loc} indexed in ${code}`;
                        const dotColor =
                          inAppContext && hasMeta
                            ? "var(--mantine-color-green-6)"
                            : "var(--mantine-color-blue-3)";
                        return (
                          <Table.Td key={loc} style={{ textAlign: "center" }}>
                            <Tooltip label={tooltipLabel} withArrow>
                              <Box
                                style={{
                                  width: 12,
                                  height: 12,
                                  borderRadius: "50%",
                                  background: dotColor,
                                  margin: "0 auto",
                                }}
                              />
                            </Tooltip>
                          </Table.Td>
                        );
                      })}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Box>
          </Card>
        </Stack>
      )}
    </Container>
  );
}
