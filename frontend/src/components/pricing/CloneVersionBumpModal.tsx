import { useEffect, useMemo, useState } from "react";
import {
  Modal,
  Stack,
  TextInput,
  Group,
  Button,
  Checkbox,
  Alert,
  Text,
  Title,
  Badge,
  Progress,
  Divider,
  Loader,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconCheck,
  IconRefresh,
  IconX,
} from "@tabler/icons-react";
import {
  useClonePreview,
  useCloneSubOrIAP,
  useRetryCloneOperation,
} from "@/lib/hooks";
import type {
  CloneOperationOut,
  CloneScope,
  CloneStepStatus,
} from "@/types";

interface SubscriptionTarget {
  kind: "subscription";
  subId: string;
  productId: string;
  name: string;
}

interface IAPTarget {
  kind: "iap";
  iapId: string;
  productId: string;
  name: string;
}

interface Props {
  appId: string;
  target: SubscriptionTarget | IAPTarget | null;
  opened: boolean;
  onClose: () => void;
}

const DEFAULT_SCOPE: CloneScope = {
  localizations: true,
  price_schedule: true,
  intro_offers: true,
  screenshot: true,
  auto_archive: true,
  group_availability: true,
};

function StepRow({ step }: { step: CloneStepStatus }) {
  const total = step.total ?? 0;
  const completed = step.completed ?? 0;
  const showProgress = total > 0 && step.status !== "skipped";
  const color =
    step.status === "done"
      ? "green"
      : step.status === "failed"
        ? "red"
        : step.status === "partial"
          ? "yellow"
          : step.status === "running"
            ? "blue"
            : step.status === "skipped"
              ? "gray"
              : "gray";
  return (
    <Stack gap={4}>
      <Group justify="space-between">
        <Text size="sm" fw={500}>
          {step.name}
        </Text>
        <Group gap="xs">
          {showProgress ? (
            <Text size="xs" c="dimmed">
              {completed}/{total}
            </Text>
          ) : null}
          <Badge color={color} variant="light" size="sm">
            {step.status}
          </Badge>
        </Group>
      </Group>
      {step.detail ? (
        <Text size="xs" c="dimmed">
          {step.detail}
        </Text>
      ) : null}
      {showProgress ? (
        <Progress value={total > 0 ? (completed / total) * 100 : 0} size="xs" />
      ) : null}
    </Stack>
  );
}

