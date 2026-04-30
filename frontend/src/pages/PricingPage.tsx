import { useState, useCallback, useMemo } from "react";
import { useParams } from "react-router-dom";
import {
  Container,
  Title,
  Text,
  Tabs,
  Paper,
  Stack,
  Skeleton,
  Select,
  Group,
  Badge,
  SegmentedControl,
} from "@mantine/core";
import {
  IconCoin,
  IconCoins,
  IconLanguage,
  IconReceipt,
  IconRefresh,
  IconCheck,
} from "@tabler/icons-react";
import { Button, Tooltip } from "@mantine/core";
import {
  useApp,
  useSubscriptions,
  useSubscriptionPrices,
  usePreviewPrices,
  useApplyPrices,
  useSyncSubscriptionPrices,
  useSyncPricePoints,
  usePricePointCacheStatus,
  useIAPs,
  useIAPPrices,
  usePreviewIAPPrices,
  useApplyIAPPrices,
  useSyncIAPPrices,
  useSyncIAPPricePoints,
  useIAPPricePointCacheStatus,
  useResolveIAPPrice,
  useTerritories,
  useSubscriptionLocalizations,
  useSaveSubscriptionLocalizations,
  useIAPLocalizations,
  useSaveIAPLocalizations,
  useSubscriptionScreenshot,
  useUploadSubscriptionScreenshot,
  useIAPScreenshot,
  useUploadIAPScreenshot,
  useResolvePrice,
} from "@/lib/hooks";
import PriceGrid from "@/components/pricing/PriceGrid";
import PriceMultiplierPanel from "@/components/pricing/PriceMultiplierPanel";
import ExportImportButtons from "@/components/pricing/ExportImportButtons";
import LocalizationEditor from "@/components/pricing/LocalizationEditor";
import ReviewScreenshotUpload from "@/components/pricing/ReviewScreenshotUpload";
import type {
  PricePreviewRequest,
  PricePreviewResponse,
  PricePreviewItem,
  PriceExportItem,
  PriceImportItem,
  PricePoint,
  LocalizationCreate,
  Subscription,
  SubscriptionGroup,
  IAP,
  IAPPricePreviewResponse,
} from "@/types";

