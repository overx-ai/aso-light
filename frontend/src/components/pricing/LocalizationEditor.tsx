import { useState, useCallback, useEffect } from "react";
import {
  Paper,
  Stack,
  Group,
  Button,
  Text,
  TextInput,
  Textarea,
  Select,
  Badge,
  ActionIcon,
  Table,
  Modal,
  JsonInput,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconPlus,
  IconTrash,
  IconDeviceFloppy,
  IconCode,
} from "@tabler/icons-react";
import type { Localization, LocalizationCreate } from "@/types";

const COMMON_LOCALES = [
  { value: "en-US", label: "en-US (English)" },
  { value: "es-ES", label: "es-ES (Spanish)" },
  { value: "ru", label: "ru (Russian)" },
  { value: "de-DE", label: "de-DE (German)" },
  { value: "pt-BR", label: "pt-BR (Portuguese)" },
  { value: "fr-FR", label: "fr-FR (French)" },
  { value: "ja", label: "ja (Japanese)" },
  { value: "ko", label: "ko (Korean)" },
  { value: "zh-Hans", label: "zh-Hans (Chinese Simplified)" },
  { value: "zh-Hant", label: "zh-Hant (Chinese Traditional)" },
  { value: "it", label: "it (Italian)" },
  { value: "nl-NL", label: "nl-NL (Dutch)" },
  { value: "tr", label: "tr (Turkish)" },
  { value: "ar-SA", label: "ar-SA (Arabic)" },
  { value: "hi", label: "hi (Hindi)" },
  { value: "th", label: "th (Thai)" },
  { value: "vi", label: "vi (Vietnamese)" },
  { value: "id", label: "id (Indonesian)" },
  { value: "ms", label: "ms (Malay)" },
  { value: "pl", label: "pl (Polish)" },
  { value: "uk", label: "uk (Ukrainian)" },
  { value: "sv", label: "sv (Swedish)" },
  { value: "da", label: "da (Danish)" },
  { value: "fi", label: "fi (Finnish)" },
  { value: "no", label: "no (Norwegian)" },
  { value: "cs", label: "cs (Czech)" },
  { value: "el", label: "el (Greek)" },
  { value: "he", label: "he (Hebrew)" },
  { value: "ro", label: "ro (Romanian)" },
  { value: "hu", label: "hu (Hungarian)" },
  { value: "sk", label: "sk (Slovak)" },
  { value: "ca", label: "ca (Catalan)" },
  { value: "hr", label: "hr (Croatian)" },
  { value: "en-GB", label: "en-GB (English UK)" },
  { value: "en-AU", label: "en-AU (English AU)" },
  { value: "es-MX", label: "es-MX (Spanish MX)" },
  { value: "pt-PT", label: "pt-PT (Portuguese PT)" },
  { value: "fr-CA", label: "fr-CA (French CA)" },
];

interface EditableRow {
  locale: string;
  name: string;
  description: string;
  isNew: boolean;
}

interface LocalizationEditorProps {
  localizations: Localization[];
  onSave: (items: LocalizationCreate[]) => void;
  isSaving: boolean;
  isLoading: boolean;
}

