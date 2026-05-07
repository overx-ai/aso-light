import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Modal,
  MultiSelect,
  Progress,
  ScrollArea,
  Select,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import {
  IconAlertCircle,
  IconCheck,
  IconLanguage,
  IconPlayerPlay,
  IconX,
} from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import api from "@/lib/api";
import { queryKeys, useTranslateMetadata } from "@/lib/hooks";
import type {
  AppMetadataLocalization,
  AppMetadataSnapshot,
  LocaleUpsertIn,
  TranslateOut,
} from "@/types";
import { localeWithCode } from "@/components/metadata/localeLabel";

const SOURCE_LOCALE_STORAGE_KEY = "metadata-source-locale";
const MAX_TARGETS = 30;
const TRANSLATABLE_FIELDS = [
  { key: "name", label: "Name" },
  { key: "subtitle", label: "Subtitle" },
  { key: "description", label: "Description" },
  { key: "keywords", label: "Keywords" },
  { key: "promotional_text", label: "Promotional text" },
  { key: "whats_new", label: "What's new" },
] as const;
type TranslatableFieldKey = (typeof TRANSLATABLE_FIELDS)[number]["key"];

const APP_INFO_TEXT_FIELDS: TranslatableFieldKey[] = ["name", "subtitle"];
const VERSION_TEXT_FIELDS: TranslatableFieldKey[] = [
  "description",
  "keywords",
  "promotional_text",
  "whats_new",
];

type SideStatus = "pending" | "running" | "done" | "skipped" | "failed";

interface RowResult {
  locale: string;
  appInfo: SideStatus;
  version: SideStatus;
  error?: string;
}

interface Props {
  appId: number;
  opened: boolean;
  onClose: () => void;
  snapshot: AppMetadataSnapshot;
  indexedLocales: string[];
}

/**
 * Locales that have *any* metadata filled (app_info or version row exists).
 * Mirrors collectLocalesWithMetadata in CrossLocalizationPage.tsx — kept local
 * to avoid a circular import; both pieces are tiny.
 */
function localesWithMetadata(snapshot: AppMetadataSnapshot): Set<string> {
  const out = new Set<string>();
  for (const r of snapshot.app_info) out.add(r.locale);
  for (const r of snapshot.versions) out.add(r.locale);
  return out;
}

function rowFor(
  rows: AppMetadataLocalization[],
  locale: string,
): AppMetadataLocalization | undefined {
  return rows.find((r) => r.locale === locale);
}

