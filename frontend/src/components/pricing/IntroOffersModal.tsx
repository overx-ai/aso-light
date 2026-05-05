import { useEffect, useState } from "react";
import {
  Modal,
  Stack,
  Group,
  Button,
  Table,
  Select,
  NumberInput,
  TextInput,
  Text,
  Skeleton,
  ActionIcon,
} from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import {
  useIntroOffers,
  useCreateIntroOffer,
  useDeleteIntroOffer,
  useTerritories,
} from "@/lib/hooks";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/hooks";
import api from "@/lib/api";
import { INTRO_DURATION_OPTIONS } from "@/components/pricing/subscriptionConstants";
import type {
  IntroOfferDuration,
  IntroOfferMode,
  PriceApplyRequest,
  PriceApplyResponse,
  Subscription,
} from "@/types";

const MODE_OPTIONS: { value: IntroOfferMode; label: string }[] = [
  { value: "FREE_TRIAL", label: "Free trial" },
  { value: "PAY_AS_YOU_GO", label: "Pay as you go" },
  { value: "PAY_UP_FRONT", label: "Pay up front" },
];

interface Props {
  appId: string;
  subscription: Subscription | null;
  opened: boolean;
  onClose: () => void;
}

export default function IntroOffersModal({
  appId,
  subscription,
  opened,
  onClose,
}: Props) {
  const subId = subscription ? String(subscription.id) : "";
  const { data: offers = [], isLoading } = useIntroOffers(
    appId,
    opened ? subId : "",
  );
  const { data: territories = [] } = useTerritories();
  const createMutation = useCreateIntroOffer(appId, subId);
  const deleteMutation = useDeleteIntroOffer(appId, subId);
  const queryClient = useQueryClient();

  // Local mutation that calls /prices/apply but does NOT inherit
  // useApplyPrices' generic "Prices applied" toast — bulk-apply from
  // this modal only changes intro offers, never prices, so the generic
  // hook's success copy ("Applied 0 price(s)…") would be misleading.
  const bulkMutation = useMutation({
    mutationFn: async (data: PriceApplyRequest) => {
      const response = await api.post<PriceApplyResponse>(
        `/apps/${appId}/subscriptions/${subId}/prices/apply`,
        data,
      );
      return response.data;
    },
    onError: () => {
      notifications.show({
        title: "Free trial sync failed",
        message: "Could not apply free trial. Please try again.",
        color: "red",
      });
    },
  });

  const [territoryCode, setTerritoryCode] = useState<string | null>("US");
  const [mode, setMode] = useState<IntroOfferMode>("FREE_TRIAL");
  const [duration, setDuration] = useState<IntroOfferDuration>("ONE_MONTH");
  const [numberOfPeriods, setNumberOfPeriods] = useState<number>(1);
  const [pricePointId, setPricePointId] = useState("");

  // Reset all draft inputs when the modal closes or the subscription
  // changes so we never show another sub's stale data.
  useEffect(() => {
    if (!opened) return;
    setTerritoryCode("US");
    setMode("FREE_TRIAL");
    setDuration("ONE_MONTH");
    setNumberOfPeriods(1);
    setPricePointId("");
  }, [opened, subId]);

  // Apple requires a territory on every intro offer — there is no
  // "worldwide" option. To set the same offer in every territory, use
  // the "Include free trial on apply" toggle on the Pricing panel.
  const territoryOptions = territories.map((t) => ({
    value: t.code,
    label: `${t.name} (${t.code})`,
  }));

  const requiresPrice = mode === "PAY_AS_YOU_GO" || mode === "PAY_UP_FRONT";
  const periodsLocked = mode === "FREE_TRIAL" || mode === "PAY_UP_FRONT";

  const handleAdd = () => {
    if (!territoryCode) return;
    if (requiresPrice && !pricePointId.trim()) return;
    createMutation.mutate(
      {
        territory_code: territoryCode,
        offer_mode: mode,
        duration,
        number_of_periods: periodsLocked ? 1 : numberOfPeriods,
        price_point_id: requiresPrice ? pricePointId.trim() : null,
      },
      {
        onSuccess: () => {
          setPricePointId("");
        },
      },
    );
  };

  // Bulk apply: replace all existing intro offers with a free trial of
  // the chosen duration in every territory the sub is currently priced
  // in. Reuses the prices/apply route with empty items + intro_offer.
  const handleBulkApply = () => {
    bulkMutation.mutate(
      {
        items: [],
        intro_offer: {
          duration,
          number_of_periods: 1,
        },
      },
      {
        onSuccess: (result) => {
          queryClient.invalidateQueries({
            queryKey: queryKeys.introOffers(appId, subId),
          });
          if (result.intro_offer_synced && !result.intro_offer_error) {
            notifications.show({
              title: "Free trial applied",
              message: "Free trial pushed to every priced territory.",
              color: "green",
            });
          } else if (result.intro_offer_error) {
            notifications.show({
              title: "Free trial sync had failures",
              message: result.intro_offer_error,
              color: "orange",
            });
          }
        },
      },
    );
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={`Introductory offers${subscription ? ` — ${subscription.name}` : ""}`}
      size="xl"
      centered
    >
      <Stack gap="md">
        <Stack gap="xs">
          <Text size="sm" fw={500}>
            Existing offers
          </Text>
          {isLoading ? (
            <Skeleton height={120} />
          ) : offers.length === 0 ? (
            <Text c="dimmed" size="sm">
              No introductory offers yet.
            </Text>
          ) : (
            <Table withTableBorder striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Territory</Table.Th>
                  <Table.Th>Mode</Table.Th>
                  <Table.Th>Duration</Table.Th>
                  <Table.Th>Periods</Table.Th>
                  <Table.Th>Price point</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {offers.map((o) => (
                  <Table.Tr key={o.id}>
                    <Table.Td>{o.territory_code ?? "Worldwide"}</Table.Td>
                    <Table.Td>{o.offer_mode}</Table.Td>
                    <Table.Td>{o.duration}</Table.Td>
                    <Table.Td>{o.number_of_periods}</Table.Td>
                    <Table.Td>{o.price_point_id ?? "—"}</Table.Td>
                    <Table.Td>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        onClick={() => deleteMutation.mutate(o.id)}
                        loading={deleteMutation.isPending}
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

        <Stack gap="xs">
          <Text size="sm" fw={500}>
            Add new offer
          </Text>
          <Group grow align="flex-end">
            <Select
              label="Territory"
              data={territoryOptions}
              value={territoryCode}
              onChange={(v) => setTerritoryCode(v)}
              searchable
              required
              size="xs"
            />
            <Select
              label="Mode"
              data={MODE_OPTIONS}
              value={mode}
              onChange={(v) => setMode((v as IntroOfferMode) ?? "FREE_TRIAL")}
              size="xs"
            />
            <Select
              label="Duration"
              data={INTRO_DURATION_OPTIONS}
              value={duration}
              onChange={(v) =>
                setDuration((v as IntroOfferDuration) ?? "ONE_MONTH")
              }
              size="xs"
            />
            <NumberInput
              label="# periods"
              value={numberOfPeriods}
              onChange={(v) => setNumberOfPeriods(Number(v) || 1)}
              min={1}
              max={12}
              disabled={periodsLocked}
              description={periodsLocked ? "Forced to 1 by Apple" : undefined}
              size="xs"
            />
          </Group>
          {requiresPrice && (
            <TextInput
              label="Subscription price-point ID"
              description="ASC subscriptionPricePoint id for this territory."
              value={pricePointId}
              onChange={(e) => setPricePointId(e.currentTarget.value)}
              size="xs"
            />
          )}
          <Group justify="space-between" align="center">
            {mode === "FREE_TRIAL" ? (
              <Button
                size="xs"
                variant="light"
                color="grape"
                onClick={handleBulkApply}
                loading={bulkMutation.isPending}
              >
                Apply to all territories
              </Button>
            ) : (
              <Text size="xs" c="dimmed">
                Bulk apply only available for free trials.
              </Text>
            )}
            <Button
              size="xs"
              onClick={handleAdd}
              loading={createMutation.isPending}
              disabled={
                !territoryCode || (requiresPrice && !pricePointId.trim())
              }
            >
              Add offer
            </Button>
          </Group>
        </Stack>
      </Stack>
    </Modal>
  );
}
