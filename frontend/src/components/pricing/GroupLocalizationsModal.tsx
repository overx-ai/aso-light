import { useEffect, useState } from "react";
import {
  Modal,
  Stack,
  Group,
  Button,
  Table,
  TextInput,
  Text,
  Skeleton,
  ActionIcon,
} from "@mantine/core";
import { IconCheck, IconPencil, IconX } from "@tabler/icons-react";
import {
  useGroupLocalizations,
  useCreateGroupLocalization,
  useUpdateGroupLocalization,
} from "@/lib/hooks";
import type { GroupLocalization, SubscriptionGroup } from "@/types";

interface Props {
  appId: string;
  group: SubscriptionGroup | null;
  opened: boolean;
  onClose: () => void;
}

export default function GroupLocalizationsModal({
  appId,
  group,
  opened,
  onClose,
}: Props) {
  const groupId = group ? String(group.id) : "";
  const { data: localizations = [], isLoading } = useGroupLocalizations(
    appId,
    opened ? groupId : "",
  );
  const createMutation = useCreateGroupLocalization(appId, groupId);
  const updateMutation = useUpdateGroupLocalization(appId, groupId);

  const [newLocale, setNewLocale] = useState("");
  const [newName, setNewName] = useState("");
  const [newCustomAppName, setNewCustomAppName] = useState("");

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editCustomAppName, setEditCustomAppName] = useState("");

  // Reset all draft inputs when the modal closes or the group changes so
  // a stale draft from one group doesn't bleed into another.
  useEffect(() => {
    if (!opened) return;
    setNewLocale("");
    setNewName("");
    setNewCustomAppName("");
    setEditingId(null);
    setEditName("");
    setEditCustomAppName("");
  }, [opened, groupId]);

  const handleAdd = () => {
    if (!newLocale.trim() || !newName.trim()) return;
    createMutation.mutate(
      {
        locale: newLocale.trim(),
        name: newName.trim(),
        custom_app_name: newCustomAppName.trim() || null,
      },
      {
        onSuccess: () => {
          setNewLocale("");
          setNewName("");
          setNewCustomAppName("");
        },
      },
    );
  };

  const startEdit = (loc: GroupLocalization) => {
    setEditingId(loc.id);
    setEditName(loc.name);
    setEditCustomAppName(loc.custom_app_name ?? "");
  };

  const cancelEdit = () => setEditingId(null);

  const saveEdit = (id: string) => {
    if (!editName.trim()) return;
    updateMutation.mutate(
      {
        localizationId: id,
        body: {
          name: editName.trim(),
          custom_app_name: editCustomAppName.trim() || null,
        },
      },
      { onSuccess: () => setEditingId(null) },
    );
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={`Group localizations${group ? ` — ${group.name}` : ""}`}
      size="lg"
      centered
    >
      <Stack gap="md">
        <Stack gap="xs">
          <Text size="sm" fw={500}>
            Existing localizations
          </Text>
          {isLoading ? (
            <Skeleton height={120} />
          ) : localizations.length === 0 ? (
            <Text c="dimmed" size="sm">
              No localizations yet.
            </Text>
          ) : (
            <Table withTableBorder striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Locale</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Custom app name</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {localizations.map((loc) =>
                  editingId === loc.id ? (
                    <Table.Tr key={loc.id}>
                      <Table.Td>{loc.locale}</Table.Td>
                      <Table.Td>
                        <TextInput
                          value={editName}
                          onChange={(e) => setEditName(e.currentTarget.value)}
                          maxLength={30}
                          size="xs"
                        />
                      </Table.Td>
                      <Table.Td>
                        <TextInput
                          value={editCustomAppName}
                          onChange={(e) =>
                            setEditCustomAppName(e.currentTarget.value)
                          }
                          size="xs"
                        />
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          <ActionIcon
                            variant="subtle"
                            color="green"
                            onClick={() => saveEdit(loc.id)}
                            loading={updateMutation.isPending}
                          >
                            <IconCheck size={14} />
                          </ActionIcon>
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            onClick={cancelEdit}
                          >
                            <IconX size={14} />
                          </ActionIcon>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ) : (
                    <Table.Tr key={loc.id}>
                      <Table.Td>{loc.locale}</Table.Td>
                      <Table.Td>{loc.name}</Table.Td>
                      <Table.Td>{loc.custom_app_name ?? "—"}</Table.Td>
                      <Table.Td>
                        <ActionIcon
                          variant="subtle"
                          onClick={() => startEdit(loc)}
                        >
                          <IconPencil size={14} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ),
                )}
              </Table.Tbody>
            </Table>
          )}
        </Stack>

        <Stack gap="xs">
          <Text size="sm" fw={500}>
            Add new localization
          </Text>
          <Group grow>
            <TextInput
              label="Locale"
              placeholder="en-US"
              value={newLocale}
              onChange={(e) => setNewLocale(e.currentTarget.value)}
              size="xs"
            />
            <TextInput
              label="Name"
              placeholder="My subscription"
              value={newName}
              onChange={(e) => setNewName(e.currentTarget.value)}
              maxLength={30}
              size="xs"
            />
            <TextInput
              label="Custom app name (optional)"
              value={newCustomAppName}
              onChange={(e) => setNewCustomAppName(e.currentTarget.value)}
              size="xs"
            />
          </Group>
          <Group justify="flex-end">
            <Button
              size="xs"
              onClick={handleAdd}
              loading={createMutation.isPending}
              disabled={!newLocale.trim() || !newName.trim()}
            >
              Add localization
            </Button>
          </Group>
        </Stack>
      </Stack>
    </Modal>
  );
}
