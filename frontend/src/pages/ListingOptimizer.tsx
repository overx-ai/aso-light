import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Container,
  Group,
  Loader,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconDownload,
  IconLanguage,
  IconPlus,
  IconRefresh,
  IconSparkles,
  IconWand,
} from "@tabler/icons-react";
import {
  useAddKeyword,
  useApplyBulkMetadata,
  useExportListing,
  useListingBrainstorm,
  useListingGaps,
  useLocalizeListing,
  usePreviewBulkMetadata,
  useProposeListing,
  useRefreshListingRankings,
  useRunListingBrainstorm,
} from "@/lib/hooks";
import type {
  BrainstormOut,
  BrainstormTerm,
  BulkPreviewItem,
  GapCell,
  GapPlacement,
  ListingBulkPreview,
  ScoredProposal,
} from "@/types";

const DEFAULT_STOREFRONT = "US";

const PLACEMENT_COLOR: Record<GapPlacement, string> = {
  title: "green",
  subtitle: "teal",
  keywords: "blue",
  none: "gray",
  missing_localized: "red",
};

const PLACEMENT_LABEL: Record<GapPlacement, string> = {
  title: "Title",
  subtitle: "Subtitle",
  keywords: "Keywords",
  none: "Absent",
  missing_localized: "Missing localized",
};

function gapKey(keyword: string, locale: string): string {
  return `${keyword}\u0000${locale}`;
}

function previewStatus(item: BulkPreviewItem | undefined): string | null | undefined {
  if (!item) return "";
  return item.would_skip ? item.reason : item.action;
}