function SubscriptionsTab({ appId }: { appId: string }) {
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedSubId, setSelectedSubId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PricePreviewResponse | null>(null);
  const [manualTerritories, setManualTerritories] = useState<Set<string>>(new Set());
  const [manualItems, setManualItems] = useState<Map<string, PricePreviewItem>>(new Map());
  const [forcedTerritories, setForcedTerritories] = useState<Set<string>>(new Set());

  const handleToggleForce = useCallback((territoryCode: string) => {
    setForcedTerritories((prev) => {
      const next = new Set(prev);
      if (next.has(territoryCode)) next.delete(territoryCode);
      else next.add(territoryCode);
      return next;
    });
  }, []);

  const { data: groups, isLoading: groupsLoading } = useSubscriptions(appId);
  const { data: territories = [] } = useTerritories();
  const {
    data: priceData,
    isLoading: pricesLoading,
  } = useSubscriptionPrices(appId, selectedSubId ?? "");
  const previewMutation = usePreviewPrices();
  const applyMutation = useApplyPrices();
  const syncMutation = useSyncSubscriptionPrices();
  const syncPricePointsMutation = useSyncPricePoints();
  const resolveMutation = useResolvePrice();
  const { data: cacheStatus } = usePricePointCacheStatus(
    appId,
    selectedSubId ?? "",
  );

  const groupOptions = useMemo(
    () =>
      (groups ?? []).map((g: SubscriptionGroup) => ({
        value: String(g.id),
        label: g.name,
      })),
    [groups],
  );

  const selectedGroup = useMemo(
    () =>
      groups?.find(
        (g: SubscriptionGroup) => String(g.id) === selectedGroupId,
      ) ?? null,
    [groups, selectedGroupId],
  );

  const subOptions = useMemo(
    () =>
      (selectedGroup?.subscriptions ?? []).map((s: Subscription) => ({
        value: String(s.id),
        label: `${s.name} (${s.product_id})`,
      })),
    [selectedGroup],
  );

  const handleGroupChange = useCallback(
    (value: string | null) => {
      setSelectedGroupId(value);
      setSelectedSubId(null);
      setPreview(null);
    },
    [],
  );

  const handleSubChange = useCallback(
    (value: string | null) => {
      setSelectedSubId(value);
      setPreview(null);
    },
    [],
  );

  const handlePreview = useCallback(
    (pAppId: string, pSubId: string, data: PricePreviewRequest) => {
      previewMutation.mutate(
        { appId: pAppId, subId: pSubId, data },
        {
          onSuccess: (result) => setPreview(result),
        },
      );
    },
    [previewMutation],
  );

  const handleApply = useCallback(
    (
      pAppId: string,
      pSubId: string,
      items: { territory_code: string; price_point_id: string; force?: boolean }[],
    ) => {
      applyMutation.mutate(
        { appId: pAppId, subId: pSubId, data: { items } },
        {
          onSuccess: () => {
            setPreview(null);
            setForcedTerritories(new Set());
          },
        },
      );
    },
    [applyMutation],
  );

  const handleClearPreview = useCallback(() => setPreview(null), []);

  const handleToggleManual = useCallback((territoryCode: string) => {
    setManualTerritories((prev) => {
      const next = new Set(prev);
      if (next.has(territoryCode)) {
        next.delete(territoryCode);
        setManualItems((m) => {
          const nm = new Map(m);
          nm.delete(territoryCode);
          return nm;
        });
      } else {
        next.add(territoryCode);
      }
      return next;
    });
  }, []);

  const handleManualPriceChange = useCallback(
    (territoryCode: string, price: number) => {
      if (!selectedSubId) return;
      resolveMutation.mutate(
        { appId, subId: selectedSubId, territory_code: territoryCode, price },
        {
          onSuccess: (resolved) => {
            // Find territory info from prices or preview
            const existing =
              priceData?.prices.find((p) => p.territory_code === territoryCode) ??
              preview?.items.find((i) => i.territory_code === territoryCode);
            const currentPrice = existing
              ? "customer_price" in existing
                ? existing.customer_price
                : existing.current_price
              : null;
            const diffPercent =
              currentPrice && currentPrice > 0
                ? Math.round(
                    ((resolved.customer_price - currentPrice) / currentPrice) * 100 * 100,
                  ) / 100
                : null;

            setManualItems((prev) => {
              const next = new Map(prev);
              next.set(territoryCode, {
                territory_code: territoryCode,
                territory_name:
                  existing && "territory_name" in existing
                    ? existing.territory_name
                    : territoryCode,
                currency_code: resolved.currency_code,
                current_price: currentPrice ?? null,
                suggested_price: resolved.customer_price,
                nearest_apple_price: resolved.customer_price,
                price_point_id: resolved.price_point_id,
                diff_percent: diffPercent,
                would_be_skipped: false,
              });
              return next;
            });
          },
        },
      );
    },
    [appId, selectedSubId, resolveMutation, priceData, preview],
  );

  // Merge auto-preview with manual overrides
  const mergedPreviewItems = useMemo(() => {
    if (!preview && manualItems.size === 0) return null;
    const items = preview?.items ?? [];
    const merged = items.map((item) =>
      manualItems.has(item.territory_code)
        ? manualItems.get(item.territory_code)!
        : item,
    );
    // Add manual items for territories not in the preview
    for (const [tc, item] of manualItems) {
      if (!items.find((i) => i.territory_code === tc)) {
        merged.push(item);
      }
    }
    return merged.length > 0 ? merged : null;
  }, [preview, manualItems]);

  const selectedSub = useMemo(() => {
    if (!selectedGroup || !selectedSubId) return null;
    return (
      selectedGroup.subscriptions.find(
        (s: Subscription) => String(s.id) === selectedSubId,
      ) ?? null
    );
  }, [selectedGroup, selectedSubId]);

  const exportPrices: PriceExportItem[] = useMemo(
    () =>
      (priceData?.prices ?? []).map((p) => ({
        territory_code: p.territory_code,
        territory_name: p.territory_name,
        currency_code: p.currency_code,
        customer_price: p.customer_price,
        proceeds: p.proceeds,
      })),
    [priceData],
  );

  const handleImport = useCallback((_items: PriceImportItem[]) => {
    // Import results are displayed via notification from the hook.
    // Imported prices can be used for further processing in the future.
  }, []);

  if (groupsLoading) {
    return (
      <Stack gap="md">
        <Skeleton height={40} width={300} />
        <Skeleton height={200} />
      </Stack>
    );
  }

  if (!groups || groups.length === 0) {
    return (
      <Paper withBorder p="xl" ta="center" radius="md">
        <Stack align="center" gap="sm">
          <IconReceipt size={48} color="var(--mantine-color-dimmed)" />
          <Title order={4} c="dimmed">
            No subscriptions found
          </Title>
          <Text c="dimmed" size="sm" maw={400}>
            Sync your app data from App Store Connect to see subscription groups
            and their pricing.
          </Text>
        </Stack>
      </Paper>
    );
  }

  return (
    <Stack gap="md">
      <Group align="flex-end">
        <Group grow align="flex-end" style={{ flex: 1, maxWidth: 600 }}>
          <Select
            label="Subscription Group"
            placeholder="Select a group..."
            data={groupOptions}
            value={selectedGroupId}
            onChange={handleGroupChange}
            size="sm"
          />
          <Select
            label="Subscription"
            placeholder="Select a subscription..."
            data={subOptions}
            value={selectedSubId}
            onChange={handleSubChange}
            disabled={!selectedGroupId}
            size="sm"
          />
        </Group>
        {selectedSubId && (
          <Group gap="xs">
            <Button
              variant="light"
              size="sm"
              leftSection={<IconRefresh size={16} />}
              loading={syncMutation.isPending}
              onClick={() =>
                syncMutation.mutate({ appId, subId: selectedSubId })
              }
            >
              Sync Prices
            </Button>
            <Tooltip
              label={
                cacheStatus?.synced_at
                  ? `Synced ${new Date(cacheStatus.synced_at).toLocaleDateString()}`
                  : "Not synced yet"
              }
              withArrow
            >
              <Button
                variant="light"
                size="sm"
                color={cacheStatus?.cached_territories ? "grape" : "orange"}
                leftSection={<IconCoins size={16} />}
                rightSection={
                  cacheStatus?.cached_territories ? (
                    <Badge
                      size="sm"
                      variant="filled"
                      color="grape"
                      circle
                      styles={{ root: { padding: 0, width: 20, height: 20, minWidth: 20 } }}
                    >
                      <IconCheck size={12} />
                    </Badge>
                  ) : null
                }
                loading={syncPricePointsMutation.isPending}
                onClick={() =>
                  syncPricePointsMutation.mutate({ appId, subId: selectedSubId })
                }
              >
                {cacheStatus?.cached_territories
                  ? `Price Tiers (${cacheStatus.cached_territories})`
                  : "Sync Price Tiers"}
              </Button>
            </Tooltip>
          </Group>
        )}
      </Group>

      {selectedSubId && (
        <>
          <PriceMultiplierPanel
            appId={appId}
            subId={selectedSubId}
            territories={territories}
            preview={preview}
            forcedTerritories={forcedTerritories}
            onPreview={handlePreview}
            onApply={handleApply}
            onClearPreview={handleClearPreview}
            isPreviewLoading={previewMutation.isPending}
            isApplyLoading={applyMutation.isPending}
          />

          <Group justify="flex-end">
            <ExportImportButtons
              subscriptionName={selectedSub?.name ?? "Prices"}
              prices={exportPrices}
              onImport={handleImport}
            />
          </Group>

          <PriceGrid
            prices={priceData?.prices ?? []}
            previewItems={mergedPreviewItems}
            isLoading={pricesLoading}
            manualTerritories={manualTerritories}
            onToggleManual={handleToggleManual}
            onManualPriceChange={handleManualPriceChange}
            forcedTerritories={forcedTerritories}
            onToggleForce={handleToggleForce}
          />
        </>
      )}
    </Stack>
  );
}

