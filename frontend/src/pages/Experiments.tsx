/**
 * Product Page Optimization (PPO) page — App Store Version Experiments.
 *
 * Manage A/B tests of the app's product page: create experiments, add up to 3
 * treatments (variants), upload localized screenshot sets per treatment, and
 * drive the lifecycle (submit for review / stop). Apple's API exposes no
 * experiment *results* (impressions, conversion, confidence) — those live only
 * in App Store Connect's Analytics UI — so this page deep-links there for
 * results rather than showing numbers it cannot fetch.
 */
import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Center,
  Container,
  Divider,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconExternalLink,
  IconFlask,
  IconPlus,
  IconTrash,
  IconUpload,
} from "@tabler/icons-react";
import { useApp } from "@/lib/hooks";
import {
  type Experiment,
  type Treatment,
  useCreateExperiment,
  useCreateTreatment,
  useDeleteExperiment,
  useDeleteTreatment,
  useExperiments,
  useTreatments,
  useUpdateExperiment,
  useUploadTreatmentScreenshots,
} from "@/lib/experiment-hooks";

// App Store Connect ``screenshotDisplayType`` values keyed by device family.
// Mirrors the list on the Compare page (the standard appScreenshotSets model).
const DISPLAY_TYPES = [
  { value: "APP_IPHONE_67", label: 'iPhone 6.7" (1290x2796)' },
  { value: "APP_IPHONE_65", label: 'iPhone 6.5" (1242x2688)' },
  { value: "APP_IPHONE_61", label: 'iPhone 6.1" (1179x2556)' },
  { value: "APP_IPHONE_58", label: 'iPhone 5.8" (1125x2436)' },
  { value: "APP_IPAD_PRO_3GEN_129", label: 'iPad Pro 12.9" (2048x2732)' },
];

const COMMON_LOCALES = [
  { value: "en-US", label: "en-US (English)" },
  { value: "es-ES", label: "es-ES (Spanish)" },
  { value: "de-DE", label: "de-DE (German)" },
  { value: "fr-FR", label: "fr-FR (French)" },
  { value: "pt-BR", label: "pt-BR (Portuguese)" },
  { value: "ja", label: "ja (Japanese)" },
  { value: "zh-Hans", label: "zh-Hans (Chinese Simplified)" },
];

// Apple allows at most this many treatments (variants) per experiment.
const MAX_TREATMENTS = 3;

/** States from which an experiment can still be edited/deleted (pre-review). */
const DRAFT_STATES = new Set([
  "PREPARE_FOR_SUBMISSION",
  "READY_FOR_REVIEW",
  "REJECTED",
]);

/** Map an experiment state to a Mantine badge color. */
function stateColor(state: string | null): string {
  switch (state) {
    case "APPROVED":
    case "ACCEPTED":
      return "green";
    case "IN_REVIEW":
    case "WAITING_FOR_REVIEW":
      return "yellow";
    case "REJECTED":
      return "red";
    case "STOPPED":
    case "COMPLETED":
      return "gray";
    default:
      return "blue";
  }
}

/**
 * Per-treatment "after" screenshot uploader: a locale + device picker and a
 * multi-file input that posts to the treatment's from-upload endpoint. Each
 * treatment card renders its own instance with isolated local state.
 */
function TreatmentUploader({
  appId,
  experimentId,
  treatmentId,
}: {
  appId: number;
  experimentId: string;
  treatmentId: string;
}) {
  const [locale, setLocale] = useState<string>(COMMON_LOCALES[0].value);
  const [displayType, setDisplayType] = useState<string>(DISPLAY_TYPES[0].value);
  const [files, setFiles] = useState<File[]>([]);
  const upload = useUploadTreatmentScreenshots(appId, experimentId);

  const submit = () => {
    if (files.length === 0) return;
    upload.mutate(
      { treatmentId, locale, displayType, files },
      { onSuccess: () => setFiles([]) },
    );
  };

  return (
    <Stack gap="xs">
      <Group grow>
        <Select
          label="Locale"
          data={COMMON_LOCALES}
          value={locale}
          onChange={(v) => v && setLocale(v)}
          size="xs"
          searchable
        />
        <Select
          label="Device"
          data={DISPLAY_TYPES}
          value={displayType}
          onChange={(v) => v && setDisplayType(v)}
          size="xs"
        />
      </Group>
      <input
        type="file"
        multiple
        accept="image/*"
        onChange={(e) =>
          setFiles(
            Array.from(e.currentTarget.files ?? []).filter((f) =>
              f.type.startsWith("image/"),
            ),
          )
        }
      />
      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          {files.length > 0
            ? `${files.length} file${files.length === 1 ? "" : "s"} selected`
            : "Select the treatment's screenshots for this locale + device."}
        </Text>
        <Button
          size="xs"
          leftSection={<IconUpload size={14} />}
          disabled={files.length === 0}
          loading={upload.isPending}
          onClick={submit}
        >
          Upload
        </Button>
      </Group>
    </Stack>
  );
}

