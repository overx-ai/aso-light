import { useState, useCallback } from "react";
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
} from "@/lib/hooks";
import RankHistoryChart from "@/components/keywords/RankHistoryChart";
import CrossLocalizationMatrix from "@/components/keywords/CrossLocalizationMatrix";
import type {
  KeywordTrackingResponse,
  KeywordSearchResult,
  CompetitorApp,
  CompetitorKeywordResult,
} from "@/types";

// ---- Tracked Keywords Tab ----

function TrackedKeywordsTab({ appId }: { appId: string }) {
  const { data: trackings, isLoading } = useTrackedKeywords(appId);
  const addKeywordMutation = useAddKeyword();
  const removeKeywordMutation = useRemoveKeyword();
  const refreshMutation = useRefreshKeywordRankings();
  const [addModalOpened, addModalHandlers] = useDisclosure(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

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
  const addMutation = useAddCompetitor();
  const removeMutation = useRemoveCompetitor();
  const checkKeywordsMutation = useCompetitorKeywords();
  const [addModalOpened, addModalHandlers] = useDisclosure(false);
  const [keywordsResults, setKeywordsResults] = useState<{
    competitorId: number;
    results: CompetitorKeywordResult[];
  } | null>(null);

  // Form state
  const [newName, setNewName] = useState("");
  const [newAscId, setNewAscId] = useState("");
  const [newBundleId, setNewBundleId] = useState("");

  const handleAdd = useCallback(() => {
    if (!newAscId.trim() || !newName.trim()) return;
    addMutation.mutate(
      {
        appId,
        asc_app_id: newAscId.trim(),
        name: newName.trim(),
        bundle_id: newBundleId.trim() || undefined,
      },
      {
        onSuccess: () => {
          addModalHandlers.close();
          setNewName("");
          setNewAscId("");
          setNewBundleId("");
        },
      },
    );
  }, [addMutation, appId, newAscId, newName, newBundleId, addModalHandlers]);

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

      <Modal
        opened={addModalOpened}
        onClose={addModalHandlers.close}
        title="Add Competitor"
        size="sm"
      >
        <Stack gap="md">
          <TextInput
            label="App Name"
            placeholder="Competitor app name"
            value={newName}
            onChange={(e) => setNewName(e.currentTarget.value)}
            required
          />
          <TextInput
            label="iTunes App ID"
            placeholder="e.g., 123456789"
            value={newAscId}
            onChange={(e) => setNewAscId(e.currentTarget.value)}
            required
          />
          <TextInput
            label="Bundle ID (optional)"
            placeholder="e.g., com.example.app"
            value={newBundleId}
            onChange={(e) => setNewBundleId(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={addModalHandlers.close}>
              Cancel
            </Button>
            <Button
              onClick={handleAdd}
              loading={addMutation.isPending}
              disabled={!newName.trim() || !newAscId.trim()}
            >
              Add
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
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
