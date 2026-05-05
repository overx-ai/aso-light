import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { IconDeviceFloppy, IconLanguage, IconX } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import {
  useUpdateLocale,
  useCreateLocale,
  useTranslateMetadata,
  useKeywordCoverage,
} from "@/lib/hooks";
import type {
  AppMetadataLocalization,
  AppMetadataSnapshot,
  LocaleUpsertIn,
  MetadataKind,
  TranslateSuggestionItem,
} from "@/types";
import {
  fieldsForKind,
  type FieldConfig,
  type FieldKey,
} from "@/components/metadata/fieldConfig";
import { localeWithCode } from "@/components/metadata/localeLabel";
import CharLimitCounter from "@/components/metadata/CharLimitCounter";
import KeywordCoverageBadge from "@/components/metadata/KeywordCoverageBadge";

interface LocaleEditorProps {
  appId: number;
  snapshot: AppMetadataSnapshot;
  selectedLocale: string | null;
  onSelectLocale: (locale: string | null) => void;
}

interface LocaleGroup {
  appInfo: AppMetadataLocalization | undefined;
  version: AppMetadataLocalization | undefined;
}

function groupByLocale(
  snapshot: AppMetadataSnapshot,
): Map<string, LocaleGroup> {
  const map = new Map<string, LocaleGroup>();
  for (const row of snapshot.app_info) {
    const g = map.get(row.locale) ?? { appInfo: undefined, version: undefined };
    g.appInfo = row;
    map.set(row.locale, g);
  }
  for (const row of snapshot.versions) {
    const g = map.get(row.locale) ?? { appInfo: undefined, version: undefined };
    g.version = row;
    map.set(row.locale, g);
  }
  return map;
}

function rowValue(row: AppMetadataLocalization | undefined, key: FieldKey): string {
  if (!row) return "";
  const raw = (row as unknown as Record<string, string | null>)[key];
  return raw ?? "";
}

interface FieldEditorProps {
  appId: number;
  field: FieldConfig;
  locale: string;
  row: AppMetadataLocalization | undefined;
  draft: string;
  setDraft: (value: string) => void;
  editable: boolean;
  allLocales: string[];
}

function FieldEditor({
  appId,
  field,
  locale,
  row,
  draft,
  setDraft,
  editable,
  allLocales,
}: FieldEditorProps) {
  const [translateOpen, setTranslateOpen] = useState(false);
  const [sourceLocale, setSourceLocale] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<TranslateSuggestionItem[]>([]);
  const translateMutation = useTranslateMetadata(appId);
  const createMutation = useCreateLocale(appId);
  const updateMutation = useUpdateLocale(appId);

  const original = rowValue(row, field.key);
  const dirty = draft !== original;

  const onSave = () => {
    const body: LocaleUpsertIn = { [field.key]: draft } as LocaleUpsertIn;
    if (row) {
      updateMutation.mutate({ kind: field.kind, locale, body });
    } else {
      createMutation.mutate({ kind: field.kind, locale, body });
    }
  };

  const onTranslate = () => {
    if (!sourceLocale) {
      notifications.show({
        title: "Pick a source locale",
        message: "Choose a locale to translate from.",
        color: "yellow",
      });
      return;
    }
    translateMutation.mutate(
      {
        source_locale: sourceLocale,
        target_locales: [locale],
        fields: [field.key],
      },
      {
        onSuccess: (out) => setSuggestions(out.items),
      },
    );
  };

  const sourceOptions = useMemo(
    () =>
      allLocales
        .filter((l) => l !== locale)
        .map((l) => ({ value: l, label: localeWithCode(l) })),
    [allLocales, locale],
  );

  const isSaving = updateMutation.isPending || createMutation.isPending;
  // Block save when the draft exceeds the field's char limit — the server
  // would 422 anyway, this just gives instant feedback.
  const overLimit =
    field.charLimit != null && draft.length > field.charLimit;

  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Group justify="space-between" align="center">
          <Group gap="xs">
            <Text fw={600} size="sm">
              {field.label}
            </Text>
            {!editable && (
              <Badge size="xs" color="gray" variant="light">
                Read-only
              </Badge>
            )}
          </Group>
          <Group gap="xs">
            <CharLimitCounter value={draft} limit={field.charLimit} />
            <Tooltip label="Translate from another locale" withArrow>
              <ActionIcon
                variant="subtle"
                onClick={() => setTranslateOpen((v) => !v)}
                disabled={!editable}
              >
                <IconLanguage size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>

        {field.multiline ? (
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.currentTarget.value)}
            autosize
            minRows={3}
            maxRows={12}
            disabled={!editable}
            placeholder={field.label}
          />
        ) : (
          <TextInput
            value={draft}
            onChange={(e) => setDraft(e.currentTarget.value)}
            disabled={!editable}
            placeholder={field.label}
          />
        )}

        {translateOpen && (
          <Paper withBorder p="xs">
            <Stack gap="xs">
              <Group gap="xs">
                <Select
                  size="xs"
                  placeholder="Source locale"
                  data={sourceOptions}
                  value={sourceLocale}
                  onChange={setSourceLocale}
                  searchable
                  style={{ flex: 1 }}
                />
                <Button
                  size="xs"
                  variant="light"
                  onClick={onTranslate}
                  loading={translateMutation.isPending}
                  disabled={!sourceLocale}
                >
                  Suggest
                </Button>
                <ActionIcon
                  variant="subtle"
                  onClick={() => {
                    setTranslateOpen(false);
                    setSuggestions([]);
                  }}
                >
                  <IconX size={14} />
                </ActionIcon>
              </Group>
              {suggestions.length > 0 && (
                <Stack gap={4}>
                  <Text size="xs" c="dimmed">
                    Click a suggestion to fill the field (does not auto-save):
                  </Text>
                  {suggestions
                    .filter((s) => s.field === field.key && s.locale === locale)
                    .map((s) => (
                      <Badge
                        key={s.suggestion}
                        variant="light"
                        color={s.cached ? "gray" : "blue"}
                        style={{ cursor: "pointer", maxWidth: "100%" }}
                        onClick={() => setDraft(s.suggestion)}
                      >
                        {s.suggestion}
                      </Badge>
                    ))}
                </Stack>
              )}
            </Stack>
          </Paper>
        )}

        <Group justify="flex-end">
          <Button
            size="xs"
            leftSection={<IconDeviceFloppy size={14} />}
            onClick={onSave}
            disabled={!editable || !dirty || overLimit}
            loading={isSaving}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

