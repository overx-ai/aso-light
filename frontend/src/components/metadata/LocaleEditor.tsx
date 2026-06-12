import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
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
import { IconDeviceFloppy, IconLanguage } from "@tabler/icons-react";
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
} from "@/types";
import {
  fieldsForKind,
  type FieldConfig,
  type FieldKey,
} from "@/components/metadata/fieldConfig";
import { localeWithCode } from "@/components/metadata/localeLabel";
import CharLimitCounter from "@/components/metadata/CharLimitCounter";
import KeywordCoverageBadge from "@/components/metadata/KeywordCoverageBadge";

const SOURCE_LOCALE_STORAGE_KEY = "metadata-source-locale";
const LABEL_WIDTH = 115;
const APP_INFO_COLUMN_WIDTH = 540;

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

interface FieldRowProps {
  appId: number;
  field: FieldConfig;
  locale: string;
  row: AppMetadataLocalization | undefined;
  draft: string;
  setDraft: (value: string) => void;
  editable: boolean;
  sourceLocale: string | null;
}

function FieldRow({
  appId,
  field,
  locale,
  row,
  draft,
  setDraft,
  editable,
  sourceLocale,
}: FieldRowProps) {
  const translateMutation = useTranslateMetadata(appId);
  const createMutation = useCreateLocale(appId);
  const updateMutation = useUpdateLocale(appId);

  const original = rowValue(row, field.key);
  const dirty = draft !== original;
  const isSaving = updateMutation.isPending || createMutation.isPending;
  const overLimit = field.charLimit != null && draft.length > field.charLimit;

  const onSave = () => {
    if (!editable || !dirty || overLimit) return;
    const body: LocaleUpsertIn = { [field.key]: draft } as LocaleUpsertIn;
    if (row) {
      updateMutation.mutate({ kind: field.kind, locale, body });
    } else {
      createMutation.mutate({ kind: field.kind, locale, body });
    }
  };

  const canTranslate =
    editable && sourceLocale != null && sourceLocale !== locale;

  const onTranslate = () => {
    if (!canTranslate || !sourceLocale) return;
    translateMutation.mutate(
      {
        source_locale: sourceLocale,
        target_locales: [locale],
        fields: [field.key],
      },
      {
        onSuccess: (out) => {
          const item = out.items.find(
            (s) => s.field === field.key && s.locale === locale,
          );
          if (!item) {
            notifications.show({
              title: "Nothing to translate",
              message: `Source field is empty in ${localeWithCode(sourceLocale)}.`,
              color: "yellow",
              autoClose: 2500,
            });
            return;
          }
          setDraft(item.suggestion);
        },
      },
    );
  };

  const translateTooltip = !editable
    ? "Field is read-only in this state"
    : !sourceLocale
      ? "Pick a source locale above"
      : sourceLocale === locale
        ? "Source equals target — switch source"
        : `Translate from ${localeWithCode(sourceLocale)}`;

  const saveTooltip = !editable
    ? "Read-only"
    : overLimit
      ? "Over character limit"
      : dirty
        ? "Save"
        : "No changes";

  const actions = (
    <Group gap={2} wrap="nowrap">
      <CharLimitCounter value={draft} limit={field.charLimit} />
      <Tooltip label={translateTooltip} withArrow openDelay={300}>
        <ActionIcon
          variant="subtle"
          size="sm"
          onClick={onTranslate}
          disabled={!canTranslate}
          loading={translateMutation.isPending}
          aria-label="Translate field"
        >
          <IconLanguage size={14} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label={saveTooltip} withArrow openDelay={300}>
        <ActionIcon
          variant={dirty && !overLimit && editable ? "filled" : "subtle"}
          color="blue"
          size="sm"
          onClick={onSave}
          disabled={!editable || !dirty || overLimit}
          loading={isSaving}
          aria-label="Save field"
        >
          <IconDeviceFloppy size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );

  if (field.multiline) {
    return (
      <Stack gap={4}>
        <Group justify="space-between" align="center" gap="xs">
          <Text fw={500} size="xs" c="dimmed">
            {field.label}
          </Text>
          {actions}
        </Group>
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.currentTarget.value)}
          autosize
          minRows={2}
          maxRows={10}
          disabled={!editable}
          placeholder={field.label}
          size="xs"
          aria-label={field.label}
        />
      </Stack>
    );
  }

  return (
    <Group gap="xs" wrap="nowrap" align="center">
      <Text
        fw={500}
        size="xs"
        c="dimmed"
        style={{ width: LABEL_WIDTH, flexShrink: 0 }}
      >
        {field.label}
      </Text>
      <TextInput
        value={draft}
        onChange={(e) => setDraft(e.currentTarget.value)}
        disabled={!editable}
        placeholder={field.label}
        size="xs"
        aria-label={field.label}
        style={{ flex: 1, minWidth: 0 }}
      />
      {actions}
    </Group>
  );
}

