import { useState } from "react";
import {
  Title,
  Text,
  Container,
  Paper,
  Stack,
  Group,
  Badge,
  Button,
  Table,
  Skeleton,
  TextInput,
  Textarea,
  Modal,
  Code,
  CopyButton,
  ActionIcon,
  Alert,
} from "@mantine/core";
import {
  IconRefresh,
  IconDatabase,
  IconKey,
  IconCopy,
  IconCheck,
  IconTrash,
  IconAlertTriangle,
  IconPlus,
  IconCloudUpload,
  IconTargetArrow,
} from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import {
  useIndexStatus,
  useRefreshIndices,
  usePersonalAccessTokens,
  useCreatePersonalAccessToken,
  useRevokePersonalAccessToken,
  useASACredentials,
  useCreateASACredential,
  useDeleteASACredential,
  useTestASACredential,
  useASASync,
} from "@/lib/hooks";
import type { ASACredentialOut } from "@/lib/hooks";

const INDEX_LABELS: Record<string, string> = {
  ppp: "Purchasing Power Parity (PPP)",
  bigmac: "Big Mac Index",
  netflix: "Netflix Index",
  spotify: "Spotify Index",
};

function getIndexFreshness(lastRefresh: string | null): {
  color: string;
  label: string;
} {
  if (!lastRefresh) {
    return { color: "red", label: "Never" };
  }

  const refreshDate = new Date(lastRefresh);
  const daysSince = Math.floor(
    (Date.now() - refreshDate.getTime()) / (1000 * 60 * 60 * 24),
  );

  if (daysSince <= 30) {
    return { color: "green", label: "Fresh" };
  }
  if (daysSince <= 90) {
    return { color: "yellow", label: "Stale" };
  }
  return { color: "red", label: "Outdated" };
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "Never refreshed";
  return new Date(dateStr).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatRelative(dateStr: string | null): string {
  if (!dateStr) return "Never";
  const then = new Date(dateStr).getTime();
  const diffMs = Date.now() - then;
  if (diffMs < 0) return "just now";
  const min = Math.floor(diffMs / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  const y = Math.floor(mo / 12);
  return `${y}y ago`;
}

function ASACredentialsSection() {
  const { data: creds, isLoading } = useASACredentials();
  const createMutation = useCreateASACredential();
  const deleteMutation = useDeleteASACredential();
  const testMutation = useTestASACredential();
  const syncMutation = useASASync();

  const [opened, setOpened] = useState(false);
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [teamId, setTeamId] = useState("");
  const [keyId, setKeyId] = useState("");
  const [pem, setPem] = useState("");

  const closeAndReset = () => {
    setOpened(false);
    setName("");
    setClientId("");
    setTeamId("");
    setKeyId("");
    setPem("");
  };

  const handleSubmit = async () => {
    if (
      !name.trim() ||
      !clientId.trim() ||
      !teamId.trim() ||
      !keyId.trim() ||
      !pem.trim()
    ) {
      notifications.show({
        title: "Missing fields",
        message: "All fields are required.",
        color: "yellow",
      });
      return;
    }
    try {
      await createMutation.mutateAsync({
        name: name.trim(),
        client_id: clientId.trim(),
        team_id: teamId.trim(),
        key_id: keyId.trim(),
        private_key_pem: pem,
      });
      closeAndReset();
    } catch {
      // notifications already shown by hook
    }
  };

  const handleTest = async (cred: ASACredentialOut) => {
    try {
      const result = await testMutation.mutateAsync(cred.id);
      if (result.ok) {
        notifications.show({
          title: "ASA credential ok",
          message: `${result.orgs_visible} org${result.orgs_visible === 1 ? "" : "s"} visible.`,
          color: "green",
        });
      } else {
        notifications.show({
          title: "ASA credential failed",
          message: result.detail ?? "Apple rejected the credential.",
          color: "red",
        });
      }
    } catch {
      notifications.show({
        title: "ASA credential test failed",
        message: "Could not reach Apple.",
        color: "red",
      });
    }
  };

  const handleSync = (cred: ASACredentialOut) => {
    syncMutation.mutate({
      credential_id: cred.id,
      full: cred.last_synced_at === null,
    });
  };

  const handleDelete = (cred: ASACredentialOut) => {
    if (
      !confirm(
        `Delete ASA credential "${cred.name}"? This will also remove all linked orgs, campaigns, and ad groups.`,
      )
    ) {
      return;
    }
    deleteMutation.mutate(cred.id);
  };

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" p="md" pb={0}>
        <Group gap="xs">
          <IconTargetArrow size={20} color="var(--mantine-color-blue-6)" />
          <Title order={4}>ASA Credentials</Title>
        </Group>
        <Button
          size="sm"
          leftSection={<IconPlus size={16} />}
          onClick={() => setOpened(true)}
        >
          Connect ASA
        </Button>
      </Group>
      <Text c="dimmed" size="sm" px="md" mt={4} mb="md">
        Connect Apple Search Ads to pull paid campaigns, ad groups, keywords,
        negatives, and search-term reports. Private keys are stored encrypted
        and never shown again after upload.
      </Text>

      {isLoading ? (
        <Stack p="md" pt={0}>
          <Skeleton height={36} />
          <Skeleton height={36} />
        </Stack>
      ) : creds && creds.length > 0 ? (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Key ID</Table.Th>
              <Table.Th>Last sync</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {creds.map((cred) => (
              <Table.Tr key={cred.id}>
                <Table.Td>
                  <Text fw={500} size="sm">
                    {cred.name}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="xs" c="dimmed" ff="monospace">
                    {cred.key_id}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {formatRelative(cred.last_synced_at)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {cred.last_synced_at ? (
                    <Badge variant="light" color="green" size="sm">
                      Synced
                    </Badge>
                  ) : (
                    <Badge variant="light" color="yellow" size="sm">
                      Unsynced
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end">
                    <Button
                      size="xs"
                      variant="subtle"
                      onClick={() => handleTest(cred)}
                      loading={
                        testMutation.isPending &&
                        testMutation.variables === cred.id
                      }
                    >
                      Test
                    </Button>
                    <Button
                      size="xs"
                      variant="light"
                      leftSection={<IconCloudUpload size={14} />}
                      onClick={() => handleSync(cred)}
                      loading={
                        syncMutation.isPending &&
                        syncMutation.variables?.credential_id === cred.id
                      }
                    >
                      Sync
                    </Button>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      onClick={() => handleDelete(cred)}
                      loading={
                        deleteMutation.isPending &&
                        deleteMutation.variables === cred.id
                      }
                      aria-label="Delete ASA credential"
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : (
        <Text c="dimmed" size="sm" p="md" pt={0} ta="center">
          No ASA credentials connected yet.
        </Text>
      )}

      <Modal
        opened={opened}
        onClose={closeAndReset}
        title="Connect Apple Search Ads"
        size="lg"
        centered
      >
        <Stack>
          <Alert
            icon={<IconAlertTriangle size={18} />}
            color="blue"
            variant="light"
          >
            Generate an API key in Apple Search Ads → Account Settings → API.
            You'll get a Client ID, Team ID, Key ID, and a downloadable .pem
            file. Paste the .pem contents below — it is stored encrypted and
            never shown again.
          </Alert>

          <TextInput
            label="Name"
            placeholder="e.g. acme-asa"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            required
          />
          <TextInput
            label="Client ID"
            placeholder="SEARCHADS.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            value={clientId}
            onChange={(e) => setClientId(e.currentTarget.value)}
            required
          />
          <TextInput
            label="Team ID"
            placeholder="SEARCHADS.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            value={teamId}
            onChange={(e) => setTeamId(e.currentTarget.value)}
            required
          />
          <TextInput
            label="Key ID"
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            value={keyId}
            onChange={(e) => setKeyId(e.currentTarget.value)}
            required
          />
          <Textarea
            label="Private key (.pem)"
            placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
            value={pem}
            onChange={(e) => setPem(e.currentTarget.value)}
            minRows={6}
            autosize
            maxRows={12}
            required
            styles={{ input: { fontFamily: "monospace", fontSize: 12 } }}
          />

          <Group justify="flex-end">
            <Button variant="default" onClick={closeAndReset}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleSubmit()}
              loading={createMutation.isPending}
            >
              Connect
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Paper>
  );
}

function EconomicIndicesSection() {
  const { data: status, isLoading } = useIndexStatus();
  const refreshMutation = useRefreshIndices();

  if (isLoading) {
    return (
      <Paper withBorder p="md" radius="md">
        <Stack>
          <Skeleton height={24} width={200} />
          <Skeleton height={40} />
          <Skeleton height={40} />
          <Skeleton height={40} />
          <Skeleton height={40} />
        </Stack>
      </Paper>
    );
  }

  const indexEntries = Object.entries(status ?? {});

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" p="md" pb={0}>
        <Group gap="xs">
          <IconDatabase size={20} color="var(--mantine-color-blue-6)" />
          <Title order={4}>Economic Indices</Title>
        </Group>
        <Button
          leftSection={<IconRefresh size={16} />}
          onClick={() => refreshMutation.mutate()}
          loading={refreshMutation.isPending}
          size="sm"
        >
          Refresh All Indices
        </Button>
      </Group>
      <Text c="dimmed" size="sm" px="md" mt={4} mb="md">
        Economic indices used for calculating territory-specific pricing.
      </Text>

      {indexEntries.length === 0 ? (
        <Text c="dimmed" size="sm" p="md" ta="center">
          No indices configured. Click "Refresh All Indices" to populate data.
        </Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Index</Table.Th>
              <Table.Th>Entries</Table.Th>
              <Table.Th>Last Refresh</Table.Th>
              <Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {indexEntries.map(([key, info]) => {
              const freshness = getIndexFreshness(info.last_refresh);
              return (
                <Table.Tr key={key}>
                  <Table.Td>
                    <Text fw={500} size="sm">
                      {INDEX_LABELS[key] ?? key}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="gray" size="sm">
                      {info.count}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {formatDate(info.last_refresh)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={freshness.color} size="sm">
                      {freshness.label}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
    </Paper>
  );
}

function PersonalAccessTokensSection() {
  const { data: tokens, isLoading } = usePersonalAccessTokens();
  const createMutation = useCreatePersonalAccessToken();
  const revokeMutation = useRevokePersonalAccessToken();
  const [name, setName] = useState("");
  const [issuedToken, setIssuedToken] = useState<{
    token: string;
    name: string;
  } | null>(null);

  const handleIssue = async () => {
    if (!name.trim()) {
      notifications.show({
        title: "Name required",
        message: "Give the token a label so you remember where it's used.",
        color: "yellow",
      });
      return;
    }
    const result = await createMutation.mutateAsync(name.trim());
    setIssuedToken({ token: result.token, name: result.name });
    setName("");
  };

  return (
    <Paper withBorder radius="md">
      <Group justify="space-between" p="md" pb={0}>
        <Group gap="xs">
          <IconKey size={20} color="var(--mantine-color-blue-6)" />
          <Title order={4}>Personal Access Tokens</Title>
        </Group>
      </Group>
      <Text c="dimmed" size="sm" px="md" mt={4} mb="md">
        Long-lived bearer tokens for headless clients (Claude Desktop, OpenAI MCP,
        custom agents). Each token grants the same write access as your account —
        treat them like passwords.
      </Text>

      <Group p="md" pt={0} align="flex-end" gap="sm">
        <TextInput
          flex={1}
          label="Token name"
          placeholder="e.g. claude-desktop, ci-bot, my-laptop"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleIssue();
          }}
        />
        <Button
          onClick={() => void handleIssue()}
          loading={createMutation.isPending}
          leftSection={<IconKey size={16} />}
        >
          Issue token
        </Button>
      </Group>

      {isLoading ? (
        <Stack p="md" pt={0}>
          <Skeleton height={36} />
          <Skeleton height={36} />
        </Stack>
      ) : tokens && tokens.length > 0 ? (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Created</Table.Th>
              <Table.Th>Last used</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {tokens.map((t) => (
              <Table.Tr key={t.id}>
                <Table.Td>
                  <Text fw={500} size="sm">
                    {t.name}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {new Date(t.created_at).toLocaleDateString()}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {t.last_used_at
                      ? new Date(t.last_used_at).toLocaleDateString()
                      : "Never"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {t.revoked_at ? (
                    <Badge variant="light" color="gray" size="sm">
                      Revoked
                    </Badge>
                  ) : (
                    <Badge variant="light" color="green" size="sm">
                      Active
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  {!t.revoked_at && (
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      onClick={() => revokeMutation.mutate(t.id)}
                      loading={revokeMutation.isPending}
                      aria-label="Revoke token"
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : (
        <Text c="dimmed" size="sm" p="md" pt={0} ta="center">
          No tokens issued yet.
        </Text>
      )}

      <Modal
        opened={issuedToken !== null}
        onClose={() => setIssuedToken(null)}
        title="Token issued"
        size="lg"
        centered
      >
        {issuedToken && (
          <Stack>
            <Alert
              icon={<IconAlertTriangle size={18} />}
              color="yellow"
              title="Copy this token now"
            >
              This is the only time the plaintext token will be shown. After you
              close this dialog, only the hash is stored and the token cannot be
              recovered.
            </Alert>

            <Text size="sm" fw={500}>
              {issuedToken.name}
            </Text>

            <Group gap="xs">
              <Code block style={{ flex: 1, wordBreak: "break-all" }}>
                {issuedToken.token}
              </Code>
              <CopyButton value={issuedToken.token}>
                {({ copied, copy }) => (
                  <ActionIcon
                    variant="light"
                    color={copied ? "green" : "blue"}
                    onClick={copy}
                    aria-label="Copy token"
                  >
                    {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                  </ActionIcon>
                )}
              </CopyButton>
            </Group>

            <Text size="sm" c="dimmed">
              Add to your MCP client config (e.g. Claude Desktop):
            </Text>
            <Code block>
              {`{
  "mcpServers": {
    "aso-light": {
      "url": "http://localhost:8000/mcp/",
      "headers": { "Authorization": "Bearer ${issuedToken.token}" }
    }
  }
}`}
            </Code>
          </Stack>
        )}
      </Modal>
    </Paper>
  );
}

export default function SettingsPage() {
  return (
    <Container size="lg">
      <Title order={2} mb="md">
        Settings
      </Title>
      <Text c="dimmed" mb="lg">
        Application settings and preferences.
      </Text>

      <Stack gap="lg">
        <PersonalAccessTokensSection />
        <ASACredentialsSection />
        <EconomicIndicesSection />
      </Stack>
    </Container>
  );
}
