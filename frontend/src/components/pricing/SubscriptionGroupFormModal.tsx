import { useEffect, useState } from "react";
import { Modal, Stack, TextInput, Button, Group } from "@mantine/core";
import {
  useCreateSubscriptionGroup,
  useUpdateSubscriptionGroup,
} from "@/lib/hooks";
import type { SubscriptionGroup } from "@/types";

interface Props {
  appId: string;
  group: SubscriptionGroup | null;
  opened: boolean;
  onClose: () => void;
}

export default function SubscriptionGroupFormModal({
  appId,
  group,
  opened,
  onClose,
}: Props) {
  const [referenceName, setReferenceName] = useState("");
  const createMutation = useCreateSubscriptionGroup(appId);
  const updateMutation = useUpdateSubscriptionGroup(appId);
  const isEdit = group !== null;
  const isPending = createMutation.isPending || updateMutation.isPending;

  useEffect(() => {
    if (opened) setReferenceName(group?.name ?? "");
  }, [opened, group]);

  const handleSubmit = () => {
    const name = referenceName.trim();
    if (!name) return;
    if (isEdit) {
      updateMutation.mutate(
        { groupId: String(group!.id), body: { reference_name: name } },
        { onSuccess: onClose },
      );
    } else {
      createMutation.mutate(
        { reference_name: name },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={isEdit ? "Rename subscription group" : "New subscription group"}
      centered
    >
      <Stack gap="md">
        <TextInput
          label="Reference name"
          description="Internal name shown only inside App Store Connect."
          placeholder="My Subscription Group"
          value={referenceName}
          onChange={(e) => setReferenceName(e.currentTarget.value)}
          maxLength={64}
          autoFocus
          required
        />
        <Group justify="flex-end" gap="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            loading={isPending}
            disabled={!referenceName.trim()}
          >
            {isEdit ? "Save" : "Create"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