/** Modal body: manage an experiment's treatments (create / delete / upload). */
function ManageTreatments({
  appId,
  experiment,
}: {
  appId: number;
  experiment: Experiment;
}) {
  const { data: treatments, isLoading } = useTreatments(appId, experiment.id);
  const createTreatment = useCreateTreatment(appId, experiment.id);
  const deleteTreatment = useDeleteTreatment(appId, experiment.id);
  const [name, setName] = useState("");
  const [appIconName, setAppIconName] = useState("");

  const atLimit = (treatments?.length ?? 0) >= MAX_TREATMENTS;
  const editable = DRAFT_STATES.has(experiment.state ?? "");

  const addTreatment = () => {
    if (!name.trim()) return;
    createTreatment.mutate(
      { name: name.trim(), app_icon_name: appIconName.trim() || null },
      {
        onSuccess: () => {
          setName("");
          setAppIconName("");
        },
      },
    );
  };

  if (isLoading) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    );
  }

  return (
    <Stack>
      {(treatments ?? []).map((t: Treatment) => (
        <Card key={t.id} withBorder padding="sm">
          <Group justify="space-between" mb="xs">
            <div>
              <Text fw={600}>{t.name ?? t.id}</Text>
              {t.app_icon_name && (
                <Text size="xs" c="dimmed">
                  Alt app icon: {t.app_icon_name}
                </Text>
              )}
            </div>
            <Button
              size="xs"
              variant="subtle"
              color="red"
              leftSection={<IconTrash size={14} />}
              loading={deleteTreatment.isPending}
              disabled={!editable}
              onClick={() => deleteTreatment.mutate(t.id)}
            >
              Delete
            </Button>
          </Group>
          <Divider mb="xs" />
          <TreatmentUploader
            appId={appId}
            experimentId={experiment.id}
            treatmentId={t.id}
          />
        </Card>
      ))}

      {(treatments?.length ?? 0) === 0 && (
        <Text c="dimmed" size="sm">
          No treatments yet. Add up to {MAX_TREATMENTS} variants to test against
          the original product page.
        </Text>
      )}

      <Divider label="Add a treatment" labelPosition="center" />
      <Group align="flex-end">
        <TextInput
          label="Treatment name"
          placeholder="e.g. Bright hero"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          style={{ flex: 1 }}
        />
        <TextInput
          label="Alt app icon name (optional)"
          placeholder="AppIcon-Alt1"
          value={appIconName}
          onChange={(e) => setAppIconName(e.currentTarget.value)}
          style={{ flex: 1 }}
        />
        <Button
          leftSection={<IconPlus size={16} />}
          onClick={addTreatment}
          loading={createTreatment.isPending}
          disabled={atLimit || !name.trim()}
        >
          Add
        </Button>
      </Group>
      {atLimit && (
        <Text size="xs" c="dimmed">
          Maximum of {MAX_TREATMENTS} treatments reached.
        </Text>
      )}
    </Stack>
  );
}