function TermTable({
  terms,
  onTrack,
}: {
  terms: BrainstormTerm[];
  onTrack?: (term: string) => void;
}) {
  if (terms.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No terms.
      </Text>
    );
  }
  return (
    <Table striped fz="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Term</Table.Th>
          <Table.Th>Popularity</Table.Th>
          <Table.Th>Difficulty</Table.Th>
          <Table.Th>Value</Table.Th>
          {onTrack && <Table.Th />}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {terms.map((t) => (
          <Table.Tr key={t.term}>
            <Table.Td>{t.term}</Table.Td>
            <Table.Td>
              {t.popularity}
              {t.popularity_source && (
                <Text span size="xs" c="dimmed" ml={4}>
                  {t.popularity_source}
                </Text>
              )}
            </Table.Td>
            <Table.Td>{t.difficulty}</Table.Td>
            <Table.Td>{t.value.toFixed(1)}</Table.Td>
            {onTrack && (
              <Table.Td>
                <Button
                  size="compact-xs"
                  variant="light"
                  leftSection={<IconPlus size={12} />}
                  onClick={() => onTrack(t.term)}
                >
                  Track
                </Button>
              </Table.Td>
            )}
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function GapBoard({ cells, locales, keywords }: {
  cells: GapCell[];
  locales: string[];
  keywords: string[];
}) {
  const byKey = useMemo(() => {
    const map = new Map<string, GapCell>();
    for (const cell of cells) map.set(gapKey(cell.keyword, cell.locale), cell);
    return map;
  }, [cells]);

  if (keywords.length === 0 || locales.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Track keywords and sync metadata to see the market grid.
      </Text>
    );
  }
  return (
    <ScrollArea>
      <Table fz="xs" withColumnBorders>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Keyword</Table.Th>
            {locales.map((locale) => (
              <Table.Th key={locale}>{locale}</Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {keywords.map((keyword) => (
            <Table.Tr key={keyword}>
              <Table.Td>{keyword}</Table.Td>
              {locales.map((locale) => {
                const cell = byKey.get(gapKey(keyword, locale));
                if (!cell) return <Table.Td key={locale} />;
                return (
                  <Table.Td key={locale}>
                    <Tooltip
                      label={`${cell.storefront} rank: ${cell.rank ?? "unranked"}${
                        cell.variant ? ` / variant: ${cell.variant}` : ""
                      }`}
                    >
                      <Badge color={PLACEMENT_COLOR[cell.placement]} variant="light" size="sm">
                        {PLACEMENT_LABEL[cell.placement]}
                        {cell.rank !== null ? ` #${cell.rank}` : ""}
                      </Badge>
                    </Tooltip>
                  </Table.Td>
                );
              })}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}

function ProposalCard({
  proposal,
  label,
  selected,
  onSelect,
}: {
  proposal: ScoredProposal;
  label: string;
  selected?: boolean;
  onSelect?: () => void;
}) {
  return (
    <Card withBorder padding="sm" style={selected ? { borderColor: "var(--mantine-color-blue-6)" } : undefined}>
      <Stack gap={4}>
        <Group justify="space-between">
          <Text fw={600} size="sm">
            {label}
          </Text>
          <Badge variant="light" color={proposal.delta_vs_current > 0 ? "green" : "gray"}>
            {proposal.delta_vs_current >= 0 ? "+" : ""}
            {proposal.delta_vs_current} popularity
          </Badge>
        </Group>
        <Text size="sm">{proposal.name}</Text>
        <Text size="sm" c="dimmed">
          {proposal.subtitle}
        </Text>
        <Code block>{proposal.keywords}</Code>
        <Text size="xs" c="dimmed">
          Covers {proposal.covered_popularity} popularity
        </Text>
        {onSelect && (
          <Button size="xs" variant={selected ? "filled" : "light"} onClick={onSelect}>
            {selected ? "Selected" : "Use this"}
          </Button>
        )}
      </Stack>
    </Card>
  );
}

function BrainstormResult({
  result,
  storefront,
  onTrack,
}: {
  result: BrainstormOut | null;
  storefront: string;
  onTrack: (term: string, locale: string) => void;
}) {
  if (!result) {
    return (
      <Text size="sm" c="dimmed">
        No brainstorm for {storefront} yet. It runs automatically after an app is added.
      </Text>
    );
  }
  return (
    <SimpleGrid cols={{ base: 1, md: 2 }}>
      <div>
        <Text fw={600} size="sm" mb="xs">
          Using ({result.using.length})
        </Text>
        <TermTable terms={result.using} />
      </div>
      <div>
        <Text fw={600} size="sm" mb="xs">
          Missing ({result.missing.length})
        </Text>
        <TermTable
          terms={result.missing}
          onTrack={(term) => onTrack(term, result.source_locale)}
        />
      </div>
    </SimpleGrid>
  );
}

function FieldPreview({ appId, payload }: { appId: number; payload: ListingBulkPreview }) {
  const preview = usePreviewBulkMetadata(appId);
  const apply = useApplyBulkMetadata(appId);
  const [items, setItems] = useState<BulkPreviewItem[] | null>(null);
  const body = { ...payload, value: null };

  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" mb="xs">
        <Text fw={600} size="sm">
          {payload.field} ({payload.target_locales.length} locales)
        </Text>
        <Group gap="xs">
          <Button
            size="xs"
            variant="light"
            loading={preview.isPending}
            onClick={() => preview.mutate(body, { onSuccess: (data) => setItems(data.items) })}
          >
            Preview diff
          </Button>
          <Button
            size="xs"
            color="orange"
            disabled={!items}
            loading={apply.isPending}
            onClick={() => apply.mutate(body)}
          >
            Apply to App Store Connect
          </Button>
        </Group>
      </Group>
      <Table fz="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Locale</Table.Th>
            <Table.Th>Current</Table.Th>
            <Table.Th>Proposed</Table.Th>
            <Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {payload.target_locales.map((locale) => {
            const item = items?.find((i) => i.locale === locale);
            return (
              <Table.Tr key={locale}>
                <Table.Td>{locale}</Table.Td>
                <Table.Td>{item?.current_value ?? ""}</Table.Td>
                <Table.Td>{payload.values_by_locale[locale]}</Table.Td>
                <Table.Td>{previewStatus(item)}</Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Card>
  );
}

export default function ListingOptimizerPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ? Number(id) : 0;
  const [storefrontInput, setStorefrontInput] = useState(DEFAULT_STOREFRONT);
  const storefront = storefrontInput.trim().toUpperCase();
  const [selected, setSelected] = useState<number | null>(null);

  const brainstorm = useListingBrainstorm(appId, storefront);
  const runBrainstorm = useRunListingBrainstorm(appId);
  const gaps = useListingGaps(appId);
  const refreshRankings = useRefreshListingRankings(appId);
  const propose = useProposeListing(appId);
  const localize = useLocalizeListing(appId);
  const exportListing = useExportListing(appId);
  const addKeyword = useAddKeyword();

  const result = brainstorm.data ?? null;
  const proposals = propose.data?.proposals ?? [];
  const chosen = selected !== null ? proposals[selected] : null;

  return (
    <Container size="xl">
      <Group justify="space-between" mb="lg" align="flex-end">
        <div>
          <Group gap="sm">
            <IconSparkles size={22} />
            <Title order={2}>Listing Optimizer</Title>
          </Group>
          <Text c="dimmed" size="sm" mt={4}>
            Keywords you use and miss, market gaps, AI listing proposals and one-click
            localization. Suggestions only: nothing reaches App Store Connect until you apply.
          </Text>
        </div>
        <Group gap="xs" align="flex-end">
          <TextInput
            label="Main storefront"
            value={storefrontInput}
            onChange={(e) => setStorefrontInput(e.currentTarget.value)}
            maxLength={3}
            w={120}
          />
          <Button
            variant="default"
            leftSection={<IconDownload size={16} />}
            loading={exportListing.isPending && exportListing.variables === "current"}
            onClick={() => exportListing.mutate("current")}
          >
            Export current
          </Button>
          <Button
            variant="default"
            leftSection={<IconDownload size={16} />}
            disabled={!localize.data}
            loading={exportListing.isPending && exportListing.variables === "proposal"}
            onClick={() => exportListing.mutate("proposal")}
          >
            Export proposal
          </Button>
        </Group>
      </Group>

      <Stack gap="md">
        <Card withBorder>
          <Group justify="space-between" mb="sm">
            <Title order={4}>Brainstorm</Title>
            <Button
              size="xs"
              leftSection={<IconWand size={14} />}
              loading={runBrainstorm.isPending}
              onClick={() => runBrainstorm.mutate(storefront)}
            >
              {result ? "Re-run brainstorm" : "Run brainstorm"}
            </Button>
          </Group>
          {brainstorm.isLoading ? (
            <Loader size="sm" />
          ) : brainstorm.error ? (
            <Alert color="red" icon={<IconAlertCircle size={16} />}>
              Could not load the brainstorm for {storefront}.
            </Alert>
          ) : (
            <BrainstormResult
              result={result}
              storefront={storefront}
              onTrack={(term, locale) =>
                addKeyword.mutate({ appId: String(appId), text: term, locale })
              }
            />
          )}
        </Card>

        <Card withBorder>
          <Group justify="space-between" mb="sm">
            <Title order={4}>Market gaps</Title>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              loading={refreshRankings.isPending}
              onClick={() => refreshRankings.mutate()}
            >
              Refresh rankings in live markets
            </Button>
          </Group>
          {gaps.isLoading ? (
            <Loader size="sm" />
          ) : gaps.error ? (
            <Alert color="red" icon={<IconAlertCircle size={16} />}>
              Could not load the market grid.
            </Alert>
          ) : gaps.data ? (
            <GapBoard {...gaps.data} />
          ) : null}
        </Card>

        <Card withBorder>
          <Group justify="space-between" mb="sm">
            <Title order={4}>Proposals for {storefront}</Title>
            <Button
              size="xs"
              leftSection={<IconSparkles size={14} />}
              disabled={!result}
              loading={propose.isPending}
              onClick={() => {
                setSelected(null);
                propose.mutate(storefront);
              }}
            >
              Generate proposals
            </Button>
          </Group>
          {propose.data ? (
            <SimpleGrid cols={{ base: 1, md: 4 }}>
              <ProposalCard proposal={propose.data.current} label="Current" />
              {proposals.map((proposal, index) => (
                <ProposalCard
                  key={`${proposal.name}-${index}`}
                  proposal={proposal}
                  label={`Proposal ${index + 1}`}
                  selected={selected === index}
                  onSelect={() => setSelected(index)}
                />
              ))}
            </SimpleGrid>
          ) : (
            <Text size="sm" c="dimmed">
              Proposals use the brainstorm and count against the monthly AI cap.
            </Text>
          )}
          {propose.data && proposals.length === 0 && (
            <Text size="sm" c="dimmed" mt="sm">
              No proposal passed the char limits and no-repeat rule. Try again.
            </Text>
          )}
        </Card>

        <Card withBorder>
          <Group justify="space-between" mb="sm">
            <Title order={4}>Localize</Title>
            <Button
              size="xs"
              leftSection={<IconLanguage size={14} />}
              disabled={!chosen}
              loading={localize.isPending}
              onClick={() => chosen && localize.mutate({ proposal: chosen, storefront })}
            >
              Localize into all live locales
            </Button>
          </Group>
          {!localize.data ? (
            <Text size="sm" c="dimmed">
              Pick a proposal, then localize it. Each field is previewed before anything is applied.
            </Text>
          ) : (
            <Stack gap="sm">
              {localize.data.locked_fields.length > 0 && (
                <Alert color="yellow" icon={<IconAlertCircle size={16} />}>
                  Not editable in the current version state: {localize.data.locked_fields.join(", ")}
                </Alert>
              )}
              {localize.data.warnings.map((warning) => (
                <Text key={warning} size="xs" c="orange">
                  {warning}
                </Text>
              ))}
              {localize.data.bulk_previews.map((payload) => (
                <FieldPreview
                  key={`${payload.field}-${localize.submittedAt}`}
                  appId={appId}
                  payload={payload}
                />
              ))}
            </Stack>
          )}
        </Card>
      </Stack>
    </Container>
  );
}
