import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Drawer,
  Group,
  MultiSelect,
  Select,
  Stack,
  Text,
  Textarea,
} from "@mantine/core";
import { DataTable } from "mantine-datatable";
import {
  IconAlertTriangle,
  IconEye,
  IconUpload,
} from "@tabler/icons-react";
import {
  useApplyBulkMetadata,
  usePreviewBulkMetadata,
} from "@/lib/hooks";
import type {
  AppMetadataLocalization,
  AppMetadataSnapshot,
  BulkPreviewItem,
  MetadataKind,
} from "@/types";
import {
  FIELDS_BY_KEY,
  fieldsForKind,
  type FieldKey,
} from "@/components/metadata/fieldConfig";
import { localeWithCode } from "@/components/metadata/localeLabel";
import MetadataValueDiff from "@/components/metadata/MetadataValueDiff";

interface BulkFanoutDrawerProps {
  appId: number;
  snapshot: AppMetadataSnapshot;
  opened: boolean;
  onClose: () => void;
}

const BULK_LOCALE_CAP = 50;

function localesForKind(
  snapshot: AppMetadataSnapshot,
  kind: MetadataKind,
): string[] {
  const rows: AppMetadataLocalization[] =
    kind === "app_info" ? snapshot.app_info : snapshot.versions;
  return Array.from(new Set(rows.map((r) => r.locale))).sort();
}

function valueForLocale(
  snapshot: AppMetadataSnapshot,
  locale: string,
  field: FieldKey,
): string {
  const cfg = FIELDS_BY_KEY[field];
  if (!cfg) return "";
  const rows: AppMetadataLocalization[] =
    cfg.kind === "app_info" ? snapshot.app_info : snapshot.versions;
  const row = rows.find((r) => r.locale === locale);
  if (!row) return "";
  const raw = (row as unknown as Record<string, string | null>)[field];
  return raw ?? "";
}