function IAPsTab({ appId }: { appId: string }) {
  const [selectedIapId, setSelectedIapId] = useState<string | null>(null);
  const [preview, setPreview] = useState<IAPPricePreviewResponse | null>(null);
  const [manualTerritories, setManualTerritories] = useState<Set<string>>(new Set());
  const [manualItems, setManualItems] = useState<Map<string, PricePreviewItem>>(new Map());
  const [forcedTerritories, setForcedTerritories] = useState<Set<string>>(new Set());

  const handleToggleForce = useCallback((territoryCode: string) => {
    setForcedTerritories((prev) => {
      const next = new Set(prev);
      if (next.has(territoryCode)) next.delete(territoryCode);
      else next.add(territoryCode);
      return next;
    });
  }, []);

  const { data: iaps, isLoading: iapsLoading } = useIAPs(appId);
  const { data: territories = [] } = useTerritories();
  const {
    data: priceData,
    isLoading: pricesLoading,
  } = useIAPPrices(appId, selectedIapId ?? "");
  const previewMutation = usePreviewIAPPrices();
  const applyMutation = useApplyIAPPrices();
  const syncMutation = useSyncIAPPrices();
  const syncPricePointsMutation = useSyncIAPPricePoints();
  const resolveMutation = useResolveIAPPrice();
  const { data: cacheStatus } = useIAPPricePointCacheStatus(
    appId,
    selectedIapId ?? "",
  );

  const iapOptions = useMemo(
    () =>
      (iaps ?? []).map((i: IAP) => ({
        value: String(i.id),
        label: `${i.name} (${i.product_id})`,
      })),
    [iaps],
  );

  const handleIapChange = useCallback(
    (value: string | null) => {
      setSelectedIapId(value);
      setPreview(null);
      setManualTerritories(new Set());
      setManualItems(new Map());
    },
    [],
  );

  const handlePreview = useCallback(
    (_pAppId: string, _pSubId: string, data: PricePreviewRequest) => {
      if (!selectedIapId) return;
      previewMutation.mutate(
        { appId, iapId: selectedIapId, data },
        {
          onSuccess: (result) => setPreview(result),
        },
      );
    },
    [appId, selectedIapId, previewMutation],
  );

  const handleApply = useCallback(
    (
      _pAppId: string,
      _pSubId: string,
      items: { territory_code: string; price_point_id: string; force?: boolean }[],
    ) => {
      if (!selectedIapId) return;
      applyMutation.mutate(
        { appId, iapId: selectedIapId, data: { items } },
        {
          onSuccess: () => {
            setPreview(null);
            setForcedTerritories(new Set());
          },
        },
      );
    },
    [appId, selectedIapId, applyMutation],
  );

  const handleClearPreview = useCallback(() => setPreview(null), []);

  const handleToggleManual = useCallback((territoryCode: string) => {
    setManualTerritories((prev) => {
      const next = new Set(prev);
      if (next.has(territoryCode)) {
        next.delete(territoryCode);
        setManualItems((m) => {
          const nm = new Map(m);
          nm.delete(territoryCode);
          return nm;
        });
      } else {
        next.add(territoryCode);
      }
      return next;
    });
  }, []);

  const handleManualPriceChange = useCallback(
    (territoryCode: string, price: number) => {
      if (!selectedIapId) return;
      resolveMutation.mutate(
        { appId, iapId: selectedIapId, territory_code: territoryCode, price },
        {
          onSuccess: (resolved) => {
            const existing =
              priceData?.prices.find((p) => p.territory_code === territoryCode) ??
              preview?.items.find((i) => i.territory_code === territoryCode);
            const currentPrice = existing
              ? "customer_price" in existing
                ? existing.customer_price
                : existing.current_price
              : null;
            const diffPercent =
              currentPrice && currentPrice > 0
                ? Math.round(
                    ((resolved.customer_price - currentPrice) / currentPrice) * 100 * 100,
                  ) / 100
                : null;

            setManualItems((prev) => {
              const next = new Map(prev);
              next.set(territoryCode, {
                territory_code: territoryCode,
                territory_name:
                  existing && "territory_name" in existing
                    ? existing.territory_name
                    : territoryCode,
                currency_code: resolved.currency_code,
                current_price: currentPrice ?? null,
                suggested_price: resolved.customer_price,
                nearest_apple_price: resolved.customer_price,
                price_point_id: resolved.price_point_id,
                diff_percent: diffPercent,
                would_be_skipped: false,
              });
              return next;
            });
          },
        },
      );
    },
    [appId, selectedIapId, resolveMutation, priceData, preview],
  );

  // Merge auto-preview with manual overrides
  const mergedPreviewItems = useMemo(() => {
    if (!preview && manualItems.size === 0) return null;
    const items = preview?.items ?? [];
    const merged = items.map((item) =>
      manualItems.has(item.territory_code)
        ? manualItems.get(item.territory_code)!
        : item,
    );
    for (const [tc, item] of manualItems) {
      if (!items.find((i) => i.territory_code === tc)) {
        merged.push(item);
      }
    }
    return merged.length > 0 ? merged : null;
  }, [preview, manualItems]);

  const selectedIap = useMemo(() => {
    if (!selectedIapId) return null;
    return iaps?.find((i: IAP) => String(i.id) === selectedIapId) ?? null;
  }, [iaps, selectedIapId]);

  // Map IAP prices to PricePoint[] for PriceGrid (add default vat_rate)
  const gridPrices: PricePoint[] = useMemo(
    () =>
      (priceData?.prices ?? []).map((p) => ({
        territory_code: p.territory_code,
        territory_name: p.territory_name,
        currency_code: p.currency_code,
        customer_price: p.customer_price,
        proceeds: p.proceeds,
        price_point_id: p.price_point_id,
        vat_rate: 0,
      })),
    [priceData],
  );

  const exportPrices: PriceExportItem[] = useMemo(
    () =>
      (priceData?.prices ?? []).map((p) => ({
        territory_code: p.territory_code,
        territory_name: p.territory_name,
        currency_code: p.currency_code,
        customer_price: p.customer_price,
        proceeds: p.proceeds,
      })),
    [priceData],
  );

  const handleImport = useCallback((_items: PriceImportItem[]) => {
    // Import results are displayed via notification from the hook.
  }, []);

  // Adapt preview response to PricePreviewResponse shape for PriceMultiplierPanel
  const panelPreview: PricePreviewResponse | null = useMemo(() => {
    if (!preview) return null;
    return {
      subscription_id: preview.iap_id,
      subscription_name: preview.iap_name,
      index_type: preview.index_type,
      base_price: preview.base_price,
      items: preview.items,
    };
  }, [preview]);

  if (iapsLoading) {
    return (
      <Stack gap="md">
        <Skeleton height={40} width={300} />
        <Skeleton height={200} />
      </Stack>
    );
  }

  if (!iaps || iaps.length === 0) {
    return (
      <Paper withBorder p="xl" ta="center" radius="md">
        <Stack align="center" gap="sm">
          <IconCoin size={48} color="var(--mantine-color-dimmed)" />
          <Title order={4} c="dimmed">
            No in-app purchases found
          </Title>
          <Text c="dimmed" size="sm" maw={400}>
            Sync your app data from App Store Connect to see in-app purchases
            and their pricing.
          </Text>
        </Stack>
      </Paper>
    );
  }

  return (
    <Stack gap="md">
      <Group align="flex-end">
        <Group grow align="flex-end" style={{ flex: 1, maxWidth: 400 }}>
          <Select
            label="In-App Purchase"
            placeholder="Select an IAP..."
            data={iapOptions}
            value={selectedIapId}
            onChange={handleIapChange}
            size="sm"
            searchable
          />
        </Group>
        {selectedIapId && (
          <Group gap="xs">
            <Button
              variant="light"
              size="sm"
              leftSection={<IconRefresh size={16} />}
              loading={syncMutation.isPending}
              onClick={() =>
                syncMutation.mutate({ appId, iapId: selectedIapId })
              }
            >
              Sync Prices
            </Button>
            <Tooltip
              label={
                cacheStatus?.synced_at
                  ? `Synced ${new Date(cacheStatus.synced_at).toLocaleDateString()}`
                  : "Not synced yet"
              }
              withArrow
            >
              <Button
                variant="light"
                size="sm"
                color={cacheStatus?.cached_territories ? "grape" : "orange"}
                leftSection={<IconCoins size={16} />}
                rightSection={
                  cacheStatus?.cached_territories ? (
                    <Badge
                      size="sm"
                      variant="filled"
                      color="grape"
                      circle
                      styles={{ root: { padding: 0, width: 20, height: 20, minWidth: 20 } }}
                    >
                      <IconCheck size={12} />
                    </Badge>
                  ) : null
                }
                loading={syncPricePointsMutation.isPending}
                onClick={() =>
                  syncPricePointsMutation.mutate({ appId, iapId: selectedIapId })
                }
              >
                {cacheStatus?.cached_territories
                  ? `Price Tiers (${cacheStatus.cached_territories})`
                  : "Sync Price Tiers"}
              </Button>
            </Tooltip>
          </Group>
        )}
      </Group>

      {selectedIapId && (
        <>
          <PriceMultiplierPanel
            appId={appId}
            subId={selectedIapId}
            territories={territories}
            preview={panelPreview}
            forcedTerritories={forcedTerritories}
            onPreview={handlePreview}
            onApply={handleApply}
            onClearPreview={handleClearPreview}
            isPreviewLoading={previewMutation.isPending}
            isApplyLoading={applyMutation.isPending}
          />

          <Group justify="flex-end">
            <ExportImportButtons
              subscriptionName={selectedIap?.name ?? "IAP Prices"}
              prices={exportPrices}
              onImport={handleImport}
            />
          </Group>

          <PriceGrid
            prices={gridPrices}
            previewItems={mergedPreviewItems}
            isLoading={pricesLoading}
            manualTerritories={manualTerritories}
            onToggleManual={handleToggleManual}
            onManualPriceChange={handleManualPriceChange}
            forcedTerritories={forcedTerritories}
            onToggleForce={handleToggleForce}
          />
        </>
      )}
    </Stack>
  );
}