export default function FixMissingLocalesModal({
  appId,
  opened,
  onClose,
  snapshot,
  indexedLocales,
}: Props) {
  const queryClient = useQueryClient();
  const translateMutation = useTranslateMetadata(appId);

  const [step, setStep] = useState<"configure" | "running" | "done">("configure");
  const [sourceLocale, setSourceLocale] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(SOURCE_LOCALE_STORAGE_KEY);
  });
  const [targets, setTargets] = useState<string[]>([]);
  const [selectedFields, setSelectedFields] = useState<string[]>(
    TRANSLATABLE_FIELDS.map((f) => f.key),
  );
  const [copyUrls, setCopyUrls] = useState(true);
  const [results, setResults] = useState<RowResult[]>([]);

  const missingLocales = useMemo(() => {
    const filled = localesWithMetadata(snapshot);
    return indexedLocales.filter((l) => !filled.has(l)).sort();
  }, [indexedLocales, snapshot]);

  const sourceOptions = useMemo(() => {
    const filled = Array.from(localesWithMetadata(snapshot)).sort();
    return filled.map((l) => ({ value: l, label: localeWithCode(l) }));
  }, [snapshot]);

  const targetOptions = useMemo(
    () =>
      missingLocales.map((l) => ({ value: l, label: localeWithCode(l) })),
    [missingLocales],
  );

  // Auto-pre-select all missing on open / when missing list changes.
  useEffect(() => {
    if (opened && step === "configure") {
      setTargets(missingLocales.slice(0, MAX_TARGETS));
    }
  }, [opened, step, missingLocales]);

  // Reset to configure step on close (preserves source locale + field choices).
  useEffect(() => {
    if (!opened) {
      setStep("configure");
      setResults([]);
    }
  }, [opened]);

  // Persist source locale choice (same key as LocaleEditor).
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (sourceLocale) {
      window.localStorage.setItem(SOURCE_LOCALE_STORAGE_KEY, sourceLocale);
    }
  }, [sourceLocale]);

  const sourceAppInfoRow = sourceLocale
    ? rowFor(snapshot.app_info, sourceLocale)
    : undefined;
  const sourceVersionRow = sourceLocale
    ? rowFor(snapshot.versions, sourceLocale)
    : undefined;

  const canCreateAppInfo = snapshot.state.app_info_id != null;
  const canCreateVersion = snapshot.state.editable_version_id != null;

  const overCap = targets.length > MAX_TARGETS;
  const filteredTargets = targets.filter((l) => l !== sourceLocale);

  const runDisabled =
    !sourceLocale ||
    filteredTargets.length === 0 ||
    selectedFields.length === 0 ||
    overCap ||
    (!canCreateAppInfo && !canCreateVersion);

  const onRun = async () => {
    if (runDisabled || !sourceLocale) return;
    setStep("running");
    setResults(
      filteredTargets.map((l) => ({
        locale: l,
        appInfo: canCreateAppInfo ? "pending" : "skipped",
        version: canCreateVersion ? "pending" : "skipped",
      })),
    );

    const updateRow = (locale: string, patch: Partial<RowResult>) => {
      setResults((prev) =>
        prev.map((r) => (r.locale === locale ? { ...r, ...patch } : r)),
      );
    };

    let translateOut: TranslateOut;
    try {
      translateOut = await translateMutation.mutateAsync({
        source_locale: sourceLocale,
        target_locales: filteredTargets,
        fields: selectedFields,
      });
    } catch {
      // useTranslateMetadata already shows a toast on error.
      setStep("configure");
      return;
    }

    // Index suggestions by locale → field → text.
    const byLocale = new Map<string, Map<string, string>>();
    for (const item of translateOut.items) {
      let fields = byLocale.get(item.locale);
      if (!fields) {
        fields = new Map();
        byLocale.set(item.locale, fields);
      }
      fields.set(item.field, item.suggestion);
    }

    let appliedAppInfo = 0;
    let appliedVersion = 0;
    let failedCount = 0;

    const postSide = async (
      side: "appInfo" | "version",
      locale: string,
      url: string,
      body: LocaleUpsertIn,
    ): Promise<boolean> => {
      updateRow(locale, { [side]: "running" });
      try {
        await api.post(url, body);
        updateRow(locale, { [side]: "done" });
        return true;
      } catch (err) {
        failedCount += 1;
        updateRow(locale, { [side]: "failed", error: ascErr(err) });
        return false;
      }
    };

    for (const locale of filteredTargets) {
      const translated = byLocale.get(locale) ?? new Map<string, string>();

      const appInfoBody: LocaleUpsertIn = {};
      for (const f of APP_INFO_TEXT_FIELDS) {
        if (!selectedFields.includes(f)) continue;
        const v = translated.get(f);
        if (v) appInfoBody[f] = v;
      }
      if (copyUrls && sourceAppInfoRow?.privacy_policy_url) {
        appInfoBody.privacy_policy_url = sourceAppInfoRow.privacy_policy_url;
      }

      const versionBody: LocaleUpsertIn = {};
      for (const f of VERSION_TEXT_FIELDS) {
        if (!selectedFields.includes(f)) continue;
        const v = translated.get(f);
        if (v) versionBody[f] = v;
      }
      if (copyUrls && sourceVersionRow) {
        if (sourceVersionRow.marketing_url)
          versionBody.marketing_url = sourceVersionRow.marketing_url;
        if (sourceVersionRow.support_url)
          versionBody.support_url = sourceVersionRow.support_url;
      }

      if (canCreateAppInfo && Object.keys(appInfoBody).length > 0) {
        if (
          await postSide(
            "appInfo",
            locale,
            `/apps/${appId}/metadata/app_info/${locale}`,
            appInfoBody,
          )
        ) {
          appliedAppInfo += 1;
        }
      } else if (Object.keys(appInfoBody).length === 0) {
        updateRow(locale, { appInfo: "skipped" });
      }

      if (canCreateVersion && Object.keys(versionBody).length > 0) {
        if (
          await postSide(
            "version",
            locale,
            `/apps/${appId}/metadata/version/${locale}`,
            versionBody,
          )
        ) {
          appliedVersion += 1;
        }
      } else if (Object.keys(versionBody).length === 0) {
        updateRow(locale, { version: "skipped" });
      }
    }

    queryClient.invalidateQueries({ queryKey: queryKeys.appMetadata(appId) });
    queryClient.invalidateQueries({
      queryKey: queryKeys.keywordCoverage(appId),
    });

    setStep("done");
    notifications.show({
      title: "Fix missing locales",
      message: `Created ${appliedAppInfo} app_info + ${appliedVersion} version localizations${
        failedCount ? `, ${failedCount} failed` : ""
      }.`,
      color: failedCount ? "yellow" : "green",
    });
  };

  const isTerminal = (s: SideStatus) =>
    s === "done" || s === "failed" || s === "skipped";
  const progressDone = results.filter(
    (r) => isTerminal(r.appInfo) && isTerminal(r.version),
  ).length;
  const progressPct = results.length
    ? Math.round((progressDone / results.length) * 100)
    : 0;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={
        <Group gap="xs">
          <IconLanguage size={18} />
          <Text fw={600}>Fix missing locales</Text>
        </Group>
      }
      size="lg"
      closeOnClickOutside={step !== "running"}
      withCloseButton={step !== "running"}
    >
      {missingLocales.length === 0 ? (
        <Alert color="green" icon={<IconCheck size={16} />}>
          Every indexed locale already has metadata. Nothing to fix.
        </Alert>
      ) : step === "configure" ? (
        <Stack gap="sm">
          {!canCreateAppInfo && !canCreateVersion && (
            <Alert color="red" icon={<IconAlertCircle size={16} />}>
              Neither app_info nor an editable version is available. Re-sync
              metadata first.
            </Alert>
          )}
          {!canCreateVersion && canCreateAppInfo && (
            <Alert color="yellow" icon={<IconAlertCircle size={16} />}>
              No editable version found — only app_info localizations
              (Name / Subtitle / Privacy URL) will be created.
            </Alert>
          )}

          <Select
            label="Source locale"
            description="Used as the input for translation. Persisted across the app."
            placeholder="Pick a source locale"
            data={sourceOptions}
            value={sourceLocale}
            onChange={setSourceLocale}
            searchable
            required
          />

          <MultiSelect
            label={`Target locales (${filteredTargets.length}/${Math.min(missingLocales.length, MAX_TARGETS)})`}
            description={`Indexed locales without metadata. Capped at ${MAX_TARGETS} per run.`}
            data={targetOptions}
            value={targets}
            onChange={setTargets}
            searchable
            clearable
          />
          {overCap && (
            <Text size="xs" c="red">
              Trim selection to {MAX_TARGETS} or fewer to continue.
            </Text>
          )}

          <Checkbox.Group
            label="Fields to translate"
            value={selectedFields}
            onChange={setSelectedFields}
          >
            <Group gap="xs" mt={4}>
              {TRANSLATABLE_FIELDS.map((f) => (
                <Checkbox key={f.key} value={f.key} label={f.label} />
              ))}
            </Group>
          </Checkbox.Group>

          <Checkbox
            label="Copy URLs (privacy / marketing / support) from source verbatim"
            checked={copyUrls}
            onChange={(e) => setCopyUrls(e.currentTarget.checked)}
          />

          <Group justify="flex-end" mt="xs">
            <Button variant="subtle" onClick={onClose}>
              Cancel
            </Button>
            <Tooltip
              disabled={!runDisabled}
              label={runReason(
                sourceLocale,
                filteredTargets.length,
                selectedFields.length,
                overCap,
                canCreateAppInfo || canCreateVersion,
              )}
              withArrow
            >
              <Button
                leftSection={<IconPlayerPlay size={14} />}
                onClick={onRun}
                disabled={runDisabled}
              >
                Translate &amp; create ({filteredTargets.length})
              </Button>
            </Tooltip>
          </Group>
        </Stack>
      ) : (
        <Stack gap="sm">
          <Group justify="space-between">
            <Text size="sm" c="dimmed">
              {step === "running" ? "Running…" : "Done"} · {progressDone}/
              {results.length}
            </Text>
            <Progress
              value={progressPct}
              w={180}
              color={step === "done" ? "green" : "blue"}
            />
          </Group>
          <ScrollArea h={320}>
            <Stack gap={4}>
              {results.map((r) => (
                <Group
                  key={r.locale}
                  gap="xs"
                  wrap="nowrap"
                  justify="space-between"
                >
                  <Text size="xs" style={{ width: 140 }} truncate>
                    {localeWithCode(r.locale)}
                  </Text>
                  <Group gap={4} wrap="nowrap">
                    <StatusBadge label="app_info" status={r.appInfo} />
                    <StatusBadge label="version" status={r.version} />
                  </Group>
                  <Text
                    size="xs"
                    c="red"
                    style={{ flex: 1, minWidth: 0 }}
                    truncate
                  >
                    {r.error ?? ""}
                  </Text>
                </Group>
              ))}
            </Stack>
          </ScrollArea>
          {step === "done" && (
            <Group justify="flex-end">
              <Button onClick={onClose}>Close</Button>
            </Group>
          )}
        </Stack>
      )}
    </Modal>
  );
}