export default function LocaleEditor({
  appId,
  snapshot,
  selectedLocale,
  onSelectLocale,
}: LocaleEditorProps) {
  const grouped = useMemo(() => groupByLocale(snapshot), [snapshot]);

  const allLocales = useMemo(
    () => Array.from(grouped.keys()).sort(),
    [grouped],
  );

  // Default to first available locale if none selected.
  useEffect(() => {
    if (!selectedLocale && allLocales.length > 0) {
      onSelectLocale(allLocales[0]);
    }
  }, [selectedLocale, allLocales, onSelectLocale]);

  const editableFields = useMemo(
    () => new Set(snapshot.state.editable_fields),
    [snapshot.state.editable_fields],
  );

  // Per-field draft state, keyed by field name. Reset whenever the locale
  // changes or the snapshot is refreshed (synced_at bump triggers re-render).
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const localeGroup = selectedLocale ? grouped.get(selectedLocale) : undefined;

  useEffect(() => {
    if (!localeGroup) {
      setDrafts({});
      return;
    }
    const next: Record<string, string> = {};
    for (const cfg of [
      ...fieldsForKind("app_info"),
      ...fieldsForKind("version"),
    ]) {
      const row = cfg.kind === "app_info" ? localeGroup.appInfo : localeGroup.version;
      next[cfg.key] = rowValue(row, cfg.key);
    }
    setDrafts(next);
  }, [
    localeGroup?.appInfo?.synced_at,
    localeGroup?.version?.synced_at,
    selectedLocale,
    localeGroup,
  ]);

  const coverageQuery = useKeywordCoverage(appId);
  const coverageForLocale = useMemo(() => {
    if (!coverageQuery.data || !selectedLocale) return [];
    return coverageQuery.data.items.filter((i) => i.locale === selectedLocale);
  }, [coverageQuery.data, selectedLocale]);

  const localeOptions = useMemo(
    () => allLocales.map((l) => ({ value: l, label: localeWithCode(l) })),
    [allLocales],
  );

  const renderFields = (kind: MetadataKind) => {
    const row = kind === "app_info" ? localeGroup?.appInfo : localeGroup?.version;
    return (
      <Stack gap="sm">
        <Group justify="space-between">
          <Text fw={600} size="sm" c="dimmed" tt="uppercase">
            {kind === "app_info" ? "App Info" : "Version"}
          </Text>
          {kind === "version" && coverageForLocale.length > 0 && (
            <KeywordCoverageBadge items={coverageForLocale} />
          )}
        </Group>
        {fieldsForKind(kind).map((cfg) => (
          <FieldEditor
            key={cfg.key}
            appId={appId}
            field={cfg}
            locale={selectedLocale ?? ""}
            row={row}
            draft={drafts[cfg.key] ?? ""}
            setDraft={(v) => setDrafts((d) => ({ ...d, [cfg.key]: v }))}
            editable={editableFields.has(cfg.key)}
            allLocales={allLocales}
          />
        ))}
      </Stack>
    );
  };

  if (allLocales.length === 0) {
    return (
      <Paper withBorder p="lg">
        <Text c="dimmed">No locales found in the synced snapshot.</Text>
      </Paper>
    );
  }

  return (
    <Stack gap="md" mt="md">
      <Group justify="space-between" align="flex-end">
        <Select
          label="Locale"
          placeholder="Pick a locale"
          data={localeOptions}
          value={selectedLocale}
          onChange={onSelectLocale}
          searchable
          style={{ minWidth: 280 }}
        />
        {coverageQuery.isLoading && <Loader size="xs" />}
      </Group>

      {selectedLocale && (
        <Group align="flex-start" grow>
          {renderFields("app_info")}
          {renderFields("version")}
        </Group>
      )}
    </Stack>
  );
}