export default function LocalizationEditor({
  localizations,
  onSave,
  isSaving,
  isLoading,
}: LocalizationEditorProps) {
  const [rows, setRows] = useState<EditableRow[]>([]);
  const [dirty, setDirty] = useState(false);

  // Sync from props when localizations change
  useEffect(() => {
    setRows(
      localizations.map((l) => ({
        locale: l.locale,
        name: l.name,
        description: l.description,
        isNew: false,
      })),
    );
    setDirty(false);
  }, [localizations]);

  const usedLocales = new Set(rows.map((r) => r.locale));
  const availableLocales = COMMON_LOCALES.filter(
    (l) => !usedLocales.has(l.value),
  );

  const handleAddLocale = useCallback(
    (locale: string | null) => {
      if (!locale) return;
      setRows((prev) => [
        ...prev,
        { locale, name: "", description: "", isNew: true },
      ]);
      setDirty(true);
    },
    [],
  );

  const handleRemoveRow = useCallback((locale: string) => {
    setRows((prev) => prev.filter((r) => r.locale !== locale));
    setDirty(true);
  }, []);

  const handleFieldChange = useCallback(
    (locale: string, field: "name" | "description", value: string) => {
      setRows((prev) =>
        prev.map((r) => (r.locale === locale ? { ...r, [field]: value } : r)),
      );
      setDirty(true);
    },
    [],
  );

  const handleSave = useCallback(() => {
    const items = rows
      .filter((r) => r.name.trim() && r.description.trim())
      .map((r) => ({
        locale: r.locale,
        name: r.name.trim(),
        description: r.description.trim(),
      }));
    onSave(items);
  }, [rows, onSave]);

  const [jsonOpened, { open: openJson, close: closeJson }] =
    useDisclosure(false);
  const [jsonValue, setJsonValue] = useState("");
  const [jsonError, setJsonError] = useState("");

  const handleJsonImport = useCallback(() => {
    try {
      const parsed = JSON.parse(jsonValue);
      const items: EditableRow[] = [];

      // Support both array and object formats
      // Array: [{"locale":"en-US","name":"...","description":"..."}]
      // Object: {"en-US":{"name":"...","description":"..."}}
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          if (item.locale && item.name && item.description) {
            items.push({ ...item, isNew: true });
          }
        }
      } else if (typeof parsed === "object") {
        for (const [locale, val] of Object.entries(parsed)) {
          const v = val as { name?: string; description?: string };
          if (v.name && v.description) {
            items.push({
              locale,
              name: v.name,
              description: v.description,
              isNew: true,
            });
          }
        }
      }

      if (items.length === 0) {
        setJsonError("No valid localizations found in JSON.");
        return;
      }

      // Merge: update existing rows, add new ones
      setRows((prev) => {
        const existing = new Map(prev.map((r) => [r.locale, r]));
        for (const item of items) {
          existing.set(item.locale, item);
        }
        return Array.from(existing.values());
      });
      setDirty(true);
      setJsonError("");
      closeJson();
      setJsonValue("");
    } catch {
      setJsonError("Invalid JSON.");
    }
  }, [jsonValue, closeJson]);

  return (
    <>
      <Modal
        opened={jsonOpened}
        onClose={closeJson}
        title="Import from JSON"
        size="lg"
      >
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            Array or object format:
          </Text>
          <Text size="xs" c="dimmed" style={{ fontFamily: "monospace" }}>
            {`[{"locale":"en-US","name":"...","description":"..."}]`}
          </Text>
          <Text size="xs" c="dimmed" style={{ fontFamily: "monospace" }}>
            {`{"en-US":{"name":"...","description":"..."}}`}
          </Text>
          <JsonInput
            value={jsonValue}
            onChange={setJsonValue}
            placeholder="Paste JSON here..."
            autosize
            minRows={8}
            maxRows={20}
            validationError={jsonError || undefined}
            formatOnBlur
          />
          <Group justify="flex-end">
            <Button variant="subtle" onClick={closeJson}>
              Cancel
            </Button>
            <Button onClick={handleJsonImport} disabled={!jsonValue.trim()}>
              Import
            </Button>
          </Group>
        </Stack>
      </Modal>

    <Paper withBorder p="md" radius="md">
      <Stack gap="md">
        <Group justify="space-between">
          <Group gap="xs">
            <Text fw={600} size="sm">
              Localizations
            </Text>
            <Badge size="sm" variant="light">
              {rows.length}
            </Badge>
          </Group>
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              color="gray"
              leftSection={<IconCode size={14} />}
              onClick={openJson}
            >
              JSON
            </Button>
            <Select
              placeholder="Add locale..."
              data={availableLocales}
              onChange={handleAddLocale}
              value={null}
              searchable
              size="xs"
              w={200}
              leftSection={<IconPlus size={14} />}
            />
            <Button
              size="xs"
              leftSection={<IconDeviceFloppy size={14} />}
              onClick={handleSave}
              loading={isSaving}
              disabled={
                !dirty ||
                rows.length === 0 ||
                rows.some((r) => r.name.length > 30 || r.description.length > 55)
              }
            >
              Save All
            </Button>
          </Group>
        </Group>

        {rows.length === 0 && !isLoading ? (
          <Text size="sm" c="dimmed" ta="center" py="lg">
            No localizations yet. Add a locale to get started.
          </Text>
        ) : (
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={100}>Locale</Table.Th>
                <Table.Th w={250}>Name</Table.Th>
                <Table.Th>Description</Table.Th>
                <Table.Th w={40} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row.locale}>
                  <Table.Td>
                    <Badge
                      size="sm"
                      variant={row.isNew ? "outline" : "light"}
                      color={row.isNew ? "blue" : "gray"}
                    >
                      {row.locale}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <TextInput
                      size="xs"
                      placeholder="Display name"
                      value={row.name}
                      onChange={(e) =>
                        handleFieldChange(
                          row.locale,
                          "name",
                          e.currentTarget.value,
                        )
                      }
                      error={row.name.length > 30 ? `${row.name.length}/30` : undefined}
                      maxLength={30}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Textarea
                      size="xs"
                      placeholder="Description (max 55 chars)"
                      value={row.description}
                      onChange={(e) =>
                        handleFieldChange(
                          row.locale,
                          "description",
                          e.currentTarget.value,
                        )
                      }
                      error={row.description.length > 55 ? `${row.description.length}/55` : undefined}
                      autosize
                      minRows={1}
                      maxRows={3}
                    />
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon
                      size="sm"
                      variant="subtle"
                      color="red"
                      onClick={() => handleRemoveRow(row.locale)}
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Paper>
    </>
  );
}
