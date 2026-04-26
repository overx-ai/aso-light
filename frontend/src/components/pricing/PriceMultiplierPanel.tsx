import { useState, useCallback } from "react";
import {
  Paper,
  Group,
  Stack,
  Select,
  NumberInput,
  Switch,
  SegmentedControl,
  Button,
  Text,
  Modal,
  Alert,
  Collapse,
  UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconEye,
  IconUpload,
  IconX,
  IconAlertCircle,
  IconAlertTriangle,
  IconChevronDown,
  IconChevronUp,
  IconAdjustments,
} from "@tabler/icons-react";
import PresetManager from "@/components/pricing/PresetManager";
import type {
  PricePreviewRequest,
  PricePreviewResponse,
  PricePreviewItem,
  PricePreset,
  Territory,
} from "@/types";

const INDEX_TYPE_OPTIONS = [
  { value: "exchange_rate", label: "Exchange Rate" },
  { value: "ppp", label: "PPP" },
  { value: "bigmac", label: "Big Mac" },
  { value: "netflix", label: "Netflix" },
  { value: "spotify", label: "Spotify" },
  { value: "fixed_payout", label: "Fixed Payout" },
];

const CHARMING_OPTIONS = [
  { value: "smart", label: "Smart" },
  { value: "none", label: "None" },
  { value: ".99", label: ".99" },
  { value: ".95", label: ".95" },
];

interface PriceMultiplierPanelProps {
  appId: string;
  subId: string;
  territories: Territory[];
  preview: PricePreviewResponse | null;
  onPreview: (appId: string, subId: string, data: PricePreviewRequest) => void;
  onApply: (
    appId: string,
    subId: string,
    items: { territory_code: string; price_point_id: string }[],
  ) => void;
  onClearPreview: () => void;
  isPreviewLoading: boolean;
  isApplyLoading: boolean;
}