export default function CloneVersionBumpModal({
  appId,
  target,
  opened,
  onClose,
}: Props) {
  const targetKey = target
    ? target.kind === "subscription"
      ? { kind: target.kind as "subscription", subId: target.subId }
      : { kind: target.kind as "iap", iapId: target.iapId }
    : null;
  const previewQuery = useClonePreview(appId, opened ? targetKey : null);
  const cloneMutation = useCloneSubOrIAP();
  const retryMutation = useRetryCloneOperation();

  const [newProductId, setNewProductId] = useState("");
  const [newName, setNewName] = useState("");
  const [scope, setScope] = useState<CloneScope>(DEFAULT_SCOPE);
  const [swapRevenuecat, setSwapRevenuecat] = useState(true);
  const [operation, setOperation] = useState<CloneOperationOut | null>(null);

  useEffect(() => {
    if (!opened || !target) return;
    setNewName(target.name);
    setOperation(null);
    setScope(DEFAULT_SCOPE);
    setSwapRevenuecat(true);
  }, [opened, target]);

  useEffect(() => {
    if (previewQuery.data?.suggested_product_id && !operation) {
      setNewProductId(previewQuery.data.suggested_product_id);
    }
  }, [previewQuery.data, operation]);

  const preview = previewQuery.data;
  const failedSteps = useMemo(() => {
    if (!operation) return 0;
    return [...operation.asc_steps, ...operation.revenuecat_steps].filter(
      (s) => s.status === "failed",
    ).length;
  }, [operation]);

  if (!target) return null;

  const handleSubmit = () => {
    cloneMutation.mutate(
      {
        appId,
        target: targetKey!,
        body: {
          new_product_id: newProductId,
          new_name: newName || null,
          scope,
          swap_revenuecat: swapRevenuecat,
        },
      },
      {
        onSuccess: (op) => setOperation(op),
      },
    );
  };

  const handleRetry = () => {
    if (!operation) return;
    retryMutation.mutate(
      { appId, opId: operation.id },
      { onSuccess: (op) => setOperation(op) },
    );
  };

  const handleClose = () => {
    setOperation(null);
    onClose();
  };

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={
        <Group gap="sm">
          <Title order={4}>Clone &amp; version-bump</Title>
          <Badge variant="light">{target.kind}</Badge>
        </Group>
      }
      size="lg"
      centered
    >
      <Stack gap="md">
        <Alert
          color="blue"
          icon={<IconAlertCircle size={16} />}
          variant="light"
          title="What this does"
        >
          Mints a new product with a fresh version suffix and copies the
          chosen scope from <code>{target.productId}</code>. Apple keeps
          existing subscribers on the old product. RevenueCat entitlements
          and packages stay the same — only the underlying productId
          changes.
        </Alert>

        {previewQuery.isLoading ? (
          <Group>
            <Loader size="sm" />
            <Text size="sm">Loading source preview…</Text>
          </Group>
        ) : preview ? (
          <Stack gap={4}>
            <Group justify="space-between">
              <Text size="sm" c="dimmed">
                Source <code>{preview.source_product_id}</code>
              </Text>
              <Group gap="xs">
                <Badge variant="light">
                  {preview.locale_count} locales
                </Badge>
                <Badge variant="light">
                  {preview.priced_territory_count} prices
                </Badge>
                {preview.intro_offer_count > 0 ? (
                  <Badge variant="light" color="teal">
                    {preview.intro_offer_count} intro offers
                  </Badge>
                ) : null}
                {preview.has_screenshot ? (
                  <Badge variant="light" color="grape">
                    screenshot
                  </Badge>
                ) : null}
              </Group>
            </Group>
            {preview.revenuecat_connected ? (
              preview.revenuecat_old_product_found ? (
                <Text size="xs" c="dimmed">
                  RevenueCat: will swap on{" "}
                  {preview.revenuecat_attached_entitlements} entitlement(s)
                  and {preview.revenuecat_attached_packages} package(s).
                </Text>
              ) : (
                <Text size="xs" c="orange">
                  RevenueCat connected but old product wasn&apos;t found —
                  swap will be skipped.
                </Text>
              )
            ) : (
              <Text size="xs" c="dimmed">
                RevenueCat not connected for this app — only ASC will be
                touched.
              </Text>
            )}
          </Stack>
        ) : null}

        <TextInput
          label="New product ID"
          description="Apple's productId is immutable, so a new id is required."
          value={newProductId}
          onChange={(e) => setNewProductId(e.currentTarget.value)}
          required
          disabled={!!operation}
        />
        <TextInput
          label="Display name"
          description="Defaults to the source name."
          value={newName}
          onChange={(e) => setNewName(e.currentTarget.value)}
          disabled={!!operation}
        />

        <Divider label="What to copy" labelPosition="left" />
        <Stack gap="xs">
          <Checkbox
            label="Localizations (name + description per locale)"
            checked={scope.localizations}
            onChange={(e) =>
              setScope((s) => ({
                ...s,
                localizations: e.currentTarget.checked,
              }))
            }
            disabled={!!operation}
          />
          <Checkbox
            label="Price schedule (price_point per territory)"
            checked={scope.price_schedule}
            onChange={(e) =>
              setScope((s) => ({
                ...s,
                price_schedule: e.currentTarget.checked,
              }))
            }
            disabled={!!operation}
          />
          {target.kind === "subscription" ? (
            <Checkbox
              label="Introductory offers"
              checked={scope.intro_offers}
              onChange={(e) =>
                setScope((s) => ({
                  ...s,
                  intro_offers: e.currentTarget.checked,
                }))
              }
              disabled={!!operation}
            />
          ) : null}
          {target.kind === "subscription" ? (
            <Checkbox
              label="Group availability + family sharing flag"
              checked={scope.group_availability}
              onChange={(e) =>
                setScope((s) => ({
                  ...s,
                  group_availability: e.currentTarget.checked,
                }))
              }
              disabled={!!operation}
            />
          ) : null}
          <Checkbox
            label="Review screenshot + notes"
            checked={scope.screenshot}
            onChange={(e) =>
              setScope((s) => ({
                ...s,
                screenshot: e.currentTarget.checked,
              }))
            }
            disabled={!!operation}
          />
          <Checkbox
            label="Auto-archive old product (remove from sale)"
            description="Existing subscribers are unaffected — only new sign-ups are blocked."
            checked={scope.auto_archive}
            onChange={(e) =>
              setScope((s) => ({
                ...s,
                auto_archive: e.currentTarget.checked,
              }))
            }
            disabled={!!operation}
          />
          {preview?.revenuecat_connected ? (
            <Checkbox
              label="Update RevenueCat (swap attached products)"
              checked={swapRevenuecat}
              onChange={(e) => setSwapRevenuecat(e.currentTarget.checked)}
              disabled={!!operation}
            />
          ) : null}
        </Stack>

        {operation ? (
          <>
            <Divider
              label={
                <Group gap="xs">
                  <Text size="sm" fw={600}>
                    Operation #{operation.id}
                  </Text>
                  <Badge
                    color={
                      operation.status === "done"
                        ? "green"
                        : operation.status === "partial"
                          ? "yellow"
                          : operation.status === "failed"
                            ? "red"
                            : "gray"
                    }
                    variant="filled"
                  >
                    {operation.status}
                  </Badge>
                </Group>
              }
              labelPosition="left"
            />
            <Stack gap="md">
              <Stack gap="xs">
                <Text size="xs" c="dimmed" tt="uppercase">
                  ASC
                </Text>
                {operation.asc_steps.map((s) => (
                  <StepRow key={s.name} step={s} />
                ))}
              </Stack>
              {operation.revenuecat_steps.length > 0 ? (
                <Stack gap="xs">
                  <Text size="xs" c="dimmed" tt="uppercase">
                    RevenueCat
                  </Text>
                  {operation.revenuecat_steps.map((s) => (
                    <StepRow key={s.name} step={s} />
                  ))}
                </Stack>
              ) : null}
            </Stack>
            {operation.error_log.length > 0 ? (
              <Alert
                color="red"
                icon={<IconX size={16} />}
                variant="light"
                title={`${operation.error_log.length} error(s)`}
              >
                <Stack gap={2}>
                  {operation.error_log.slice(0, 6).map((err, i) => (
                    <Text size="xs" key={i} ff="monospace">
                      {err}
                    </Text>
                  ))}
                </Stack>
              </Alert>
            ) : null}
          </>
        ) : null}

        <Group justify="space-between">
          <Button variant="subtle" onClick={handleClose}>
            Close
          </Button>
          {operation == null ? (
            <Button
              onClick={handleSubmit}
              loading={cloneMutation.isPending}
              disabled={!newProductId}
              leftSection={<IconCheck size={16} />}
            >
              Run clone
            </Button>
          ) : failedSteps > 0 ? (
            <Button
              onClick={handleRetry}
              loading={retryMutation.isPending}
              leftSection={<IconRefresh size={16} />}
              color="yellow"
            >
              Retry {failedSteps} failed step(s)
            </Button>
          ) : (
            <Button onClick={handleClose} color="green">
              Done
            </Button>
          )}
        </Group>
      </Stack>
    </Modal>
  );
}