export default function ExperimentsPage() {
  const { id } = useParams<{ id: string }>();
  const appId = Number(id);
  const { data: app } = useApp(id ?? "");
  const { data: experiments, isLoading } = useExperiments(appId);

  const createExperiment = useCreateExperiment(appId);
  const updateExperiment = useUpdateExperiment(appId);
  const deleteExperiment = useDeleteExperiment(appId);

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTraffic, setNewTraffic] = useState<number>(50);
  const [managed, setManaged] = useState<Experiment | null>(null);

  const ascOptimizeUrl = app?.asc_app_id
    ? `https://appstoreconnect.apple.com/apps/${app.asc_app_id}/distribution/optimize`
    : "https://appstoreconnect.apple.com";

  const submitCreate = () => {
    if (!newName.trim()) return;
    createExperiment.mutate(
      { name: newName.trim(), traffic_proportion: newTraffic },
      {
        onSuccess: () => {
          setNewName("");
          setNewTraffic(50);
          setCreateOpen(false);
        },
      },
    );
  };

  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <div>
          <Title order={2}>
            <Group gap={8}>
              <IconFlask size={26} />
              Product Page Optimization
            </Group>
          </Title>
          <Text c="dimmed" size="sm">
            A/B test your product page — screenshots, app-preview videos, and
            app-icon variants.
          </Text>
        </div>
        <Button
          leftSection={<IconPlus size={16} />}
          onClick={() => setCreateOpen(true)}
        >
          New experiment
        </Button>
      </Group>

      <Alert
        icon={<IconAlertCircle size={16} />}
        color="blue"
        variant="light"
        mb="md"
      >
        Experiment <b>results</b> (impressions, conversion rate, confidence)
        aren&apos;t available through Apple&apos;s API — they live in App Store
        Connect&apos;s Analytics. This page manages the experiment setup and
        lifecycle;{" "}
        <Anchor href={ascOptimizeUrl} target="_blank" rel="noreferrer">
          view results in App Store Connect
          <IconExternalLink
            size={12}
            style={{ marginLeft: 4, verticalAlign: "middle" }}
          />
        </Anchor>
        .
      </Alert>

      {isLoading ? (
        <Center py="xl">
          <Loader />
        </Center>
      ) : (experiments?.length ?? 0) === 0 ? (
        <Card withBorder padding="xl">
          <Center>
            <Stack align="center" gap="xs">
              <IconFlask size={40} opacity={0.4} />
              <Text c="dimmed">No experiments yet.</Text>
              <Text c="dimmed" size="sm">
                Create one to start testing product-page variants. Apple allows
                one draft experiment per app at a time.
              </Text>
            </Stack>
          </Center>
        </Card>
      ) : (
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>State</Table.Th>
              <Table.Th>Traffic</Table.Th>
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(experiments ?? []).map((exp) => {
              const isDraft = DRAFT_STATES.has(exp.state ?? "");
              const isRunning = exp.state === "APPROVED";
              return (
                <Table.Tr key={exp.id}>
                  <Table.Td>{exp.name ?? exp.id}</Table.Td>
                  <Table.Td>
                    <Badge color={stateColor(exp.state)} variant="light">
                      {exp.state ?? "—"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {exp.traffic_proportion != null
                      ? `${exp.traffic_proportion}%`
                      : "—"}
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <Button
                        size="xs"
                        variant="light"
                        onClick={() => setManaged(exp)}
                      >
                        Treatments
                      </Button>
                      {isDraft && (
                        <Button
                          size="xs"
                          variant="light"
                          color="yellow"
                          loading={updateExperiment.isPending}
                          onClick={() =>
                            updateExperiment.mutate({
                              experimentId: exp.id,
                              body: { state: "WAITING_FOR_REVIEW" },
                            })
                          }
                        >
                          Submit
                        </Button>
                      )}
                      {isRunning && (
                        <Button
                          size="xs"
                          variant="light"
                          color="gray"
                          loading={updateExperiment.isPending}
                          onClick={() =>
                            updateExperiment.mutate({
                              experimentId: exp.id,
                              body: { state: "STOPPED" },
                            })
                          }
                        >
                          Stop
                        </Button>
                      )}
                      {isDraft && (
                        <Button
                          size="xs"
                          variant="subtle"
                          color="red"
                          leftSection={<IconTrash size={14} />}
                          loading={deleteExperiment.isPending}
                          onClick={() => deleteExperiment.mutate(exp.id)}
                        >
                          Delete
                        </Button>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}

      {/* Create experiment modal */}
      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title="New experiment"
      >
        <Stack>
          <TextInput
            label="Experiment name"
            placeholder="e.g. Hero screenshot test"
            value={newName}
            onChange={(e) => setNewName(e.currentTarget.value)}
            required
          />
          <NumberInput
            label="Traffic proportion (%)"
            description="Percentage of your app's traffic entered into the test."
            value={newTraffic}
            onChange={(v) => setNewTraffic(typeof v === "number" ? v : 50)}
            min={1}
            max={100}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={submitCreate}
              loading={createExperiment.isPending}
              disabled={!newName.trim()}
            >
              Create
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Manage treatments modal */}
      <Modal
        opened={managed !== null}
        onClose={() => setManaged(null)}
        title={managed ? `Treatments — ${managed.name ?? managed.id}` : ""}
        size="lg"
      >
        {managed && <ManageTreatments appId={appId} experiment={managed} />}
      </Modal>
    </Container>
  );
}
