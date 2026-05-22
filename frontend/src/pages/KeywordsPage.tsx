import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams } from "react-router-dom";
import {
  Container,
  Title,
  Text,
  Tabs,
  Paper,
  Stack,
  Skeleton,
  Group,
  Button,
  TextInput,
  Select,
  Modal,
  Badge,
  ActionIcon,
  Image,
  Switch,
} from "@mantine/core";
import { useDisclosure, useDebouncedValue } from "@mantine/hooks";
import {
  IconSearch,
  IconTarget,
  IconLanguage,
  IconUsers,
  IconPlus,
  IconRefresh,
  IconTrash,
  IconChevronDown,
  IconChevronRight,
} from "@tabler/icons-react";
import { DataTable } from "mantine-datatable";
import {
  useApp,
  useTrackedKeywords,
  useAddKeyword,
  useRemoveKeyword,
  useKeywordRankings,
  useRefreshKeywordRankings,
  useKeywordSuggestions,
  useKeywordSearch,
  useCrossLocalization,
  useCompetitors,
  useAddCompetitor,
  useRemoveCompetitor,
  useCompetitorKeywords,
  useKeywordCoverage,
  usePaidOrganicJoin,
} from "@/lib/hooks";
import RankHistoryChart from "@/components/keywords/RankHistoryChart";
import CrossLocalizationMatrix from "@/components/keywords/CrossLocalizationMatrix";
import KeywordIntelBadge from "@/components/keywords/keywordIntel";
import KeywordCoverageDots from "@/components/metadata/KeywordCoverageDots";
import type {
  KeywordTrackingResponse,
  KeywordSearchResult,
  CompetitorApp,
  CompetitorKeywordResult,
  KeywordPlacement,
} from "@/types";

// ---- Tracked Keywords Tab ----