export default function PriceMultiplierPanel({
  appId,
  subId,
  territories,
  preview,
  onPreview,
  onApply,
  onClearPreview,
  isPreviewLoading,
  isApplyLoading,
}: PriceMultiplierPanelProps) {
  const [indexType, setIndexType] = useState("exchange_rate");
  const [basePrice, setBasePrice] = useState<number | string>(9.99);
  const [baseTerritory, setBaseTerritory] = useState("US");
  const [applyVat, setApplyVat] = useState(true);
  const [charmingMode, setCharmingMode] = useState("smart");

  const [confirmOpened, { open: openConfirm, close: closeConfirm }] =
    useDisclosure(false);
  const [panelOpened, { toggle: togglePanel }] = useDisclosure(true);

  const territoryOptions = territories.map((t) => ({
    value: t.code,
    label: `${t.code} - ${t.name}`,
  }));

  const handlePreview = () => {
    const price = typeof basePrice === "string" ? parseFloat(basePrice) : basePrice;
    if (isNaN(price) || price <= 0) return;

    onPreview(appId, subId, {
      index_type: indexType,
      base_price: price,
      base_territory_code: baseTerritory,
      apply_vat: applyVat,
      charming_mode: charmingMode,
    });
  };

  const handleApply = () => {
    if (!preview) return;
    const items = preview.items
      .filter(
        (item: PricePreviewItem) =>
          item.price_point_id !== null &&
          item.diff_percent !== null &&
          Math.abs(item.diff_percent) > 0.01 &&
          !item.would_be_skipped,
      )
      .map((item: PricePreviewItem) => ({
        territory_code: item.territory_code,
        price_point_id: item.price_point_id!,
      }));

    onApply(appId, subId, items);
    closeConfirm();
  };

  const handleLoadPreset = useCallback((preset: PricePreset) => {
    setIndexType(preset.index_type);
    setBasePrice(preset.base_price);
    setBaseTerritory(preset.base_territory_code);
    setApplyVat(preset.apply_vat);
    setCharmingMode(preset.charming_mode);
  }, []);

  const changedCount = preview
    ? preview.items.filter(
        (item: PricePreviewItem) =>
          item.price_point_id !== null &&
          item.diff_percent !== null &&
          Math.abs(item.diff_percent) > 0.01 &&
          !item.would_be_skipped,
      ).length
    : 0;

  const skippedCount = preview
    ? preview.items.filter((item: PricePreviewItem) => item.would_be_skipped)
        .length
    : 0;

  return (
    <>
      <Paper withBorder p="md" radius="md" mb="md">
        <UnstyledButton onClick={togglePanel} w="100%">
          <Group justify="space-between">
            <Group gap="xs">
              <IconAdjustments size={18} color="var(--mantine-color-blue-6)" />
              <Text fw={600} size="sm">
                Price Configuration
              </Text>
              {preview && (
                <Text size="xs" c="dimmed">
                  ({changedCount} territories would change
                  {skippedCount > 0 && `, ${skippedCount} skipped`})
                </Text>
              )}
            </Group>
            {panelOpened ? (
              <IconChevronUp size={16} color="var(--mantine-color-dimmed)" />
            ) : (
              <IconChevronDown size={16} color="var(--mantine-color-dimmed)" />
            )}
          </Group>
        </UnstyledButton>

        <Collapse in={panelOpened}>
          <Stack gap="md" mt="md">
            <Group grow align="flex-end">
              <Select
                label="Price Index"
                data={INDEX_TYPE_OPTIONS}
                value={indexType}
                onChange={(v) => v && setIndexType(v)}
                size="sm"
              />
              <NumberInput
                label="Base Price"
                value={basePrice}
                onChange={setBasePrice}
                min={0.01}
                step={0.01}
                decimalScale={2}
                fixedDecimalScale
                prefix="$"
                size="sm"
              />
              <Select
                label="Base Territory"
                data={territoryOptions}
                value={baseTerritory}
                onChange={(v) => v && setBaseTerritory(v)}
                searchable
                size="sm"
              />
            </Group>

            <Group>
              <Switch
                label="Apply VAT"
                checked={applyVat}
                onChange={(e) => setApplyVat(e.currentTarget.checked)}
                size="sm"
              />
              <div>
                <Text size="xs" fw={500} mb={4}>
                  Charming Price
                </Text>
                <SegmentedControl
                  data={CHARMING_OPTIONS}
                  value={charmingMode}
                  onChange={setCharmingMode}
                  size="xs"
                />
              </div>
            </Group>

            <Group justify="space-between">
              <PresetManager
                currentSettings={{
                  base_territory_code: baseTerritory,
                  base_price:
                    typeof basePrice === "string"
                      ? parseFloat(basePrice) || 0
                      : basePrice,
                  index_type: indexType,
                  apply_vat: applyVat,
                  charming_mode: charmingMode,
                }}
                onLoadPreset={handleLoadPreset}
              />
            </Group>

            <Group>
              <Button
                leftSection={<IconEye size={16} />}
                onClick={handlePreview}
                loading={isPreviewLoading}
                variant="filled"
                size="sm"
              >
                Preview Prices
              </Button>
              {preview && (
                <>
                  <Button
                    leftSection={<IconUpload size={16} />}
                    onClick={openConfirm}
                    loading={isApplyLoading}
                    color="green"
                    variant="filled"
                    size="sm"
                    disabled={changedCount === 0}
                  >
                    Apply to App Store
                  </Button>
                  <Button
                    leftSection={<IconX size={16} />}
                    onClick={onClearPreview}
                    variant="subtle"
                    color="gray"
                    size="sm"
                  >
                    Clear Preview
                  </Button>
                </>
              )}
            </Group>
          </Stack>
        </Collapse>
      </Paper>

      <Modal
        opened={confirmOpened}
        onClose={closeConfirm}
        title="Confirm Price Update"
        size="sm"
      >
        <Stack>
          <Alert
            icon={<IconAlertCircle size={20} />}
            title="Apply prices to App Store Connect?"
            color="yellow"
          >
            This will update prices for {changedCount} territories in App Store
            Connect. This action will take effect immediately.
          </Alert>
          {skippedCount > 0 && (
            <Alert
              icon={<IconAlertTriangle size={20} />}
              title="Some territories will be skipped"
              color="orange"
            >
              {skippedCount} territories will not be updated because the price
              change exceeds safety limits (+20% / -25%, likely incorrect rates).
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="subtle" onClick={closeConfirm}>
              Cancel
            </Button>
            <Button
              color="green"
              loading={isApplyLoading}
              onClick={handleApply}
            >
              Confirm & Apply
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
