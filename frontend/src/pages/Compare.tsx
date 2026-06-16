/**
 * Visual old-vs-new screenshot Compare page.
 *
 * Two ways to compare App Store screenshots for a locale + device:
 *
 *  - **Upload compare (default)** — drop in your proposed "after" images and
 *    either pull the live default-page screenshots from App Store Connect or
 *    upload your current "before" set manually. Renders a slot-by-slot
 *    before/after grid (top = before, bottom = after) entirely in the
 *    browser — no Custom Product Page or terminal required. From here you can
 *    also push the uploaded "after" set straight into a new Custom Product
 *    Page in App Store Connect (ready to attach to Apple Search Ads).
 *  - **Custom Product Page** — the original flow: pick a CPP and the backend
 *    composites a server-rendered BEFORE/AFTER montage PNG.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Center,
  Container,
  Grid,
  Group,
  Image,
  Loader,
  Modal,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Tabs,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconCloudDownload,
  IconDeviceMobileShare,
  IconPhoto,
  IconPlus,
  IconUpload,
  IconX,
} from "@tabler/icons-react";
import {
  useCompareImage,
  useCpps,
  useCreateCpp,
  useCreateCppFromUpload,
  useDefaultScreenshots,
} from "@/lib/cpp-hooks";

// App Store Connect ``screenshotDisplayType`` values, keyed by the device
// family they target. Mirrors the constants Apple documents for the standard
// appScreenshotSets model the CPP screenshots reuse.
const DISPLAY_TYPES = [
  { value: "APP_IPHONE_67", label: 'iPhone 6.7" (1290x2796)' },
  { value: "APP_IPHONE_65", label: 'iPhone 6.5" (1242x2688)' },
  { value: "APP_IPHONE_61", label: 'iPhone 6.1" (1179x2556)' },
  { value: "APP_IPHONE_58", label: 'iPhone 5.8" (1125x2436)' },
  { value: "APP_IPHONE_55", label: 'iPhone 5.5" (1242x2208)' },
  { value: "APP_IPAD_PRO_3GEN_129", label: 'iPad Pro 12.9" (2048x2732)' },
  { value: "APP_IPAD_PRO_129", label: 'iPad Pro 12.9" gen2 (2048x2732)' },
];

// First iPhone display type, used as the default device selection.
const DEFAULT_DISPLAY_TYPE = DISPLAY_TYPES[0].value;

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
  { value: "it", label: "it (Italian)" },
  { value: "nl-NL", label: "nl-NL (Dutch)" },
  { value: "tr", label: "tr (Turkish)" },
  { value: "en-GB", label: "en-GB (English UK)" },
];

// Primary locale default for the upload compare.
const DEFAULT_LOCALE = COMMON_LOCALES[0].value;

// Fixed slot thumbnail width; the App-Store-result-like grid scrolls
// horizontally when there are more slots than fit.
const SLOT_WIDTH = 150;

// iPhone 6.7" portrait aspect (2796x1290) — used to derive each slot cell's
// height from SLOT_WIDTH so before/after thumbnails share a phone-shaped frame.
// Mirrors DEFAULT_ASPECT in the backend montage compositor (compare.py).
const SLOT_ASPECT = 2796 / 1290;
const SLOT_HEIGHT = Math.round(SLOT_WIDTH * SLOT_ASPECT);

/**
 * Extract a user-facing message from a query/mutation error, preferring the
 * server-supplied ``detail`` (e.g. an axios error body) over the generic
 * "Request failed with status code 500".
 */
function errorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: string } } })
    ?.response?.data?.detail;
  return detail ?? (error as Error)?.message ?? fallback;
}

/** A "before" or "after" image with a stable, displayable URL. */
interface SlotImage {
  url: string;
  /** True when ``url`` is an object URL we created and must revoke. */
  isObjectUrl: boolean;
  label?: string | null;
}

// ---- Upload compare ----

/**
 * A multi-file image picker that exposes the selected files as ordered
 * object URLs. Uses a plain ``<input type="file">`` (no extra deps) styled
 * as a dashed drop target; the same surface accepts drag-and-drop.
 */