function TrackedKeywordsTab({ appId }: { appId: string }) {
  const { data: trackings, isLoading } = useTrackedKeywords(appId);
  const coverage = useKeywordCoverage(Number(appId));
  const addKeywordMutation = useAddKeyword();
  const removeKeywordMutation = useRemoveKeyword();
  const refreshMutation = useRefreshKeywordRankings();
  const [addModalOpened, addModalHandlers] = useDisclosure(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // ---- Paid metrics toggle (persists per-app to localStorage) ----
  const paidStorageKey = `paid_toggle_${appId}`;
  const [withPaid, setWithPaid] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(paidStorageKey) === "1";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(paidStorageKey, withPaid ? "1" : "0");
  }, [withPaid, paidStorageKey]);

  const paidJoin = usePaidOrganicJoin(withPaid ? Number(appId) : 0, 30);
  const paidByTerm = useMemo(() => {
    const map = new Map<
      string,
      {
        impressions: number;
        taps: number;
        installs: number;
        spend: string;
        currency: string | null;
      }
    >();
    for (const row of paidJoin.data ?? []) {
      map.set(row.term.toLowerCase(), {
        impressions: row.paid_impressions_30d,
        taps: row.paid_taps_30d,
        installs: row.paid_installs_30d,
        spend: row.paid_spend_30d,
        currency: row.paid_spend_currency,
      });
    }
    return map;
  }, [paidJoin.data]);

  // keyword (lowercased) -> [(locale, placement)] across all locales/fields
  const coverageByKeyword = useMemo(() => {
    const map = new Map<
      string,
      Array<{ locale: string; placement: KeywordPlacement }>
    >();
    for (const item of coverage.data?.items ?? []) {
      if (item.placement === "none") continue;
      const key = item.keyword.toLowerCase();
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push({ locale: item.locale, placement: item.placement });
    }
    return map;
  }, [coverage.data]);

  // Add keyword form state
  const [newKeywordText, setNewKeywordText] = useState("");
  const [newKeywordLocale, setNewKeywordLocale] = useState("en-US");

  const handleAddKeyword = useCallback(() => {
    if (!newKeywordText.trim()) return;
    addKeywordMutation.mutate(
      { appId, text: newKeywordText.trim(), locale: newKeywordLocale },
      {
        onSuccess: () => {
          addModalHandlers.close();
          setNewKeywordText("");
        },
      },
    );
  }, [
    addKeywordMutation,
    appId,
    newKeywordText,
    newKeywordLocale,
    addModalHandlers,
  ]);

  const handleRefresh = useCallback(() => {
    refreshMutation.mutate({ appId });
  }, [refreshMutation, appId]);

  const toggleExpand = useCallback(
    (id: number) => {
      setExpandedId(expandedId === id ? null : id);
    },
    [expandedId],
  );

  return (
    <>
      <Stack gap="md">
        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            {trackings?.length ?? 0} tracked keyword(s)
          </Text>
          <Group gap="xs">
            <Switch
              size="sm"
              label="Show paid metrics"
              checked={withPaid}
              onChange={(e) => setWithPaid(e.currentTarget.checked)}
            />
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              onClick={handleRefresh}
              loading={refreshMutation.isPending}
            >
              Refresh Rankings
            </Button>
            <Button
              size="xs"
              leftSection={<IconPlus size={14} />}
              onClick={addModalHandlers.open}
            >
              Add Keyword
            </Button>
          </Group>
        </Group>

        <Paper withBorder radius="md">
          <DataTable
            minHeight={150}
            fetching={isLoading}
            records={trackings ?? []}
            idAccessor="id"
            noRecordsText="No keywords tracked yet"
            columns={[
              {
                accessor: "expand",
                title: "",
                width: 36,
                render: (row: KeywordTrackingResponse) => (
                  <ActionIcon
                    variant="subtle"
                    size="sm"
                    onClick={() => toggleExpand(row.id)}
                  >
                    {expandedId === row.id ? (
                      <IconChevronDown size={14} />
                    ) : (
                      <IconChevronRight size={14} />
                    )}
                  </ActionIcon>
                ),
              },
              {
                accessor: "keyword.text",
                title: "Keyword",
                render: (row: KeywordTrackingResponse) => (
                  <Text fw={500} size="sm">
                    {row.keyword.text}
                  </Text>
                ),
              },
              {
                accessor: "keyword.locale",
                title: "Locale",
                width: 90,
                render: (row: KeywordTrackingResponse) => (
                  <Badge variant="light" size="sm" color="gray">
                    {row.keyword.locale}
                  </Badge>
                ),
              },
              {
                accessor: "keyword.popularity",
                title: "Intel",
                width: 110,
                render: (row: KeywordTrackingResponse) => (
                  <KeywordIntelBadge
                    popularity={row.keyword.popularity}
                    updatedAt={row.keyword.popularity_updated_at}
                  />
                ),
              },
              {
                accessor: "latest_rank",
                title: "Rank",
                width: 80,
                textAlign: "center" as const,
                render: (row: KeywordTrackingResponse) =>
                  row.latest_rank !== null ? (
                    <Text size="sm" fw={600}>
                      #{row.latest_rank}
                    </Text>
                  ) : (
                    <Text size="sm" c="dimmed">
                      --
                    </Text>
                  ),
              },
              {
                accessor: "rank_change",
                title: "Change",
                width: 90,
                textAlign: "center" as const,
                render: (row: KeywordTrackingResponse) => {
                  if (row.rank_change === null || row.rank_change === 0) {
                    return (
                      <Text size="sm" c="dimmed">
                        --
                      </Text>
                    );
                  }
                  const isPositive = row.rank_change > 0;
                  return (
                    <Badge
                      color={isPositive ? "green" : "red"}
                      variant="light"
                      size="sm"
                    >
                      {isPositive ? "+" : ""}
                      {row.rank_change}
                    </Badge>
                  );
                },
              },
              {
                accessor: "added_at",
                title: "Added",
                width: 120,
                render: (row: KeywordTrackingResponse) => (
                  <Text size="xs" c="dimmed">
                    {new Date(row.added_at).toLocaleDateString()}
                  </Text>
                ),
              },
              {
                accessor: "coverage",
                title: "Coverage",
                width: 160,
                render: (row: KeywordTrackingResponse) => (
                  <KeywordCoverageDots
                    placements={
                      coverageByKeyword.get(row.keyword.text.toLowerCase()) ??
                      []
                    }
                  />
                ),
              },
              ...(withPaid
                ? [
                    {
                      accessor: "paid_impressions",
                      title: "Imp 30d",
                      width: 90,
                      textAlign: "right" as const,
                      render: (row: KeywordTrackingResponse) => {
                        const m = paidByTerm.get(
                          row.keyword.text.toLowerCase(),
                        );
                        return m && m.impressions > 0 ? (
                          <Text size="sm">
                            {m.impressions.toLocaleString()}
                          </Text>
                        ) : (
                          <Text size="sm" c="dimmed">
                            --
                          </Text>
                        );
                      },
                    },
                    {
                      accessor: "paid_taps",
                      title: "Taps 30d",
                      width: 90,
                      textAlign: "right" as const,
                      render: (row: KeywordTrackingResponse) => {
                        const m = paidByTerm.get(
                          row.keyword.text.toLowerCase(),
                        );
                        return m && m.taps > 0 ? (
                          <Text size="sm">{m.taps.toLocaleString()}</Text>
                        ) : (
                          <Text size="sm" c="dimmed">
                            --
                          </Text>
                        );
                      },
                    },
                    {
                      accessor: "paid_installs",
                      title: "Inst 30d",
                      width: 90,
                      textAlign: "right" as const,
                      render: (row: KeywordTrackingResponse) => {
                        const m = paidByTerm.get(
                          row.keyword.text.toLowerCase(),
                        );
                        return m && m.installs > 0 ? (
                          <Text size="sm">
                            {m.installs.toLocaleString()}
                          </Text>
                        ) : (
                          <Text size="sm" c="dimmed">
                            --
                          </Text>
                        );
                      },
                    },
                    {
                      accessor: "paid_spend",
                      title: "Spend 30d",
                      width: 130,
                      textAlign: "right" as const,
                      render: (row: KeywordTrackingResponse) => {
                        const m = paidByTerm.get(
                          row.keyword.text.toLowerCase(),
                        );
                        if (!m || parseFloat(m.spend) === 0) {
                          return (
                            <Text size="sm" c="dimmed">
                              --
                            </Text>
                          );
                        }
                        return (
                          <Group gap={4} justify="flex-end" wrap="nowrap">
                            <Text size="sm">
                              {parseFloat(m.spend).toFixed(2)}
                            </Text>
                            {m.currency && (
                              <Badge size="xs" variant="light" color="gray">
                                {m.currency}
                              </Badge>
                            )}
                          </Group>
                        );
                      },
                    },
                  ]
                : []),
              {
                accessor: "actions",
                title: "",
                width: 50,
                textAlign: "center" as const,
                render: (row: KeywordTrackingResponse) => (
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    size="sm"
                    onClick={() =>
                      removeKeywordMutation.mutate({
                        appId,
                        trackingId: row.id,
                      })
                    }
                    loading={removeKeywordMutation.isPending}
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                ),
              },
            ]}
            rowExpansion={{
              allowMultiple: false,
              expanded: {
                recordIds: expandedId !== null ? [expandedId] : [],
                onRecordIdsChange: (ids: unknown[]) =>
                  setExpandedId(ids.length > 0 ? (ids[0] as number) : null),
              },
              content: ({ record }) => (
                <RankHistoryPanel appId={appId} trackingId={record.id} />
              ),
            }}
          />
        </Paper>
      </Stack>

      <Modal
        opened={addModalOpened}
        onClose={addModalHandlers.close}
        title="Add Keyword to Track"
        size="sm"
      >
        <Stack gap="md">
          <TextInput
            label="Keyword"
            placeholder="e.g., fitness tracker"
            value={newKeywordText}
            onChange={(e) => setNewKeywordText(e.currentTarget.value)}
            required
          />
          <Select
            label="Locale"
            data={[
              { value: "en-US", label: "English (US)" },
              { value: "en-GB", label: "English (UK)" },
              { value: "de-DE", label: "German" },
              { value: "fr-FR", label: "French" },
              { value: "es-ES", label: "Spanish (Spain)" },
              { value: "es-MX", label: "Spanish (Mexico)" },
              { value: "pt-BR", label: "Portuguese (Brazil)" },
              { value: "ja", label: "Japanese" },
              { value: "zh-Hans", label: "Chinese (Simplified)" },
              { value: "zh-Hant", label: "Chinese (Traditional)" },
              { value: "ko", label: "Korean" },
              { value: "ru", label: "Russian" },
              { value: "it", label: "Italian" },
              { value: "nl", label: "Dutch" },
              { value: "ar", label: "Arabic" },
            ]}
            value={newKeywordLocale}
            onChange={(v) => setNewKeywordLocale(v ?? "en-US")}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={addModalHandlers.close}>
              Cancel
            </Button>
            <Button
              onClick={handleAddKeyword}
              loading={addKeywordMutation.isPending}
              disabled={!newKeywordText.trim()}
            >
              Add
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}