export default function BulkFanoutDrawer({
  appId,
  snapshot,
  opened,
  onClose,
}: BulkFanoutDrawerProps) {
  // Only fields the version is currently allowed to mutate. Falls back to
  // every field if the backend hasn't filled editable_fields (e.g. no
  // editable version exists — in which case Preview will surface that).
  const fieldOptions = useMemo(() => {
    const editableSet = new Set(snapshot.state.editable_fields);
    const all = [...fieldsForKind("app_info"), ...fieldsForKind("version")];
    const filtered = all.filter((f) => editableSet.has(f.key));
    return (filtered.length > 0 ? filtered : all).map((f) => ({
      value: f.key,
      label: `${f.label} (${f.kind === "app_info" ? "App Info" : "Version"})`,
    }));
  }, [snapshot.state.editable_fields]);

  const [field, setField] = useState<FieldKey | null>(null);
  const [sourceLocale, setSourceLocale] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [targets, setTargets] = useState<string[]>([]);
  const [previewResults, setPreviewResults] = useState<BulkPreviewItem[]>([]);

  const previewMutation = usePreviewBulkMetadata(appId);
  const applyMutation = useApplyBulkMetadata(appId);

  // Reset state whenever the drawer is reopened.
  useEffect(() => {
    if (opened) {
      setField(null);
      setSourceLocale(null);
      setValue("");
      setTargets([]);
      setPreviewResults([]);
    }
  }, [opened]);

  const fieldCfg = field ? FIELDS_BY_KEY[field] : null;
  const localePool = useMemo(
    () => (fieldCfg ? localesForKind(snapshot, fieldCfg.kind) : []),
    [snapshot, fieldCfg],
  );

  // When source locale or field changes, prefill the value.
  useEffect(() => {
    if (sourceLocale && field) {
      setValue(valueForLocale(snapshot, sourceLocale, field));
    }
  }, [sourceLocale, field, snapshot]);

  const allSelected =
    targets.length > 0 && targets.length === localePool.length;
  const toggleAll = () => {
    if (allSelected) setTargets([]);
    else setTargets(localePool.slice(0, BULK_LOCALE_CAP));
  };

  const overCap = targets.length > BULK_LOCALE_CAP;
  const previewReady = previewResults.length > 0;

  const onPreview = () => {
    if (!field || targets.length === 0) return;
    previewMutation.mutate(
      { field, value, target_locales: targets },
      {
        onSuccess: (out) => setPreviewResults(out.items),
      },
    );
  };

  const onApply = () => {
    if (!field || targets.length === 0) return;
    applyMutation.mutate(
      { field, value, target_locales: targets, force: false },
      {
        onSuccess: () => onClose(),
      },
    );
  };

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size="50%"
      title="Bulk fan-out"
      padding="md"
    >
      <Stack gap="md">
        <Select
          label="Field to update"
          placeholder="Pick a field"
          data={fieldOptions}
          value={field}
          onChange={(v) => {
            setField(v as FieldKey | null);
            setPreviewResults([]);
            setTargets([]);
            setSourceLocale(null);
            setValue("");
          }}
          searchable
        />

        {fieldCfg && (
          <>
            <Select
              label="Pre-fill value from locale (optional)"
              placeholder="Pick a source locale"
              data={localePool.map((l) => ({
                value: l,
                label: localeWithCode(l),
              }))}
              value={sourceLocale}
              onChange={(v) => {
                setSourceLocale(v);
                setPreviewResults([]);
              }}
              searchable
              clearable
            />

            <Textarea
              label="Value"
              value={value}
              onChange={(e) => {
                setValue(e.currentTarget.value);
                setPreviewResults([]);
              }}
              autosize
              minRows={3}
              maxRows={10}
              description={
                fieldCfg.charLimit
                  ? `Char limit ${fieldCfg.charLimit}`
                  : undefined
              }
            />

            <Stack gap={4}>
              <Group justify="space-between" align="flex-end">
                <Text size="sm" fw={500}>
                  Target locales
                </Text>
                <Checkbox
                  label="Select all"
                  checked={allSelected}
                  onChange={toggleAll}
                  size="xs"
                />
              </Group>
              <MultiSelect
                placeholder="Pick locales..."
                data={localePool.map((l) => ({
                  value: l,
                  label: localeWithCode(l),
                }))}
                value={targets}
                onChange={(vs) => {
                  setTargets(vs);
                  setPreviewResults([]);
                }}
                searchable
                clearable
              />
              {overCap && (
                <Text size="xs" c="red">
                  Bulk apply is capped at {BULK_LOCALE_CAP} locales per request.
                  Trim the selection to continue.
                </Text>
              )}
            </Stack>

            <Group>
              <Button
                variant="light"
                leftSection={<IconEye size={16} />}
                onClick={onPreview}
                loading={previewMutation.isPending}
                disabled={targets.length === 0 || overCap}
              >
                Preview
              </Button>
              <Button
                color="grape"
                leftSection={<IconUpload size={16} />}
                onClick={onApply}
                loading={applyMutation.isPending}
                disabled={!previewReady || overCap}
              >
                Apply ({targets.length})
              </Button>
            </Group>

            {previewResults.length > 0 && (
              <Stack gap="xs">
                <Text size="sm" fw={600}>
                  Preview ({previewResults.length} locales)
                </Text>
                <DataTable<BulkPreviewItem>
                  striped
                  withTableBorder
                  records={previewResults}
                  idAccessor="locale"
                  columns={[
                    {
                      accessor: "locale",
                      title: "Locale",
                      width: 140,
                      render: (r) => (
                        <Text size="xs">{localeWithCode(r.locale)}</Text>
                      ),
                    },
                    {
                      accessor: "diff",
                      title: "Current → New",
                      render: (r) => (
                        <MetadataValueDiff
                          before={r.current_value}
                          after={r.new_value}
                          multiline={fieldCfg.multiline}
                        />
                      ),
                    },
                    {
                      accessor: "char_overflow_by",
                      title: "Overflow",
                      width: 90,
                      render: (r) =>
                        r.char_overflow_by > 0 ? (
                          <Badge color="red" size="xs" variant="light">
                            +{r.char_overflow_by}
                          </Badge>
                        ) : (
                          <Text size="xs" c="dimmed">
                            ok
                          </Text>
                        ),
                    },
                    {
                      accessor: "would_skip",
                      title: "Status",
                      width: 200,
                      render: (r) =>
                        r.would_skip ? (
                          <Group gap={4}>
                            <Badge color="yellow" size="xs" variant="light">
                              skip
                            </Badge>
                            <Text size="xs" c="dimmed">
                              {r.reason ?? ""}
                            </Text>
                          </Group>
                        ) : (
                          <Badge color="green" size="xs" variant="light">
                            apply
                          </Badge>
                        ),
                    },
                  ]}
                />
              </Stack>
            )}
          </>
        )}

        {!fieldCfg && fieldOptions.length === 0 && (
          <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
            No editable fields right now — the version may be locked. Try
            syncing again or check App Store Connect.
          </Alert>
        )}
      </Stack>
    </Drawer>
  );
}