interface SectionProps {
  title: string;
  rightSlot?: React.ReactNode;
  children: React.ReactNode;
}

function Section({ title, rightSlot, children }: SectionProps) {
  return (
    <Stack gap={6}>
      <Group justify="space-between" align="center">
        <Text fw={600} size="xs" c="dimmed" tt="uppercase" lts={0.4}>
          {title}
        </Text>
        {rightSlot}
      </Group>
      <Paper withBorder p="xs">
        <Stack gap="xs">{children}</Stack>
      </Paper>
    </Stack>
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

  const [sourceLocale, setSourceLocale] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(SOURCE_LOCALE_STORAGE_KEY);
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (sourceLocale) {
      window.localStorage.setItem(SOURCE_LOCALE_STORAGE_KEY, sourceLocale);
    } else {
      window.localStorage.removeItem(SOURCE_LOCALE_STORAGE_KEY);
    }
  }, [sourceLocale]);

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
    // Dedupe by (keyword, placement) so the coverage badge dots never collide on
    // their React key if the API repeats a keyword for this locale.
    const seen = new Set<string>();
    return coverageQuery.data.items.filter((i) => {
      if (i.locale !== selectedLocale) return false;
      const key = `${i.keyword}-${i.placement}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [coverageQuery.data, selectedLocale]);

  const localeOptions = useMemo(
    () => allLocales.map((l) => ({ value: l, label: localeWithCode(l) })),
    [allLocales],
  );

  const renderRows = (kind: MetadataKind) => {
    const row = kind === "app_info" ? localeGroup?.appInfo : localeGroup?.version;
    return fieldsForKind(kind).map((cfg) => (
      <FieldRow
        key={cfg.key}
        appId={appId}
        field={cfg}
        locale={selectedLocale ?? ""}
        row={row}
        draft={drafts[cfg.key] ?? ""}
        setDraft={(v) => setDrafts((d) => ({ ...d, [cfg.key]: v }))}
        editable={editableFields.has(cfg.key)}
        sourceLocale={sourceLocale}
      />
    ));
  };

  if (allLocales.length === 0) {
    return (
      <Paper withBorder p="lg">
        <Text c="dimmed">No locales found in the synced snapshot.</Text>
      </Paper>
    );
  }

  return (
    <Stack gap="sm" mt="sm">
      <Group align="flex-end" gap="sm" wrap="wrap">
        <Select
          label="Locale"
          placeholder="Pick a locale"
          data={localeOptions}
          value={selectedLocale}
          onChange={onSelectLocale}
          searchable
          size="xs"
          style={{ minWidth: 220, flex: 1 }}
        />
        <Select
          label="Translate from"
          placeholder="Source locale"
          data={localeOptions}
          value={sourceLocale}
          onChange={setSourceLocale}
          searchable
          clearable
          size="xs"
          style={{ minWidth: 220, flex: 1 }}
        />
        {coverageQuery.isLoading && <Loader size="xs" />}
      </Group>

      {selectedLocale && (
        <Group align="flex-start" gap="sm" wrap="wrap">
          <div style={{ width: APP_INFO_COLUMN_WIDTH, flexShrink: 0 }}>
            <Section title="App Info">{renderRows("app_info")}</Section>
          </div>
          <div style={{ flex: 1, minWidth: 320 }}>
            <Section
              title="Version"
              rightSlot={
                coverageForLocale.length > 0 ? (
                  <KeywordCoverageBadge items={coverageForLocale} />
                ) : null
              }
            >
              {renderRows("version")}
            </Section>
          </div>
        </Group>
      )}
    </Stack>
  );
}