function RankHistoryPanel({
  appId,
  trackingId,
}: {
  appId: string;
  trackingId: number;
}) {
  const { data: histories, isLoading } = useKeywordRankings(
    appId,
    String(trackingId),
  );

  return (
    <div style={{ padding: "var(--mantine-spacing-sm)" }}>
      <RankHistoryChart histories={histories ?? []} isLoading={isLoading} />
    </div>
  );
}

// ---- Search Tab ----

function SearchTab({ appId }: { appId: string }) {
  const [searchTerm, setSearchTerm] = useState("");
  const [debouncedTerm] = useDebouncedValue(searchTerm, 400);
  const [searchLocale, setSearchLocale] = useState("en_us");
  const [searchCountry, setSearchCountry] = useState("us");
  const addKeywordMutation = useAddKeyword();
  const searchMutation = useKeywordSearch();
  const { data: suggestions } = useKeywordSuggestions(
    debouncedTerm,
    searchLocale,
  );

  const handleSearch = useCallback(() => {
    if (!searchTerm.trim()) return;
    searchMutation.mutate({ term: searchTerm.trim(), country: searchCountry });
  }, [searchMutation, searchTerm, searchCountry]);

  const handleTrackKeyword = useCallback(
    (text: string) => {
      // Convert locale from "en_us" to "en-US" format
      const locale = searchLocale
        .split("_")
        .map((p, i) => (i === 0 ? p.toLowerCase() : p.toUpperCase()))
        .join("-");
      addKeywordMutation.mutate({ appId, text, locale });
    },
    [addKeywordMutation, appId, searchLocale],
  );

  return (
    <Stack gap="md">
      <Group align="flex-end">
        <TextInput
          label="Search Keyword"
          placeholder="Type to search..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.currentTarget.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          style={{ flex: 1, maxWidth: 400 }}
          size="sm"
        />
        <Select
          label="Country"
          data={[
            { value: "us", label: "US" },
            { value: "gb", label: "UK" },
            { value: "de", label: "DE" },
            { value: "fr", label: "FR" },
            { value: "jp", label: "JP" },
            { value: "cn", label: "CN" },
            { value: "kr", label: "KR" },
            { value: "br", label: "BR" },
            { value: "au", label: "AU" },
            { value: "ca", label: "CA" },
          ]}
          value={searchCountry}
          onChange={(v) => {
            setSearchCountry(v ?? "us");
            setSearchLocale((v ?? "us") + "_" + (v ?? "us"));
          }}
          size="sm"
          w={100}
        />
        <Button
          size="sm"
          onClick={handleSearch}
          loading={searchMutation.isPending}
          leftSection={<IconSearch size={14} />}
        >
          Search
        </Button>
      </Group>

      {/* Suggestions */}
      {suggestions && suggestions.length > 0 && (
        <Paper withBorder p="sm" radius="md">
          <Text size="xs" fw={600} c="dimmed" mb="xs">
            Suggestions
          </Text>
          <Group gap="xs">
            {suggestions.map((s) => (
              <Badge
                key={s.term}
                variant="outline"
                size="sm"
                style={{ cursor: "pointer" }}
                onClick={() => {
                  setSearchTerm(s.term);
                  searchMutation.mutate({
                    term: s.term,
                    country: searchCountry,
                  });
                }}
              >
                {s.term}
              </Badge>
            ))}
          </Group>
        </Paper>
      )}

      {/* Search Results */}
      {searchMutation.data && (
        <Paper withBorder radius="md">
          <DataTable
            minHeight={150}
            records={searchMutation.data}
            idAccessor="app_id"
            noRecordsText="No results found"
            columns={[
              {
                accessor: "position",
                title: "#",
                width: 50,
                textAlign: "center" as const,
                render: (row: KeywordSearchResult) => (
                  <Text size="sm" fw={600}>
                    {row.position}
                  </Text>
                ),
              },
              {
                accessor: "icon",
                title: "",
                width: 40,
                render: (row: KeywordSearchResult) =>
                  row.icon_url ? (
                    <Image
                      src={row.icon_url}
                      alt={row.name}
                      w={28}
                      h={28}
                      radius={6}
                    />
                  ) : null,
              },
              {
                accessor: "name",
                title: "App",
                render: (row: KeywordSearchResult) => (
                  <Stack gap={0}>
                    <Text size="sm" fw={500}>
                      {row.name}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {row.bundle_id}
                    </Text>
                  </Stack>
                ),
              },
              {
                accessor: "app_id",
                title: "iTunes ID",
                width: 120,
                render: (row: KeywordSearchResult) => (
                  <Text size="xs" c="dimmed">
                    {row.app_id}
                  </Text>
                ),
              },
            ]}
          />
        </Paper>
      )}

      {/* Track button for the searched term */}
      {searchTerm.trim() && (
        <Group>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconPlus size={14} />}
            onClick={() => handleTrackKeyword(searchTerm.trim())}
            loading={addKeywordMutation.isPending}
          >
            Track &quot;{searchTerm.trim()}&quot;
          </Button>
        </Group>
      )}
    </Stack>
  );
}

