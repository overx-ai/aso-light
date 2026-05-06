import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  Container,
  Title,
  Text,
  Stack,
  Paper,
  TextInput,
  PasswordInput,
  Button,
  Group,
  Alert,
  Select,
  Skeleton,
  Anchor,
  Badge,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconCheck,
  IconExternalLink,
  IconKey,
  IconTrash,
} from "@tabler/icons-react";
import {
  useRevenueCatCredential,
  useSaveRevenueCatCredential,
  useDeleteRevenueCatCredential,
  useTestRevenueCatCredential,
  useRevenueCatApps,
} from "@/lib/hooks";

export default function RevenueCatSettingsPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ?? "";
  const credQuery = useRevenueCatCredential(appId);
  const saveMutation = useSaveRevenueCatCredential();
  const deleteMutation = useDeleteRevenueCatCredential();
  const testMutation = useTestRevenueCatCredential();
  const appsQuery = useRevenueCatApps(appId);

  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [rcAppId, setRcAppId] = useState<string | null>(null);
  const [secretKey, setSecretKey] = useState("");

  useEffect(() => {
    if (credQuery.data) {
      setName(credQuery.data.name);
      setProjectId(credQuery.data.project_id);
      setRcAppId(credQuery.data.rc_app_id ?? null);
    }
  }, [credQuery.data]);

  const handleSave = () => {
    if (!projectId || !secretKey) return;
    saveMutation.mutate(
      {
        appId,
        body: {
          name: name || projectId,
          project_id: projectId,
          rc_app_id: rcAppId,
          secret_key: secretKey,
        },
      },
      { onSuccess: () => setSecretKey("") },
    );
  };

  const handleTest = () => testMutation.mutate({ appId });

  const handleDisconnect = () => {
    if (!confirm("Disconnect RevenueCat from this app?")) return;
    deleteMutation.mutate(
      { appId },
      {
        onSuccess: () => {
          setName("");
          setProjectId("");
          setRcAppId(null);
          setSecretKey("");
        },
      },
    );
  };

  const isConnected = !!credQuery.data;
  const apps = appsQuery.data ?? [];

  return (
    <Container size="md" py="md">
      <Stack gap="lg">
        <Group justify="space-between" align="flex-end">
          <div>
            <Title order={2}>RevenueCat</Title>
            <Text c="dimmed" size="sm">
              Connect this app to a RevenueCat project so version-bump
              clones can swap entitlements automatically.
            </Text>
          </div>
          {isConnected ? (
            <Badge size="lg" color="green" variant="light">
              Connected
            </Badge>
          ) : (
            <Badge size="lg" color="gray" variant="light">
              Not connected
            </Badge>
          )}
        </Group>

        {isConnected ? (
          <Anchor
            component={Link}
            to={`/apps/${appId}/revenuecat/entitlements`}
            size="sm"
          >
            Manage entitlements, offerings & packages →
          </Anchor>
        ) : null}

        <Paper withBorder p="md" radius="md">
          <Stack gap="md">
            {credQuery.isLoading ? (
              <Skeleton height={200} />
            ) : (
              <>
                <TextInput
                  label="Display name"
                  placeholder="e.g. Production project"
                  value={name}
                  onChange={(e) => setName(e.currentTarget.value)}
                />
                <TextInput
                  label="Project ID"
                  description="Find under RevenueCat dashboard → Project settings."
                  placeholder="proj_xxx"
                  value={projectId}
                  onChange={(e) => setProjectId(e.currentTarget.value)}
                  required
                />
                <PasswordInput
                  label="Secret API key"
                  description={
                    isConnected
                      ? "Leave blank to keep the existing key. Otherwise enter a new v2 secret key."
                      : "Generate under RevenueCat → API keys → V2 (secret)."
                  }
                  placeholder={isConnected ? "•••••••• (unchanged)" : "sk_..."}
                  leftSection={<IconKey size={16} />}
                  value={secretKey}
                  onChange={(e) => setSecretKey(e.currentTarget.value)}
                  required={!isConnected}
                />
                {apps.length > 0 ? (
                  <Select
                    label="RevenueCat App"
                    description="The store-app this project tracks (matches your bundle id on RC's side)."
                    data={apps.map((a) => ({
                      value: a.id,
                      label: a.name ? `${a.name} (${a.id})` : a.id,
                    }))}
                    value={rcAppId}
                    onChange={setRcAppId}
                    clearable
                  />
                ) : isConnected ? (
                  <TextInput
                    label="RevenueCat App ID"
                    description="Copy from RC → Project → Apps."
                    value={rcAppId ?? ""}
                    onChange={(e) =>
                      setRcAppId(e.currentTarget.value || null)
                    }
                  />
                ) : null}

                {testMutation.data ? (
                  <Alert
                    icon={
                      testMutation.data.success ? (
                        <IconCheck size={16} />
                      ) : (
                        <IconAlertCircle size={16} />
                      )
                    }
                    color={testMutation.data.success ? "green" : "red"}
                    variant="light"
                  >
                    {testMutation.data.message}
                  </Alert>
                ) : null}

                <Group justify="space-between">
                  <Group gap="xs">
                    <Button
                      onClick={handleSave}
                      loading={saveMutation.isPending}
                      disabled={!projectId || (!isConnected && !secretKey)}
                    >
                      {isConnected ? "Save changes" : "Connect"}
                    </Button>
                    <Button
                      variant="subtle"
                      onClick={handleTest}
                      loading={testMutation.isPending}
                      disabled={!isConnected}
                      leftSection={<IconExternalLink size={16} />}
                    >
                      Test connection
                    </Button>
                  </Group>
                  {isConnected ? (
                    <Button
                      color="red"
                      variant="subtle"
                      leftSection={<IconTrash size={16} />}
                      onClick={handleDisconnect}
                      loading={deleteMutation.isPending}
                    >
                      Disconnect
                    </Button>
                  ) : null}
                </Group>
              </>
            )}
          </Stack>
        </Paper>
      </Stack>
    </Container>
  );
}
