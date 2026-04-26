import { useState } from "react";
import {
  Container,
  Title,
  Text,
  Button,
  Group,
  Table,
  Modal,
  TextInput,
  FileInput,
  Stack,
  ActionIcon,
  Badge,
  Paper,
  Skeleton,
  Tooltip,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { useDisclosure } from "@mantine/hooks";
import {
  IconPlus,
  IconTrash,
  IconPlugConnected,
  IconKey,
  IconUpload,
  IconAlertCircle,
} from "@tabler/icons-react";
import {
  useCredentials,
  useCreateCredential,
  useDeleteCredential,
  useTestCredential,
} from "@/lib/hooks";

interface CredentialFormValues {
  name: string;
  issuer_id: string;
  key_id: string;
  private_key_file: File | null;
}

export default function CredentialsPage() {
  const [modalOpened, { open: openModal, close: closeModal }] =
    useDisclosure(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const { data: credentials, isLoading } = useCredentials();
  const createMutation = useCreateCredential();
  const deleteMutation = useDeleteCredential();
  const testMutation = useTestCredential();

  const form = useForm<CredentialFormValues>({
    initialValues: {
      name: "",
      issuer_id: "",
      key_id: "",
      private_key_file: null,
    },
    validate: {
      name: (value) =>
        value.trim().length > 0 ? null : "Name is required",
      issuer_id: (value) =>
        value.trim().length > 0 ? null : "Issuer ID is required",
      key_id: (value) =>
        value.trim().length > 0 ? null : "Key ID is required",
      private_key_file: (value) =>
        value ? null : "Private key file is required",
    },
  });

  const handleCreate = async (values: CredentialFormValues) => {
    if (!values.private_key_file) return;

    await createMutation.mutateAsync({
      name: values.name,
      issuer_id: values.issuer_id,
      key_id: values.key_id,
      private_key_file: values.private_key_file,
    });

    form.reset();
    closeModal();
  };

  const handleDelete = async (id: number) => {
    await deleteMutation.mutateAsync(id);
    setDeleteId(null);
  };

  const handleTest = (id: number) => {
    testMutation.mutate(id);
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  };

  return (
    <Container size="lg">
      <Group justify="space-between" mb="lg">
        <div>
          <Title order={2}>ASC Credentials</Title>
          <Text c="dimmed" size="sm" mt={4}>
            Manage your App Store Connect API credentials.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={openModal}>
          Add Credential
        </Button>
      </Group>

      {isLoading ? (
        <Paper withBorder p="md">
          <Stack>
            <Skeleton height={40} />
            <Skeleton height={40} />
            <Skeleton height={40} />
          </Stack>
        </Paper>
      ) : !credentials || credentials.length === 0 ? (
        <Paper withBorder p="xl" ta="center">
          <Stack align="center" gap="sm">
            <IconKey size={48} color="var(--mantine-color-dimmed)" />
            <Title order={4} c="dimmed">
              No credentials yet
            </Title>
            <Text c="dimmed" size="sm">
              Add your App Store Connect API key to get started.
            </Text>
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={openModal}
              mt="sm"
            >
              Add Credential
            </Button>
          </Stack>
        </Paper>
      ) : (
        <Paper withBorder>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Issuer ID</Table.Th>
                <Table.Th>Key ID</Table.Th>
                <Table.Th>Created</Table.Th>
                <Table.Th ta="right">Actions</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {credentials.map((cred) => (
                <Table.Tr key={cred.id}>
                  <Table.Td>
                    <Text fw={500}>{cred.name}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="gray" size="sm">
                      {cred.issuer_id}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="gray" size="sm">
                      {cred.key_id}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {formatDate(cred.created_at)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs" justify="flex-end">
                      <Tooltip label="Test connection">
                        <ActionIcon
                          variant="light"
                          color="blue"
                          onClick={() => handleTest(cred.id)}
                          loading={
                            testMutation.isPending &&
                            testMutation.variables === cred.id
                          }
                        >
                          <IconPlugConnected size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Delete credential">
                        <ActionIcon
                          variant="light"
                          color="red"
                          onClick={() => setDeleteId(cred.id)}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      )}

      {/* Add Credential Modal */}
      <Modal
        opened={modalOpened}
        onClose={() => {
          closeModal();
          form.reset();
        }}
        title="Add ASC Credential"
        size="md"
      >
        <form onSubmit={form.onSubmit(handleCreate)}>
          <Stack>
            <TextInput
              label="Name"
              placeholder="My ASC Key"
              description="A friendly name for this credential"
              withAsterisk
              {...form.getInputProps("name")}
            />
            <TextInput
              label="Issuer ID"
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              description="Found in App Store Connect > Users and Access > Integrations > Keys"
              withAsterisk
              {...form.getInputProps("issuer_id")}
            />
            <TextInput
              label="Key ID"
              placeholder="XXXXXXXXXX"
              description="The Key ID from the generated API key"
              withAsterisk
              {...form.getInputProps("key_id")}
            />
            <FileInput
              label="Private Key"
              placeholder="Select .p8 file"
              description="The AuthKey .p8 file downloaded from App Store Connect"
              accept=".p8"
              withAsterisk
              leftSection={<IconUpload size={16} />}
              {...form.getInputProps("private_key_file")}
            />
            <Group justify="flex-end" mt="md">
              <Button
                variant="subtle"
                onClick={() => {
                  closeModal();
                  form.reset();
                }}
              >
                Cancel
              </Button>
              <Button type="submit" loading={createMutation.isPending}>
                Add Credential
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        opened={deleteId !== null}
        onClose={() => setDeleteId(null)}
        title="Delete Credential"
        size="sm"
      >
        <Stack>
          <Group gap="xs">
            <IconAlertCircle size={20} color="var(--mantine-color-red-6)" />
            <Text size="sm">
              Are you sure you want to delete this credential? This action
              cannot be undone.
            </Text>
          </Group>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setDeleteId(null)}>
              Cancel
            </Button>
            <Button
              color="red"
              loading={deleteMutation.isPending}
              onClick={() => deleteId && handleDelete(deleteId)}
            >
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Container>
  );
}