// ---- Cross-Localization Tab ----

function CrossLocalizationTab() {
  const { data, isLoading } = useCrossLocalization();

  return (
    <CrossLocalizationMatrix data={data ?? []} isLoading={isLoading} />
  );
}

// ---- Competitors Tab ----

function CompetitorsTab({ appId }: { appId: string }) {
  const { data: competitors, isLoading } = useCompetitors(appId);
  const removeMutation = useRemoveCompetitor();
  const checkKeywordsMutation = useCompetitorKeywords();
  const [addModalOpened, addModalHandlers] = useDisclosure(false);
  const [keywordsResults, setKeywordsResults] = useState<{
    competitorId: number;
    results: CompetitorKeywordResult[];
  } | null>(null);

  const handleCheckKeywords = useCallback(
    (competitorId: number) => {
      checkKeywordsMutation.mutate(
        { appId, competitorId },
        {
          onSuccess: (results) =>
            setKeywordsResults({ competitorId, results }),
        },
      );
    },
    [checkKeywordsMutation, appId],
  );

  return (
    <>
      <Stack gap="md">
        <Group justify="space-between">
          <Text size="sm" c="dimmed">
            {competitors?.length ?? 0} competitor(s)
          </Text>
          <Button
            size="xs"
            leftSection={<IconPlus size={14} />}
            onClick={addModalHandlers.open}
          >
            Add Competitor
          </Button>
        </Group>

        <Paper withBorder radius="md">
          <DataTable
            minHeight={150}
            fetching={isLoading}
            records={competitors ?? []}
            idAccessor="id"
            noRecordsText="No competitors added yet"
            columns={[
              {
                accessor: "name",
                title: "Name",
                render: (row: CompetitorApp) => (
                  <Text fw={500} size="sm">
                    {row.name}
                  </Text>
                ),
              },
              {
                accessor: "asc_app_id",
                title: "iTunes ID",
                width: 130,
                render: (row: CompetitorApp) => (
                  <Text size="xs" c="dimmed">
                    {row.asc_app_id}
                  </Text>
                ),
              },
              {
                accessor: "bundle_id",
                title: "Bundle ID",
                render: (row: CompetitorApp) => (
                  <Text size="xs" c="dimmed">
                    {row.bundle_id ?? "--"}
                  </Text>
                ),
              },
              {
                accessor: "actions",
                title: "",
                width: 180,
                textAlign: "right" as const,
                render: (row: CompetitorApp) => (
                  <Group gap="xs" justify="flex-end">
                    <Button
                      size="xs"
                      variant="light"
                      onClick={() => handleCheckKeywords(row.id)}
                      loading={
                        checkKeywordsMutation.isPending &&
                        checkKeywordsMutation.variables?.competitorId ===
                          row.id
                      }
                    >
                      Check Keywords
                    </Button>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      size="sm"
                      onClick={() =>
                        removeMutation.mutate({
                          appId,
                          competitorId: row.id,
                        })
                      }
                      loading={removeMutation.isPending}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Group>
                ),
              },
            ]}
          />
        </Paper>

        {/* Competitor keyword results */}
        {keywordsResults && (
          <Paper withBorder p="md" radius="md">
            <Stack gap="sm">
              <Group justify="space-between">
                <Text size="sm" fw={600}>
                  Keyword Rankings Comparison
                </Text>
                <Button
                  size="xs"
                  variant="subtle"
                  onClick={() => setKeywordsResults(null)}
                >
                  Close
                </Button>
              </Group>
              <DataTable
                minHeight={100}
                records={keywordsResults.results}
                idAccessor="keyword_text"
                noRecordsText="No tracked keywords to compare"
                columns={[
                  {
                    accessor: "keyword_text",
                    title: "Keyword",
                    render: (row: CompetitorKeywordResult) => (
                      <Text size="sm" fw={500}>
                        {row.keyword_text}
                      </Text>
                    ),
                  },
                  {
                    accessor: "our_rank",
                    title: "Our Rank",
                    width: 100,
                    textAlign: "center" as const,
                    render: (row: CompetitorKeywordResult) =>
                      row.our_rank !== null ? (
                        <Text size="sm" fw={600}>
                          #{row.our_rank}
                        </Text>
                      ) : (
                        <Text size="sm" c="dimmed">
                          --
                        </Text>
                      ),
                  },
                  {
                    accessor: "competitor_rank",
                    title: "Competitor Rank",
                    width: 130,
                    textAlign: "center" as const,
                    render: (row: CompetitorKeywordResult) =>
                      row.competitor_rank !== null ? (
                        <Text size="sm" fw={600}>
                          #{row.competitor_rank}
                        </Text>
                      ) : (
                        <Text size="sm" c="dimmed">
                          --
                        </Text>
                      ),
                  },
                  {
                    accessor: "territory_code",
                    title: "Territory",
                    width: 90,
                    render: (row: CompetitorKeywordResult) => (
                      <Badge variant="light" size="sm" color="gray">
                        {row.territory_code}
                      </Badge>
                    ),
                  },
                ]}
              />
            </Stack>
          </Paper>
        )}
      </Stack>

      <AddCompetitorModal
        appId={appId}
        opened={addModalOpened}
        onClose={addModalHandlers.close}
        existingAscIds={
          new Set((competitors ?? []).map((c) => c.asc_app_id))
        }
      />
    </>
  );
}

