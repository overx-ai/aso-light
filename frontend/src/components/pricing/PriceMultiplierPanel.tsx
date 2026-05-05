import { useState, useCallback, useEffect } from "react";
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
import GDPBracketEditor from "@/components/pricing/GDPBracketEditor";
import { INTRO_DURATION_OPTIONS } from "@/components/pricing/subscriptionConstants";
import type {
  GDPBracketConfig,
  IntroOfferDuration,
  PricePreviewRequest,
  PricePreviewResponse,
  PricePreviewItem,
  PricePreset,
  Territory,
} from "@/types";

// "No changes (intro offer only)" is meaningful only for subscriptions
// — IAPs don't have intro offers, so for those callers (showIntroOffer
// = false) we drop this option from the dropdown to avoid presenting a
// silently no-op apply.
const INTRO_OFFER_ONLY_OPTION = {
  value: "none",
  label: "No changes (intro offer only)",
};

const PRICE_INDEX_OPTIONS = [
  { value: "exchange_rate", label: "Exchange Rate" },
  { value: "ppp", label: "PPP" },
  { value: "bigmac", label: "Big Mac" },
  { value: "netflix", label: "Netflix" },
  { value: "spotify", label: "Spotify" },
  { value: "fixed_payout", label: "Fixed Payout" },
  { value: "gdp_brackets", label: "GDP Brackets" },
];

const DEFAULT_GDP_CONFIG: GDPBracketConfig = {
  tier_prices_usd: { top: 9.99, mid: 4.99, low: 1.99, special: 2.99 },
  tier_thresholds_usd: { top_min: 40000, mid_min: 15000 },
  manual_overrides: {},
  special_territories: [],
};

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
  forcedTerritories?: Set<string>;
  onPreview: (appId: string, subId: string, data: PricePreviewRequest) => void;
  onApply: (
    appId: string,
    subId: string,
    items: { territory_code: string; price_point_id: string; force?: boolean }[],
    introOffer?: { duration: IntroOfferDuration; number_of_periods: number } | null,
  ) => void;
  onClearPreview: () => void;
  isPreviewLoading: boolean;
  isApplyLoading: boolean;
  /** Show the worldwide free-trial toggle (subscriptions only). */
  showIntroOffer?: boolean;
}