const STATUS_COLOR: Record<SideStatus, string> = {
  done: "green",
  failed: "red",
  running: "blue",
  skipped: "gray",
  pending: "gray",
};

function StatusBadge({ label, status }: { label: string; status: SideStatus }) {
  let icon: React.ReactNode = null;
  if (status === "done") icon = <IconCheck size={10} />;
  else if (status === "failed") icon = <IconX size={10} />;
  return (
    <Badge
      size="xs"
      color={STATUS_COLOR[status]}
      variant={status === "pending" ? "light" : "filled"}
      leftSection={icon}
      style={{ minWidth: 90 }}
    >
      {label}: {status}
    </Badge>
  );
}

function runReason(
  source: string | null,
  targetCount: number,
  fieldCount: number,
  overCap: boolean,
  hasParent: boolean,
): string {
  if (!hasParent) return "No editable parent — re-sync metadata first";
  if (!source) return "Pick a source locale";
  if (targetCount === 0) return "Pick at least one target locale";
  if (fieldCount === 0) return "Pick at least one field";
  if (overCap) return `Trim selection to ${MAX_TARGETS} or fewer`;
  return "Ready";
}

function ascErr(err: unknown): string {
  const e = err as {
    response?: { data?: { detail?: string | { msg?: string }[] } };
    message?: string;
  };
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return e?.message ?? "Request failed";
}
