import { useEffect, useState } from "react";
import {
  Modal,
  Stack,
  TextInput,
  Select,
  NumberInput,
  Switch,
  Textarea,
  Group,
  Button,
} from "@mantine/core";
import {
  useCreateSubscription,
  useUpdateSubscription,
} from "@/lib/hooks";
import { SUBSCRIPTION_PERIOD_OPTIONS } from "@/components/pricing/subscriptionConstants";
import type {
  Subscription,
  SubscriptionGroup,
  SubscriptionPeriod,
} from "@/types";

interface Props {
  appId: string;
  group: SubscriptionGroup | null;
  subscription: Subscription | null;
  opened: boolean;
  onClose: () => void;
}

export default function SubscriptionFormModal({
  appId,
  group,
  subscription,
  opened,
  onClose,
}: Props) {
  const isEdit = subscription !== null;
  const groupId = group ? String(group.id) : "";
  const createMutation = useCreateSubscription(appId, groupId);
  const updateMutation = useUpdateSubscription(appId);
  const isPending = createMutation.isPending || updateMutation.isPending;

  const [productId, setProductId] = useState("");
  const [name, setName] = useState("");
  const [period, setPeriod] = useState<SubscriptionPeriod>("ONE_MONTH");
  const [familySharable, setFamilySharable] = useState(false);
  const [groupLevel, setGroupLevel] = useState<number>(1);
  const [reviewNote, setReviewNote] = useState("");

  useEffect(() => {
    if (!opened) return;
    setProductId(subscription?.product_id ?? "");
    setName(subscription?.name ?? "");
    setPeriod("ONE_MONTH");
    setFamilySharable(false);
    setGroupLevel(1);
    setReviewNote("");
  }, [opened, subscription]);

  const handleSubmit = () => {
    if (!name.trim()) return;
    if (isEdit) {
      // We don't have the existing group_level / family_sharable /
      // review_note in our DB cache, so the form fields below open as
      // defaults rather than the real ASC values. Sending them on a
      // simple rename would clobber whatever the user has set in App
      // Store Connect — so on edit we PATCH only ``name``. Until we
      // fetch the live attributes, the other fields are intentionally
      // hidden (see the JSX below) so they can't be silently changed.
      updateMutation.mutate(
        {
          subId: String(subscription!.id),
          body: { name: name.trim() },
        },
        { onSuccess: onClose },
      );
    } else {
      if (!productId.trim()) return;
      createMutation.mutate(
        {
          product_id: productId.trim(),
          name: name.trim(),
          period,
          family_sharable: familySharable,
          available_in_all_territories: true,
          group_level: groupLevel,
          review_note: reviewNote.trim() || null,
        },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={isEdit ? "Edit subscription" : "New subscription"}
      size="md"
      centered
    >
      <Stack gap="sm">
        <TextInput
          label="Product ID"
          description="Reverse-DNS, immutable after creation."
          placeholder="com.example.app.monthly"
          value={productId}
          onChange={(e) => setProductId(e.currentTarget.value)}
          disabled={isEdit}
          maxLength={255}
          required={!isEdit}
        />
        <TextInput
          label="Reference name"
          description="Internal ASC name."
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          maxLength={64}
          required
        />
        {!isEdit && (
          <Select
            label="Subscription period"
            data={SUBSCRIPTION_PERIOD_OPTIONS}
            value={period}
            onChange={(v) =>
              setPeriod((v as SubscriptionPeriod) ?? "ONE_MONTH")
            }
          />
        )}
        {!isEdit && (
          <NumberInput
            label="Group level"
            description="Tier within the group (1 = highest)."
            value={groupLevel}
            onChange={(v) => setGroupLevel(Number(v) || 1)}
            min={1}
            max={10}
          />
        )}
        {!isEdit && (
          <Switch
            label="Family Sharing eligible"
            checked={familySharable}
            onChange={(e) => setFamilySharable(e.currentTarget.checked)}
          />
        )}
        {!isEdit && (
          <Textarea
            label="Review note (optional)"
            placeholder="Notes for App Review"
            value={reviewNote}
            onChange={(e) => setReviewNote(e.currentTarget.value)}
            maxLength={4000}
            autosize
            minRows={2}
          />
        )}
        <Group justify="flex-end" gap="sm" mt="sm">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            loading={isPending}
            disabled={!name.trim() || (!isEdit && !productId.trim())}
          >
            {isEdit ? "Save" : "Create"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