export default function PriceMultiplierPanel({
  appId,
  subId,
  territories,
  preview,
  forcedTerritories,
  onPreview,
  onApply,
  onClearPreview,
  isPreviewLoading,
  isApplyLoading,
  showIntroOffer = false,
}: PriceMultiplierPanelProps) {
  // For subscriptions, the "intro-offer-only" mode is the default
  // because the panel is most often opened to push a free trial. IAPs
  // (showIntroOffer=false) don't support intro offers, so default to a
  // real index that will produce a preview.
  const [indexType, setIndexType] = useState(
    showIntroOffer ? "none" : "exchange_rate",
  );
  const [basePrice, setBasePrice] = useState<number | string>(9.99);
  const [baseTerritory, setBaseTerritory] = useState("US");
  const [applyVat, setApplyVat] = useState(true);
  const [charmingMode, setCharmingMode] = useState("smart");
  const [gdpConfig, setGdpConfig] = useState<GDPBracketConfig>(DEFAULT_GDP_CONFIG);
  const [introOfferEnabled, setIntroOfferEnabled] = useState(false);
  const [introOfferDuration, setIntroOfferDuration] =
    useState<IntroOfferDuration>("ONE_MONTH");

  const isForced = (code: string) =>
    forcedTerritories ? forcedTerritories.has(code) : false;

  const [confirmOpened, { open: openConfirm, close: closeConfirm }] =
    useDisclosure(false);
  const [panelOpened, { toggle: togglePanel }] = useDisclosure(true);
  const [
    gdpEditorOpened,
    { open: openGdpEditor, close: closeGdpEditor },
  ] = useDisclosure(false);

  const isGdpBrackets = indexType === "gdp_brackets";
  const isNoChanges = indexType === "none";

  // When "No changes" is selected the only thing the panel does is push
  // the free trial — so force the toggle on (and clear the existing
  // preview, if any, so the Apply confirm copy is correct).
  useEffect(() => {
    if (isNoChanges) {
      setIntroOfferEnabled(true);
      onClearPreview();
    }
  }, [isNoChanges, onClearPreview]);

  const territoryOptions = territories.map((t) => ({
    value: t.code,
    label: `${t.code} - ${t.name}`,
  }));

  const handlePreview = () => {
    if (isGdpBrackets) {
      onPreview(appId, subId, {
        index_type: "gdp_brackets",
        base_price: 0,
        base_territory_code: baseTerritory,
        apply_vat: applyVat,
        charming_mode: charmingMode,
        gdp_config: gdpConfig,
      });
      return;
    }

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
    const items =
      isNoChanges || !preview
        ? []
        : preview.items
            .filter(
              (item: PricePreviewItem) =>
                wouldChange(item) &&
                (!item.would_be_skipped || isForced(item.territory_code)),
            )
            .map((item: PricePreviewItem) => ({
              territory_code: item.territory_code,
              price_point_id: item.price_point_id!,
              force: isForced(item.territory_code) ? true : undefined,
            }));

    const introOffer =
      showIntroOffer && introOfferEnabled
        ? { duration: introOfferDuration, number_of_periods: 1 }
        : null;
    onApply(appId, subId, items, introOffer);
    closeConfirm();
  };

  const handleLoadPreset = useCallback((preset: PricePreset) => {
    setIndexType(preset.index_type);
    setBasePrice(preset.base_price);
    setBaseTerritory(preset.base_territory_code);
    setApplyVat(preset.apply_vat);
    setCharmingMode(preset.charming_mode);
    if (preset.index_type === "gdp_brackets" && preset.config) {
      setGdpConfig(preset.config as unknown as GDPBracketConfig);
    }
  }, []);

  // A territory counts as "would change" when there's an Apple
  // price_point_id to apply AND either:
  //   * no current price (so applying creates a new manual entry), or
  //   * the diff is large enough to be worth submitting.
  const wouldChange = (item: PricePreviewItem) => {
    if (item.price_point_id === null) return false;
    if (item.current_price === null) return true;
    return (
      item.diff_percent !== null && Math.abs(item.diff_percent) > 0.01
    );
  };

  const skippedItems = preview
    ? preview.items.filter((item) => wouldChange(item) && item.would_be_skipped)
    : [];
  const safeChangedCount = preview
    ? preview.items.filter((item) => wouldChange(item) && !item.would_be_skipped)
        .length
    : 0;
  const forcedCount = skippedItems.filter((item) =>
    isForced(item.territory_code),
  ).length;
  const changedCount = safeChangedCount + forcedCount;
  const skippedCount = skippedItems.length - forcedCount;

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
                data={
                  showIntroOffer
                    ? [INTRO_OFFER_ONLY_OPTION, ...PRICE_INDEX_OPTIONS]
                    : PRICE_INDEX_OPTIONS
                }
                value={indexType}
                onChange={(v) => v && setIndexType(v)}
                size="sm"
              />
              {!isNoChanges && (isGdpBrackets ? (
                <div>
                  <Text size="xs" fw={500} mb={4}>
                    Tier Configuration
                  </Text>
                  <Button
                    variant="light"
                    onClick={openGdpEditor}
                    size="sm"
                    fullWidth
                  >
                    Configure brackets
                    {" — "}
                    Top ${gdpConfig.tier_prices_usd.top.toFixed(2)} ·
                    Mid ${gdpConfig.tier_prices_usd.mid.toFixed(2)} ·
                    Low ${gdpConfig.tier_prices_usd.low.toFixed(2)} ·
                    Special ${gdpConfig.tier_prices_usd.special.toFixed(2)}
                  </Button>
                </div>
              ) : (
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
              ))}
              {!isNoChanges && (
                <Select
                  label="Base Territory"
                  data={territoryOptions}
                  value={baseTerritory}
                  onChange={(v) => v && setBaseTerritory(v)}
                  searchable
                  size="sm"
                  disabled={isGdpBrackets}
                />
              )}
            </Group>

            {!isNoChanges && (
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
            )}

            {!isNoChanges && (
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
                    config: isGdpBrackets
                      ? (gdpConfig as unknown as Record<string, unknown>)
                      : null,
                  }}
                  onLoadPreset={handleLoadPreset}
                />
              </Group>
            )}

            {showIntroOffer && (
              <Group align="flex-end" gap="sm">
                {!isNoChanges && (
                  <Switch
                    label="Include free trial on apply"
                    description="Replaces any existing intro offers."
                    checked={introOfferEnabled}
                    onChange={(e) =>
                      setIntroOfferEnabled(e.currentTarget.checked)
                    }
                    size="sm"
                  />
                )}
                <Select
                  label="Trial duration"
                  data={INTRO_DURATION_OPTIONS}
                  value={introOfferDuration}
                  onChange={(v) =>
                    setIntroOfferDuration(
                      (v as IntroOfferDuration) ?? "ONE_MONTH",
                    )
                  }
                  disabled={!introOfferEnabled}
                  size="sm"
                  w={140}
                />
              </Group>
            )}

            <Group>
              {!isNoChanges && (
                <Button
                  leftSection={<IconEye size={16} />}
                  onClick={handlePreview}
                  loading={isPreviewLoading}
                  variant="filled"
                  size="sm"
                >
                  Preview Prices
                </Button>
              )}
              {(preview || isNoChanges) && (
                <>
                  <Button
                    leftSection={<IconUpload size={16} />}
                    onClick={openConfirm}
                    loading={isApplyLoading}
                    color="green"
                    variant="filled"
                    size="sm"
                    disabled={
                      !isNoChanges &&
                      changedCount === 0 &&
                      !(showIntroOffer && introOfferEnabled)
                    }
                  >
                    Apply to App Store
                  </Button>
                  {!isNoChanges && (
                    <Button
                      leftSection={<IconX size={16} />}
                      onClick={onClearPreview}
                      variant="subtle"
                      color="gray"
                      size="sm"
                    >
                      Clear Preview
                    </Button>
                  )}
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
            title="Apply to App Store Connect?"
            color="yellow"
          >
            {changedCount > 0 ? (
              <>
                This will update prices for {changedCount} territories
                {showIntroOffer && introOfferEnabled
                  ? " and replace any existing introductory offers with a free trial in every priced territory"
                  : ""}
                . This action takes effect immediately.
              </>
            ) : (
              <>
                {isNoChanges ? "Prices won't change" : "Prices already match"}
                {" "}— this will replace any existing introductory offers with
                a free trial in every priced territory. This action takes
                effect immediately.
              </>
            )}
          </Alert>
          {forcedCount > 0 && (
            <Alert
              icon={<IconAlertTriangle size={20} />}
              title="Forced overrides"
              color="red"
            >
              {forcedCount} territories exceed the ±50% safety band and will
              be applied anyway because you forced them. Double-check those
              prices.
            </Alert>
          )}
          {skippedCount > 0 && (
            <Alert
              icon={<IconAlertTriangle size={20} />}
              title="Some territories will be skipped"
              color="orange"
            >
              {skippedCount} territories will not be updated because the price
              change exceeds safety limits (±50%, likely incorrect rates).
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

      <GDPBracketEditor
        opened={gdpEditorOpened}
        onClose={closeGdpEditor}
        value={gdpConfig}
        onChange={setGdpConfig}
      />
    </>
  );
}