// ---- Add Competitor Modal (iTunes typeahead) ----

interface AddCompetitorModalProps {
  appId: string;
  opened: boolean;
  onClose: () => void;
  existingAscIds: Set<string>;
}

function AddCompetitorModal({
  appId,
  opened,
  onClose,
  existingAscIds,
}: AddCompetitorModalProps) {
  const [term, setTerm] = useState("");
  const [country, setCountry] = useState("us");
  const [debouncedTerm] = useDebouncedValue(term, 350);
  const [results, setResults] = useState<KeywordSearchResult[]>([]);
  const [justAdded, setJustAdded] = useState<Set<string>>(new Set());
  const [showManual, setShowManual] = useState(false);

  // Manual fallback state
  const [manualName, setManualName] = useState("");
  const [manualAscId, setManualAscId] = useState("");
  const [manualBundleId, setManualBundleId] = useState("");

  const search = useKeywordSearch();
  const add = useAddCompetitor();

  // Reset on open
  useEffect(() => {
    if (opened) {
      setTerm("");
      setResults([]);
      setJustAdded(new Set());
      setShowManual(false);
      setManualName("");
      setManualAscId("");
      setManualBundleId("");
    }
  }, [opened]);

  // Trigger search when debounced term changes
  useEffect(() => {
    if (!opened) return;
    const trimmed = debouncedTerm.trim();
    if (trimmed.length < 2) {
      setResults([]);
      return;
    }
    search.mutate(
      { term: trimmed, country },
      {
        onSuccess: (out) => setResults(out),
      },
    );
    // We intentionally exclude `search` from deps to avoid re-run loops
    // (mutation identity is stable enough for our needs).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedTerm, country, opened]);

  const handlePick = (r: KeywordSearchResult) => {
    if (existingAscIds.has(r.app_id) || justAdded.has(r.app_id)) return;
    add.mutate(
      {
        appId,
        asc_app_id: r.app_id,
        name: r.name,
        bundle_id: r.bundle_id || undefined,
      },
      {
        onSuccess: () => {
          setJustAdded((prev) => {
            const next = new Set(prev);
            next.add(r.app_id);
            return next;
          });
        },
      },
    );
  };

  const handleManualAdd = () => {
    if (!manualName.trim() || !manualAscId.trim()) return;
    add.mutate(
      {
        appId,
        asc_app_id: manualAscId.trim(),
        name: manualName.trim(),
        bundle_id: manualBundleId.trim() || undefined,
      },
      {
        onSuccess: () => {
          setManualName("");
          setManualAscId("");
          setManualBundleId("");
          setShowManual(false);
        },
      },
    );
  };

  const isSearching = search.isPending && debouncedTerm.trim().length >= 2;
  const trimmedTerm = debouncedTerm.trim();

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Add Competitor"
      size="lg"
    >
      <Stack gap="sm">
        <Group gap="xs" align="flex-end">
          <TextInput
            label="Search iTunes"
            placeholder="App name (e.g., Calm, Headspace)…"
            value={term}
            onChange={(e) => setTerm(e.currentTarget.value)}
            leftSection={<IconSearch size={14} />}
            style={{ flex: 1 }}
            data-autofocus
          />
          <Select
            label="Country"
            data={[
              { value: "us", label: "US" },
              { value: "gb", label: "GB" },
              { value: "de", label: "DE" },
              { value: "fr", label: "FR" },
              { value: "es", label: "ES" },
              { value: "jp", label: "JP" },
              { value: "kr", label: "KR" },
              { value: "cn", label: "CN" },
              { value: "ru", label: "RU" },
              { value: "br", label: "BR" },
            ]}
            value={country}
            onChange={(v) => setCountry(v ?? "us")}
            style={{ width: 90 }}
            allowDeselect={false}
          />
        </Group>

        <Paper withBorder radius="sm" p={0} mih={260}>
          {trimmedTerm.length < 2 ? (
            <Stack align="center" justify="center" mih={260} c="dimmed" gap={4}>
              <IconSearch size={20} />
              <Text size="sm">Type at least 2 characters to search</Text>
            </Stack>
          ) : isSearching && results.length === 0 ? (
            <Stack align="center" justify="center" mih={260} gap={4}>
              <Skeleton h={36} w="90%" />
              <Skeleton h={36} w="90%" />
              <Skeleton h={36} w="90%" />
            </Stack>
          ) : results.length === 0 ? (
            <Stack align="center" justify="center" mih={260} c="dimmed">
              <Text size="sm">No apps found for "{trimmedTerm}"</Text>
            </Stack>
          ) : (
            <Stack gap={0}>
              {results.map((r) => {
                const alreadyAdded =
                  existingAscIds.has(r.app_id) || justAdded.has(r.app_id);
                const isPending =
                  add.isPending && add.variables?.asc_app_id === r.app_id;
                return (
                  <Group
                    key={r.app_id}
                    gap="sm"
                    wrap="nowrap"
                    p="xs"
                    style={{
                      borderBottom:
                        "1px solid var(--mantine-color-gray-2)",
                      cursor: alreadyAdded ? "default" : "pointer",
                      opacity: alreadyAdded ? 0.55 : 1,
                      background:
                        isPending
                          ? "var(--mantine-color-blue-0)"
                          : undefined,
                    }}
                    onClick={() => !alreadyAdded && handlePick(r)}
                  >
                    <Image
                      src={r.icon_url}
                      w={40}
                      h={40}
                      radius="sm"
                      fallbackSrc="https://placehold.co/40x40?text=?"
                    />
                    <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                      <Text size="sm" fw={500} truncate>
                        {r.name}
                      </Text>
                      <Group gap={6} wrap="nowrap">
                        <Text size="xs" c="dimmed" truncate>
                          {r.bundle_id || "—"}
                        </Text>
                        <Text size="xs" c="dimmed">
                          ·
                        </Text>
                        <Text size="xs" c="dimmed">
                          ID {r.app_id}
                        </Text>
                      </Group>
                    </Stack>
                    {alreadyAdded ? (
                      <Badge
                        color="green"
                        variant="light"
                        leftSection={<IconPlus size={10} />}
                      >
                        Added
                      </Badge>
                    ) : (
                      <Button
                        size="xs"
                        variant="light"
                        leftSection={<IconPlus size={12} />}
                        loading={isPending}
                        onClick={(e) => {
                          e.stopPropagation();
                          handlePick(r);
                        }}
                      >
                        Add
                      </Button>
                    )}
                  </Group>
                );
              })}
            </Stack>
          )}
        </Paper>

        <Group justify="space-between">
          <Button
            variant="subtle"
            size="xs"
            leftSection={
              showManual ? (
                <IconChevronDown size={12} />
              ) : (
                <IconChevronRight size={12} />
              )
            }
            onClick={() => setShowManual((v) => !v)}
          >
            Enter ID manually
          </Button>
          <Button variant="default" onClick={onClose}>
            Done
          </Button>
        </Group>

        {showManual && (
          <Paper withBorder p="sm" radius="sm">
            <Stack gap="xs">
              <TextInput
                label="App name"
                size="xs"
                value={manualName}
                onChange={(e) => setManualName(e.currentTarget.value)}
              />
              <TextInput
                label="iTunes app ID"
                size="xs"
                placeholder="e.g., 123456789"
                value={manualAscId}
                onChange={(e) => setManualAscId(e.currentTarget.value)}
              />
              <TextInput
                label="Bundle ID (optional)"
                size="xs"
                placeholder="e.g., com.example.app"
                value={manualBundleId}
                onChange={(e) => setManualBundleId(e.currentTarget.value)}
              />
              <Group justify="flex-end">
                <Button
                  size="xs"
                  onClick={handleManualAdd}
                  loading={add.isPending}
                  disabled={!manualName.trim() || !manualAscId.trim()}
                >
                  Add
                </Button>
              </Group>
            </Stack>
          </Paper>
        )}
      </Stack>
    </Modal>
  );
}

// ---- Main Page ----

export default function KeywordsPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ?? "";
  const { data: app, isLoading } = useApp(appId);

  return (
    <Container size="xl">
      {isLoading ? (
        <Stack gap="sm" mb="lg">
          <Skeleton height={32} width={300} />
          <Skeleton height={16} width={200} />
        </Stack>
      ) : (
        <div style={{ marginBottom: "var(--mantine-spacing-lg)" }}>
          <Title order={2}>{app?.name ?? "App"} - Keyword Analysis</Title>
          <Text c="dimmed" size="sm" mt={4}>
            Analyze and optimize keywords for discoverability.
          </Text>
        </div>
      )}

      <Tabs defaultValue="tracked">
        <Tabs.List>
          <Tabs.Tab value="tracked" leftSection={<IconTarget size={16} />}>
            Tracked Keywords
          </Tabs.Tab>
          <Tabs.Tab value="search" leftSection={<IconSearch size={16} />}>
            Search
          </Tabs.Tab>
          <Tabs.Tab
            value="cross-localization"
            leftSection={<IconLanguage size={16} />}
          >
            Cross-Localization
          </Tabs.Tab>
          <Tabs.Tab value="competitors" leftSection={<IconUsers size={16} />}>
            Competitors
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="tracked" pt="md">
          <TrackedKeywordsTab appId={appId} />
        </Tabs.Panel>

        <Tabs.Panel value="search" pt="md">
          <SearchTab appId={appId} />
        </Tabs.Panel>

        <Tabs.Panel value="cross-localization" pt="md">
          <CrossLocalizationTab />
        </Tabs.Panel>

        <Tabs.Panel value="competitors" pt="md">
          <CompetitorsTab appId={appId} />
        </Tabs.Panel>
      </Tabs>
    </Container>
  );
}