function LocalizationsTab({ appId }: { appId: string }) {
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedSubId, setSelectedSubId] = useState<string | null>(null);
  const [selectedIapId, setSelectedIapId] = useState<string | null>(null);
  const [mode, setMode] = useState<"subscription" | "iap">("subscription");

  const { data: groups, isLoading: groupsLoading } = useSubscriptions(appId);
  const { data: iaps } = useIAPs(appId);

  const { data: subLocalizations = [], isLoading: subLocsLoading } =
    useSubscriptionLocalizations(appId, selectedSubId ?? "");
  const saveSubLocs = useSaveSubscriptionLocalizations();

  const { data: iapLocalizations = [], isLoading: iapLocsLoading } =
    useIAPLocalizations(appId, selectedIapId ?? "");
  const saveIapLocs = useSaveIAPLocalizations();

  const { data: subScreenshot, isLoading: subScreenshotLoading } =
    useSubscriptionScreenshot(appId, selectedSubId ?? "");
  const uploadSubScreenshot = useUploadSubscriptionScreenshot();

  const { data: iapScreenshot, isLoading: iapScreenshotLoading } =
    useIAPScreenshot(appId, selectedIapId ?? "");
  const uploadIapScreenshot = useUploadIAPScreenshot();

  const groupOptions = useMemo(
    () =>
      (groups ?? []).map((g: SubscriptionGroup) => ({
        value: String(g.id),
        label: g.name,
      })),
    [groups],
  );

  const selectedGroup = useMemo(
    () =>
      groups?.find(
        (g: SubscriptionGroup) => String(g.id) === selectedGroupId,
      ) ?? null,
    [groups, selectedGroupId],
  );

  const subOptions = useMemo(
    () =>
      (selectedGroup?.subscriptions ?? []).map((s: Subscription) => ({
        value: String(s.id),
        label: `${s.name} (${s.product_id})`,
      })),
    [selectedGroup],
  );

  const iapOptions = useMemo(
    () =>
      (iaps ?? []).map((i: IAP) => ({
        value: String(i.id),
        label: `${i.name} (${i.product_id})`,
      })),
    [iaps],
  );

  const handleSaveSubLocs = useCallback(
    (items: LocalizationCreate[]) => {
      if (!selectedSubId) return;
      saveSubLocs.mutate({ appId, subId: selectedSubId, localizations: items });
    },
    [appId, selectedSubId, saveSubLocs],
  );

  const handleSaveIapLocs = useCallback(
    (items: LocalizationCreate[]) => {
      if (!selectedIapId) return;
      saveIapLocs.mutate({ appId, iapId: selectedIapId, localizations: items });
    },
    [appId, selectedIapId, saveIapLocs],
  );

  if (groupsLoading) {
    return <Skeleton height={200} />;
  }

  return (
    <Stack gap="md">
      <SegmentedControl
        data={[
          { value: "subscription", label: "Subscriptions" },
          { value: "iap", label: "In-App Purchases" },
        ]}
        value={mode}
        onChange={(v) => setMode(v as "subscription" | "iap")}
        size="sm"
      />

      {mode === "subscription" && (
        <>
          <Group grow align="flex-end" style={{ maxWidth: 600 }}>
            <Select
              label="Subscription Group"
              placeholder="Select a group..."
              data={groupOptions}
              value={selectedGroupId}
              onChange={(v) => {
                setSelectedGroupId(v);
                setSelectedSubId(null);
              }}
              size="sm"
            />
            <Select
              label="Subscription"
              placeholder="Select a subscription..."
              data={subOptions}
              value={selectedSubId}
              onChange={setSelectedSubId}
              disabled={!selectedGroupId}
              size="sm"
            />
          </Group>

          {selectedSubId && (
            <>
              <ReviewScreenshotUpload
                screenshot={subScreenshot}
                onUpload={(file) =>
                  uploadSubScreenshot.mutate({
                    appId,
                    subId: selectedSubId,
                    file,
                  })
                }
                isUploading={uploadSubScreenshot.isPending}
                isLoading={subScreenshotLoading}
              />
              <LocalizationEditor
                localizations={subLocalizations}
                onSave={handleSaveSubLocs}
                isSaving={saveSubLocs.isPending}
                isLoading={subLocsLoading}
              />
            </>
          )}
        </>
      )}

      {mode === "iap" && (
        <>
          <Group grow align="flex-end" style={{ maxWidth: 400 }}>
            <Select
              label="In-App Purchase"
              placeholder="Select an IAP..."
              data={iapOptions}
              value={selectedIapId}
              onChange={setSelectedIapId}
              size="sm"
            />
          </Group>

          {selectedIapId && (
            <>
              <ReviewScreenshotUpload
                screenshot={iapScreenshot}
                onUpload={(file) =>
                  uploadIapScreenshot.mutate({
                    appId,
                    iapId: selectedIapId,
                    file,
                  })
                }
                isUploading={uploadIapScreenshot.isPending}
                isLoading={iapScreenshotLoading}
              />
              <LocalizationEditor
                localizations={iapLocalizations}
                onSave={handleSaveIapLocs}
                isSaving={saveIapLocs.isPending}
                isLoading={iapLocsLoading}
              />
            </>
          )}
        </>
      )}
    </Stack>
  );
}

