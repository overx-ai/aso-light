import { useMemo, useState } from "react";
import {
  Paper,
  Text,
  TextInput,
  Group,
  Stack,
  Badge,
  ScrollArea,
  Table,
  Loader,
} from "@mantine/core";
import { IconSearch, IconCheck } from "@tabler/icons-react";
import type { CrossLocalizationEntry } from "@/types";

interface CrossLocalizationMatrixProps {
  data: CrossLocalizationEntry[];
  isLoading: boolean;
}

// Ordered locales for columns
const LOCALE_ORDER = [
  "en-US",
  "en-GB",
  "en-AU",
  "en-CA",
  "es-MX",
  "es-ES",
  "fr-FR",
  "fr-CA",
  "de-DE",
  "it",
  "pt-BR",
  "pt-PT",
  "ja",
  "zh-Hans",
  "zh-Hant",
  "ko",
  "ru",
  "ar",
  "nl",
  "sv",
  "pl",
  "tr",
  "th",
  "vi",
  "id",
  "ms",
  "hi",
  "he",
  "nb",
  "da",
  "fi",
  "cs",
  "ro",
  "hu",
  "el",
  "uk",
];

const TERRITORY_NAMES: Record<string, string> = {
  US: "United States",
  GB: "United Kingdom",
  DE: "Germany",
  FR: "France",
  JP: "Japan",
  CN: "China",
  KR: "South Korea",
  RU: "Russia",
  BR: "Brazil",
  MX: "Mexico",
  IT: "Italy",
  ES: "Spain",
  AU: "Australia",
  CA: "Canada",
  IN: "India",
  TR: "Turkey",
  NL: "Netherlands",
  SE: "Sweden",
  PL: "Poland",
  TW: "Taiwan",
  SA: "Saudi Arabia",
  AE: "UAE",
  TH: "Thailand",
  VN: "Vietnam",
  ID: "Indonesia",
  PT: "Portugal",
  NO: "Norway",
  DK: "Denmark",
  FI: "Finland",
  AT: "Austria",
  CH: "Switzerland",
  BE: "Belgium",
  IL: "Israel",
  SG: "Singapore",
  HK: "Hong Kong",
  MY: "Malaysia",
  PH: "Philippines",
  CO: "Colombia",
  AR: "Argentina",
  CL: "Chile",
  PE: "Peru",
  EG: "Egypt",
  ZA: "South Africa",
  NG: "Nigeria",
  NZ: "New Zealand",
  IE: "Ireland",
  CZ: "Czech Republic",
  RO: "Romania",
  HU: "Hungary",
  GR: "Greece",
  UA: "Ukraine",
};

export default function CrossLocalizationMatrix({
  data,
  isLoading,
}: CrossLocalizationMatrixProps) {
  const [search, setSearch] = useState("");

  const { territories, locales, indexMap } = useMemo(() => {
    // Build a set of territories and locales present in data
    const territorySet = new Set<string>();
    const localeSet = new Set<string>();
    const iMap = new Map<string, boolean>();

    for (const entry of data) {
      territorySet.add(entry.territory_code);
      localeSet.add(entry.locale);
      if (entry.is_indexed) {
        iMap.set(`${entry.territory_code}:${entry.locale}`, true);
      }
    }

    // Sort territories alphabetically
    const sortedTerritories = Array.from(territorySet).sort();

    // Sort locales using predefined order
    const sortedLocales = LOCALE_ORDER.filter((l) => localeSet.has(l));
    // Add any locales not in LOCALE_ORDER
    for (const l of localeSet) {
      if (!sortedLocales.includes(l)) {
        sortedLocales.push(l);
      }
    }

    return {
      territories: sortedTerritories,
      locales: sortedLocales,
      indexMap: iMap,
    };
  }, [data]);

  const filteredTerritories = useMemo(() => {
    if (!search.trim()) return territories;
    const lower = search.toLowerCase();
    return territories.filter((tc) => {
      const name = TERRITORY_NAMES[tc] ?? tc;
      return (
        tc.toLowerCase().includes(lower) || name.toLowerCase().includes(lower)
      );
    });
  }, [territories, search]);

  if (isLoading) {
    return (
      <Paper withBorder p="xl" ta="center" radius="md">
        <Loader size="sm" />
      </Paper>
    );
  }

  if (data.length === 0) {
    return (
      <Paper withBorder p="xl" ta="center" radius="md">
        <Text c="dimmed" size="sm">
          No cross-localization data available.
        </Text>
      </Paper>
    );
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <TextInput
          placeholder="Search territory..."
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          style={{ maxWidth: 300 }}
          size="sm"
        />
        <Group gap="xs">
          <Badge color="green" variant="light" size="sm">
            <IconCheck size={10} />
          </Badge>
          <Text size="xs" c="dimmed">
            = locale is indexed in territory
          </Text>
        </Group>
      </Group>

      <Paper withBorder radius="md">
        <ScrollArea>
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th
                  style={{
                    position: "sticky",
                    left: 0,
                    background: "var(--mantine-color-body)",
                    zIndex: 1,
                    minWidth: 160,
                  }}
                >
                  Territory
                </Table.Th>
                {locales.map((locale) => (
                  <Table.Th
                    key={locale}
                    style={{
                      textAlign: "center",
                      minWidth: 70,
                      fontSize: "var(--mantine-font-size-xs)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {locale}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredTerritories.map((tc) => (
                <Table.Tr key={tc}>
                  <Table.Td
                    style={{
                      position: "sticky",
                      left: 0,
                      background: "var(--mantine-color-body)",
                      zIndex: 1,
                    }}
                  >
                    <Group gap={6}>
                      <Text fw={600} size="sm">
                        {tc}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {TERRITORY_NAMES[tc] ?? ""}
                      </Text>
                    </Group>
                  </Table.Td>
                  {locales.map((locale) => {
                    const isIndexed = indexMap.has(`${tc}:${locale}`);
                    return (
                      <Table.Td
                        key={locale}
                        style={{
                          textAlign: "center",
                          background: isIndexed
                            ? "var(--mantine-color-green-0)"
                            : undefined,
                        }}
                      >
                        {isIndexed && (
                          <IconCheck
                            size={14}
                            color="var(--mantine-color-green-6)"
                          />
                        )}
                      </Table.Td>
                    );
                  })}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Paper>

      <Text size="xs" c="dimmed">
        {filteredTerritories.length} territories, {locales.length} locales
      </Text>
    </Stack>
  );
}