function ImageDropInput({
  id,
  label,
  accentColor,
  onFilesChange,
  disabled,
}: {
  id: string;
  label: string;
  accentColor: string;
  onFilesChange: (files: File[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [count, setCount] = useState(0);

  const acceptFiles = (fileList: FileList | null) => {
    if (!fileList) return;
    const files = Array.from(fileList).filter((f) =>
      f.type.startsWith("image/"),
    );
    setCount(files.length);
    onFilesChange(files);
  };

  return (
    <Box>
      <input
        ref={inputRef}
        id={id}
        type="file"
        multiple
        accept="image/*"
        style={{ display: "none" }}
        onChange={(e) => acceptFiles(e.currentTarget.files)}
        disabled={disabled}
      />
      <Box
        role="button"
        tabIndex={0}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => {
          if (!disabled && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (!disabled) acceptFiles(e.dataTransfer.files);
        }}
        style={{
          border: `2px dashed var(--mantine-color-${
            dragOver ? accentColor : "gray"
          }-5)`,
          borderRadius: "var(--mantine-radius-md)",
          padding: "var(--mantine-spacing-md)",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          background: dragOver
            ? `var(--mantine-color-${accentColor}-0)`
            : "transparent",
          transition: "background 120ms ease, border-color 120ms ease",
        }}
      >
        <Group gap="xs" justify="center" wrap="nowrap">
          <IconUpload size={18} />
          <Text size="sm" ta="center">
            {count > 0
              ? `${count} image${count === 1 ? "" : "s"} selected — ${label}`
              : `Drop or click to choose ${label}`}
          </Text>
        </Group>
      </Box>
    </Box>
  );
}

/** A single before/after column for one slot index. */
function SlotColumn({
  slot,
  before,
  after,
}: {
  slot: number;
  before: SlotImage | null;
  after: SlotImage | null;
}) {
  return (
    <Stack gap={6} align="center" style={{ width: SLOT_WIDTH }}>
      <Badge size="sm" variant="light" color="gray">
        Slot {slot}
      </Badge>
      <SlotCell image={before} tone="blue" emptyLabel="No before" />
      <SlotCell image={after} tone="grape" emptyLabel="No after" />
    </Stack>
  );
}

/** One image cell (or an empty placeholder) inside a slot column. */
function SlotCell({
  image,
  tone,
  emptyLabel,
}: {
  image: SlotImage | null;
  tone: string;
  emptyLabel: string;
}) {
  if (!image) {
    return (
      <Center
        style={{
          width: SLOT_WIDTH,
          height: SLOT_HEIGHT,
          borderRadius: "var(--mantine-radius-sm)",
          border: "1px dashed var(--mantine-color-gray-4)",
          background: "var(--mantine-color-gray-0)",
        }}
      >
        <Text size="xs" c="dimmed" ta="center">
          {emptyLabel}
        </Text>
      </Center>
    );
  }
  return (
    <Box
      style={{
        width: SLOT_WIDTH,
        borderRadius: "var(--mantine-radius-sm)",
        overflow: "hidden",
        border: `2px solid var(--mantine-color-${tone}-4)`,
      }}
    >
      <Image
        src={image.url}
        alt={image.label ?? "screenshot"}
        w={SLOT_WIDTH}
        h={SLOT_HEIGHT}
        fit="cover"
      />
    </Box>
  );
}

/**
 * Modal prompting for the new CPP name, then dispatching the create-from-upload
 * mutation with the proposed "after" files. The default name folds in the
 * selected locale so multi-locale pages stay distinguishable.
 */
function CreateCppFromUploadModal({
  appId,
  opened,
  onClose,
  locale,
  displayType,
  files,
}: {
  appId: number;
  opened: boolean;
  onClose: () => void;
  locale: string;
  displayType: string;
  files: File[];
}) {
  const createFromUpload = useCreateCppFromUpload(appId);
  const [name, setName] = useState("");

  // Seed a sensible default name each time the modal opens.
  useEffect(() => {
    if (opened) setName(`Proposed — ${locale}`);
  }, [opened, locale]);

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed || files.length === 0) return;
    createFromUpload.mutate(
      { name: trimmed, locale, displayType, files },
      { onSuccess: () => onClose() },
    );
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Create Custom Product Page from this set"
      centered
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Uploads {files.length} screenshot{files.length === 1 ? "" : "s"} to a
          new Custom Product Page for {locale} ({displayType}). The page can then
          be attached to an Apple Search Ads ad group.
        </Text>
        <TextInput
          label="Page name"
          placeholder="e.g. Proposed — en-US"
          value={name}
          maxLength={100}
          data-autofocus
          onChange={(e) => setName(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <Group justify="flex-end">
          <Button
            variant="default"
            onClick={onClose}
            disabled={createFromUpload.isPending}
          >
            Cancel
          </Button>
          <Button
            leftSection={<IconDeviceMobileShare size={16} />}
            onClick={submit}
            loading={createFromUpload.isPending}
            disabled={!name.trim() || files.length === 0}
          >
            Create page
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function UploadComparePanel({ appId }: { appId: number }) {
  const [locale, setLocale] = useState<string | null>(DEFAULT_LOCALE);
  const [displayType, setDisplayType] = useState<string | null>(
    DEFAULT_DISPLAY_TYPE,
  );

  // Raw File[] selections; object URLs are derived in effects so they can be
  // revoked deterministically.
  const [afterFiles, setAfterFiles] = useState<File[]>([]);
  const [beforeFiles, setBeforeFiles] = useState<File[]>([]);
  // When set, the "before" side comes from ASC's live default page instead of
  // uploaded files. Cleared if the user uploads a manual before set.
  const [useAscBefore, setUseAscBefore] = useState(false);

  const [afterUrls, setAfterUrls] = useState<SlotImage[]>([]);
  const [beforeUrls, setBeforeUrls] = useState<SlotImage[]>([]);

  // Controls the "create CPP from this set" name prompt.
  const [createModalOpen, setCreateModalOpen] = useState(false);

  // Derive + revoke object URLs for the "after" files.
  useEffect(() => {
    const next: SlotImage[] = afterFiles.map((f) => ({
      url: URL.createObjectURL(f),
      isObjectUrl: true,
      label: f.name,
    }));
    setAfterUrls(next);
    return () => next.forEach((s) => URL.revokeObjectURL(s.url));
  }, [afterFiles]);

  // Derive + revoke object URLs for the manually-uploaded "before" files.
  useEffect(() => {
    const next: SlotImage[] = beforeFiles.map((f) => ({
      url: URL.createObjectURL(f),
      isObjectUrl: true,
      label: f.name,
    }));
    setBeforeUrls(next);
    return () => next.forEach((s) => URL.revokeObjectURL(s.url));
  }, [beforeFiles]);

  const {
    data: ascDefault,
    isFetching: ascFetching,
    error: ascError,
    refetch: refetchAsc,
  } = useDefaultScreenshots(
    appId,
    useAscBefore ? locale : null,
    useAscBefore ? displayType : null,
  );

  // The ASC default screenshots are public CDN URLs — usable directly.
  const ascBeforeImages: SlotImage[] = useMemo(
    () =>
      (ascDefault ?? []).map((shot) => ({
        url: shot.source_url,
        isObjectUrl: false,
        label: shot.file_name,
      })),
    [ascDefault],
  );

  // Resolve which "before" set to render: ASC-live takes precedence once the
  // user has loaded it, otherwise fall back to manual uploads.
  const beforeImages = useAscBefore ? ascBeforeImages : beforeUrls;

  const slotCount = Math.max(beforeImages.length, afterUrls.length);

  const loadFromAppStore = () => {
    setBeforeFiles([]); // ASC-live supersedes any manual before set
    setUseAscBefore(true);
    // refetch covers the case where the toggle was already on.
    void refetchAsc();
  };

  const onBeforeUpload = (files: File[]) => {
    setUseAscBefore(false);
    setBeforeFiles(files);
  };

  const canCreateCpp =
    afterFiles.length > 0 && !!locale && !!displayType;

  return (
    <Stack gap="md">
      <Card withBorder padding="md" radius="md">
        <Group grow align="flex-end">
          <Select
            label="Device"
            data={DISPLAY_TYPES}
            value={displayType}
            onChange={setDisplayType}
            searchable
          />
          <Select
            label="Locale"
            data={COMMON_LOCALES}
            value={locale}
            onChange={setLocale}
            searchable
          />
        </Group>
      </Card>

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Card withBorder padding="md" radius="md" h="100%">
            <Stack gap="sm">
              <Group gap="xs">
                <Badge color="grape" variant="filled">
                  After
                </Badge>
                <Text fw={600} size="sm">
                  Proposed screenshots
                </Text>
              </Group>
              <Text size="xs" c="dimmed">
                Drop the new creative set, in slot order. Required.
              </Text>
              <ImageDropInput
                id="after-upload"
                label="after (proposed)"
                accentColor="grape"
                onFilesChange={setAfterFiles}
              />
              <Button
                variant="light"
                color="grape"
                leftSection={<IconDeviceMobileShare size={16} />}
                onClick={() => setCreateModalOpen(true)}
                disabled={!canCreateCpp}
              >
                Create Custom Product Page from this set
              </Button>
              <Text size="xs" c="dimmed">
                Pushes these {afterFiles.length || "0"} screenshot
                {afterFiles.length === 1 ? "" : "s"} into a new Custom Product
                Page in App Store Connect, ready to attach to an Apple Search
                Ads ad group.
              </Text>
            </Stack>
          </Card>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 6 }}>
          <Card withBorder padding="md" radius="md" h="100%">
            <Stack gap="sm">
              <Group gap="xs">
                <Badge color="blue" variant="filled">
                  Before
                </Badge>
                <Text fw={600} size="sm">
                  Current screenshots
                </Text>
              </Group>
              <Text size="xs" c="dimmed">
                Pull the live default page from App Store Connect, or upload
                your current set manually.
              </Text>
              <Button
                variant="light"
                leftSection={<IconCloudDownload size={16} />}
                onClick={loadFromAppStore}
                loading={useAscBefore && ascFetching}
              >
                Load current from App Store
              </Button>
              {useAscBefore && !ascFetching && !ascError ? (
                ascBeforeImages.length > 0 ? (
                  <Text size="xs" c="blue">
                    Loaded {ascBeforeImages.length} live screenshot
                    {ascBeforeImages.length === 1 ? "" : "s"}.
                  </Text>
                ) : (
                  <Text size="xs" c="dimmed">
                    App Store Connect returned no screenshots for this
                    locale/device. Upload a before set manually instead.
                  </Text>
                )
              ) : null}
              {ascError ? (
                <Text size="xs" c="red">
                  Could not reach App Store Connect. Upload a before set
                  manually instead.
                </Text>
              ) : null}
              <ImageDropInput
                id="before-upload"
                label="before (current)"
                accentColor="blue"
                onFilesChange={onBeforeUpload}
              />
            </Stack>
          </Card>
        </Grid.Col>
      </Grid>

      <Card withBorder padding="md" radius="md" mih={320}>
        <Stack gap="sm">
          <Group justify="space-between">
            <Text fw={600} size="sm">
              Slot-by-slot comparison
            </Text>
            <Group gap="md">
              <Group gap={6}>
                <Box
                  w={12}
                  h={12}
                  style={{
                    borderRadius: 2,
                    background: "var(--mantine-color-blue-4)",
                  }}
                />
                <Text size="xs" c="dimmed">
                  Before (top)
                </Text>
              </Group>
              <Group gap={6}>
                <Box
                  w={12}
                  h={12}
                  style={{
                    borderRadius: 2,
                    background: "var(--mantine-color-grape-4)",
                  }}
                />
                <Text size="xs" c="dimmed">
                  After (bottom)
                </Text>
              </Group>
            </Group>
          </Group>

          {slotCount === 0 ? (
            <Center mih={260}>
              <Stack align="center" gap="xs">
                <IconPhoto size={40} color="var(--mantine-color-dimmed)" />
                <Text size="sm" c="dimmed" ta="center">
                  Add proposed screenshots and a current set to see the
                  before/after grid.
                </Text>
              </Stack>
            </Center>
          ) : (
            <ScrollArea type="auto" offsetScrollbars>
              <Group gap="md" align="flex-start" wrap="nowrap" py="xs">
                {Array.from({ length: slotCount }, (_, index) => (
                  <SlotColumn
                    key={index}
                    slot={index + 1}
                    before={beforeImages[index] ?? null}
                    after={afterUrls[index] ?? null}
                  />
                ))}
              </Group>
            </ScrollArea>
          )}

          {slotCount > 0 &&
          beforeImages.length !== afterUrls.length ? (
            <Alert
              color="yellow"
              variant="light"
              icon={<IconAlertCircle size={16} />}
            >
              Slot counts differ — before has {beforeImages.length}, after has{" "}
              {afterUrls.length}. Empty cells are placeholders.
            </Alert>
          ) : null}
        </Stack>
      </Card>

      <CreateCppFromUploadModal
        appId={appId}
        opened={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        locale={locale ?? DEFAULT_LOCALE}
        displayType={displayType ?? DEFAULT_DISPLAY_TYPE}
        files={afterFiles}
      />
    </Stack>
  );
}

// ---- Custom Product Page compare (server-rendered montage) ----

function CreateCppForm({ appId }: { appId: number }) {
  const createCpp = useCreateCpp(appId);
  const [name, setName] = useState("");
  const [visible, setVisible] = useState(true);

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    createCpp.mutate(
      { name: trimmed, visible },
      {
        onSuccess: () => {
          setName("");
          setVisible(true);
        },
      },
    );
  };

  return (
    <Card withBorder padding="md" radius="md">
      <Stack gap="sm">
        <Text fw={600} size="sm">
          Create Custom Product Page
        </Text>
        <TextInput
          label="Name"
          placeholder="e.g. Sleep-focused creative"
          value={name}
          maxLength={100}
          onChange={(e) => setName(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <Switch
          label="Visible"
          checked={visible}
          onChange={(e) => setVisible(e.currentTarget.checked)}
        />
        <Button
          leftSection={<IconPlus size={16} />}
          onClick={submit}
          loading={createCpp.isPending}
          disabled={!name.trim()}
        >
          Create page
        </Button>
      </Stack>
    </Card>
  );
}

function CppListPanel({ appId }: { appId: number }) {
  const { data: cpps, isLoading, error } = useCpps(appId);

  return (
    <Stack gap="md">
      <CreateCppForm appId={appId} />
      <Card withBorder padding="md" radius="md">
        <Stack gap="sm">
          <Text fw={600} size="sm">
            Custom Product Pages
          </Text>
          {isLoading ? (
            <Center mih={80}>
              <Loader size="sm" />
            </Center>
          ) : error ? (
            <Alert color="red" icon={<IconAlertCircle size={16} />}>
              {errorMessage(error, "Could not load Custom Product Pages.")}
            </Alert>
          ) : !cpps || cpps.length === 0 ? (
            <Text size="sm" c="dimmed">
              No Custom Product Pages yet. Create one above.
            </Text>
          ) : (
            <Stack gap="xs">
              {cpps.map((cpp) => (
                <Group key={cpp.id} justify="space-between" wrap="nowrap">
                  <Box style={{ minWidth: 0 }}>
                    <Text size="sm" fw={500} truncate>
                      {cpp.name ?? cpp.id}
                    </Text>
                    <Text size="xs" c="dimmed" truncate>
                      {cpp.id}
                    </Text>
                  </Box>
                  <Badge
                    size="sm"
                    color={cpp.visible ? "green" : "gray"}
                    variant="light"
                  >
                    {cpp.visible ? "Visible" : "Hidden"}
                  </Badge>
                </Group>
              ))}
            </Stack>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}

function CppComparePanel({ appId }: { appId: number }) {
  const { data: cpps } = useCpps(appId);
  const [cppId, setCppId] = useState<string | null>(null);
  const [locale, setLocale] = useState<string | null>("en-US");
  const [displayType, setDisplayType] = useState<string | null>(
    "APP_IPHONE_67",
  );

  // Default to the first CPP once the list loads (keeps a valid selection
  // without forcing the user to pick before anything renders).
  useEffect(() => {
    if (!cppId && cpps && cpps.length > 0) {
      setCppId(cpps[0].id);
    }
  }, [cpps, cppId]);

  const {
    data: imageUrl,
    isFetching,
    error,
  } = useCompareImage(appId, cppId, locale, displayType);

  const cppOptions = (cpps ?? []).map((cpp) => ({
    value: cpp.id,
    label: cpp.name ?? cpp.id,
  }));

  return (
    <Stack gap="md">
      <Card withBorder padding="md" radius="md">
        <Group grow align="flex-end">
          <Select
            label="Custom Product Page (after)"
            placeholder="Select a CPP"
            data={cppOptions}
            value={cppId}
            onChange={setCppId}
            searchable
            nothingFoundMessage="No Custom Product Pages"
          />
          <Select
            label="Locale"
            data={COMMON_LOCALES}
            value={locale}
            onChange={setLocale}
            searchable
          />
          <Select
            label="Device"
            data={DISPLAY_TYPES}
            value={displayType}
            onChange={setDisplayType}
            searchable
          />
        </Group>
      </Card>

      <Card withBorder padding="md" radius="md" mih={320}>
        {!cppId || !locale || !displayType ? (
          <Center mih={280}>
            <Stack align="center" gap="xs">
              <IconPhoto size={40} color="var(--mantine-color-dimmed)" />
              <Text size="sm" c="dimmed">
                Pick a Custom Product Page, locale, and device to compare.
              </Text>
            </Stack>
          </Center>
        ) : isFetching ? (
          <Center mih={280}>
            <Stack align="center" gap="xs">
              <Loader />
              <Text size="sm" c="dimmed">
                Building before/after montage…
              </Text>
            </Stack>
          </Center>
        ) : error ? (
          <Alert
            color="red"
            icon={<IconX size={16} />}
            title="Could not build comparison"
          >
            {errorMessage(
              error,
              "The default page or the CPP may have no screenshots for this locale/device.",
            )}
          </Alert>
        ) : imageUrl ? (
          <Image
            src={imageUrl}
            alt="Before/after screenshot comparison"
            fit="contain"
          />
        ) : (
          <Center mih={280}>
            <Text size="sm" c="dimmed">
              No comparison available.
            </Text>
          </Center>
        )}
      </Card>
    </Stack>
  );
}

export default function Compare() {
  const { id } = useParams<{ id: string }>();
  const appId = Number(id);

  if (!Number.isFinite(appId) || appId <= 0) {
    return (
      <Container size="xl">
        <Alert color="red" icon={<IconAlertCircle size={16} />}>
          Invalid app id.
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="xl">
      <Stack gap="md">
        <Title order={2}>Visual Compare</Title>
        <Text size="sm" c="dimmed">
          See a slot-by-slot before/after of your App Store screenshots. Drop
          in proposed images and load your current set from App Store Connect —
          no Custom Product Page required.
        </Text>
        <Tabs defaultValue="upload" keepMounted={false}>
          <Tabs.List>
            <Tabs.Tab value="upload" leftSection={<IconUpload size={16} />}>
              Upload compare
            </Tabs.Tab>
            <Tabs.Tab value="cpp" leftSection={<IconPhoto size={16} />}>
              Custom Product Page
            </Tabs.Tab>
          </Tabs.List>

          <Tabs.Panel value="upload" pt="md">
            <UploadComparePanel appId={appId} />
          </Tabs.Panel>

          <Tabs.Panel value="cpp" pt="md">
            <Grid gutter="md">
              <Grid.Col span={{ base: 12, md: 8 }}>
                <CppComparePanel appId={appId} />
              </Grid.Col>
              <Grid.Col span={{ base: 12, md: 4 }}>
                <CppListPanel appId={appId} />
              </Grid.Col>
            </Grid>
          </Tabs.Panel>
        </Tabs>
      </Stack>
    </Container>
  );
}