export default function PricingPage() {
  const { id } = useParams<{ id: string }>();
  const appId = id ?? "";
  const { data: app, isLoading } = useApp(appId);

  return (
    <Container size="xl">
      {isLoading ? (
        <Stack gap="sm" mb="lg">
          <Skeleton height={32} width={300} />
          <Skeleton height={16} width={200} />
        </Stack>
      ) : (
        <div style={{ marginBottom: "var(--mantine-spacing-lg)" }}>
          <Title order={2}>{app?.name ?? "App"} - Price Management</Title>
          <Text c="dimmed" size="sm" mt={4}>
            Configure and manage pricing across territories.
          </Text>
        </div>
      )}

      <Tabs defaultValue="subscriptions">
        <Tabs.List>
          <Tabs.Tab
            value="subscriptions"
            leftSection={<IconReceipt size={16} />}
          >
            Subscriptions
          </Tabs.Tab>
          <Tabs.Tab value="iap" leftSection={<IconCoin size={16} />}>
            In-App Purchases
          </Tabs.Tab>
          <Tabs.Tab
            value="localizations"
            leftSection={<IconLanguage size={16} />}
          >
            Localizations
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="subscriptions" pt="md">
          <SubscriptionsTab appId={appId} />
        </Tabs.Panel>

        <Tabs.Panel value="iap" pt="md">
          <IAPsTab appId={appId} />
        </Tabs.Panel>

        <Tabs.Panel value="localizations" pt="md">
          <LocalizationsTab appId={appId} />
        </Tabs.Panel>
      </Tabs>
    </Container>
  );
}
