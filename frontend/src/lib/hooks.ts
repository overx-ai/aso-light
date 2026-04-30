import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { notifications } from "@mantine/notifications";
import api from "@/lib/api";
import type {
  ASCCredential,
  App,
  AppSyncResponse,
  CredentialTestResult,
  SubscriptionGroup,
  SubscriptionPrices,
  PricePreviewRequest,
  PricePreviewResponse,
  PriceApplyRequest,
  PriceApplyResponse,
  PricePointSyncResponse,
  PricePointCacheStatus,
  IAP,
  IAPPricesResponse,
  IAPPricePreviewResponse,
  Localization,
  LocalizationCreate,
  BulkLocalizationResponse,
  ReviewScreenshot,
  PriceResolveResponse,
  Territory,
  IndexStatus,
  AppAvailabilityResponse,
  AppAvailabilityUpdateRequest,
  GDPDataRow,
  PricePreset,
  PresetCreate,
  PresetUpdate,
  PriceExportItem,
  PriceImportResponse,
  KeywordTrackingResponse,
  KeywordSuggestion,
  KeywordSearchResult,
  KeywordRankingHistory,
  CrossLocalizationEntry,
  CompetitorApp,
  CompetitorKeywordResult,
} from "@/types";

// ---- Query Keys ----

export const queryKeys = {
  credentials: ["credentials"] as const,
  apps: ["apps"] as const,
  app: (id: string) => ["apps", id] as const,
  subscriptions: (appId: string) => ["subscriptions", appId] as const,
  subscriptionPrices: (appId: string, subId: string) =>
    ["subscriptionPrices", appId, subId] as const,
  iaps: (appId: string) => ["iaps", appId] as const,
  iapPrices: (appId: string, iapId: string) =>
    ["iapPrices", appId, iapId] as const,
  iapPricePointCacheStatus: (appId: string, iapId: string) =>
    ["iapPricePointCacheStatus", appId, iapId] as const,
  territories: ["territories"] as const,
  indexStatus: ["indexStatus"] as const,
  gdpData: ["gdpData"] as const,
  appAvailability: (appId: string) => ["appAvailability", appId] as const,
  presets: ["presets"] as const,
  keywords: (appId: string) => ["keywords", appId] as const,
  keywordRankings: (appId: string, trackingId: string) =>
    ["keywordRankings", appId, trackingId] as const,
  keywordSuggestions: (term: string, locale: string) =>
    ["keywordSuggestions", term, locale] as const,
  crossLocalization: ["crossLocalization"] as const,
  competitors: (appId: string) => ["competitors", appId] as const,
  pricePointCacheStatus: (appId: string, subId: string) =>
    ["pricePointCacheStatus", appId, subId] as const,
  subscriptionLocalizations: (appId: string, subId: string) =>
    ["subscriptionLocalizations", appId, subId] as const,
  iapLocalizations: (appId: string, iapId: string) =>
    ["iapLocalizations", appId, iapId] as const,
  subscriptionScreenshot: (appId: string, subId: string) =>
    ["subscriptionScreenshot", appId, subId] as const,
  iapScreenshot: (appId: string, iapId: string) =>
    ["iapScreenshot", appId, iapId] as const,
};

// ---- Credential Hooks ----

export function useCredentials() {
  return useQuery({
    queryKey: queryKeys.credentials,
    queryFn: async () => {
      const response = await api.get<ASCCredential[]>("/credentials");
      return response.data;
    },
  });
}

export function useCreateCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (data: {
      name: string;
      issuer_id: string;
      key_id: string;
      private_key_file: File;
    }) => {
      const formData = new FormData();
      formData.append("name", data.name);
      formData.append("issuer_id", data.issuer_id);
      formData.append("key_id", data.key_id);
      formData.append("private_key_file", data.private_key_file);

      const response = await api.post<ASCCredential>(
        "/credentials",
        formData,
        {
          headers: { "Content-Type": "multipart/form-data" },
        },
      );
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.credentials });
      notifications.show({
        title: "Credential created",
        message: "ASC credential has been added successfully.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to create credential",
        message: "Could not add the credential. Please check your inputs.",
        color: "red",
      });
    },
  });
}

export function useDeleteCredential() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/credentials/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.credentials });
      notifications.show({
        title: "Credential deleted",
        message: "ASC credential has been removed.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to delete credential",
        message: "Could not remove the credential. Please try again.",
        color: "red",
      });
    },
  });
}

export function useTestCredential() {
  return useMutation({
    mutationFn: async (id: number) => {
      const response = await api.post<CredentialTestResult>(
        `/credentials/${id}/test`,
      );
      return response.data;
    },
    onSuccess: (data) => {
      if (data.success) {
        notifications.show({
          title: "Connection successful",
          message: data.apps_count
            ? `Connected! Found ${data.apps_count} app(s).`
            : data.message,
          color: "green",
        });
      } else {
        notifications.show({
          title: "Connection failed",
          message: data.message,
          color: "red",
        });
      }
    },
    onError: () => {
      notifications.show({
        title: "Test failed",
        message: "Could not test the credential. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- App Hooks ----

export function useApps() {
  return useQuery({
    queryKey: queryKeys.apps,
    queryFn: async () => {
      const response = await api.get<App[]>("/apps");
      return response.data;
    },
  });
}

export function useSyncApps() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      const response = await api.post<AppSyncResponse>("/apps/sync");
      return response.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.apps });
      notifications.show({
        title: "Apps synced",
        message: `Synced ${data.synced} app(s) from App Store Connect.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Sync failed",
        message:
          "Could not sync apps. Make sure you have valid credentials configured.",
        color: "red",
      });
    },
  });
}

export function useApp(id: string) {
  return useQuery({
    queryKey: queryKeys.app(id),
    queryFn: async () => {
      const response = await api.get<App>(`/apps/${id}`);
      return response.data;
    },
    enabled: !!id,
  });
}

// ---- Subscription / Pricing Hooks ----

export function useSubscriptions(appId: string) {
  return useQuery({
    queryKey: queryKeys.subscriptions(appId),
    queryFn: async () => {
      const response = await api.get<SubscriptionGroup[]>(
        `/apps/${appId}/subscriptions`,
      );
      return response.data;
    },
    enabled: !!appId,
  });
}

export function useSubscriptionPrices(appId: string, subId: string) {
  return useQuery({
    queryKey: queryKeys.subscriptionPrices(appId, subId),
    queryFn: async () => {
      const response = await api.get<SubscriptionPrices>(
        `/apps/${appId}/subscriptions/${subId}/prices`,
      );
      return response.data;
    },
    enabled: !!appId && !!subId,
  });
}

export function useSyncSubscriptionPrices() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      subId,
    }: {
      appId: string;
      subId: string;
    }) => {
      const response = await api.post<{
        prices_synced: number;
        price_points_synced: number;
      }>(`/apps/${appId}/subscriptions/${subId}/sync`);
      return response.data;
    },
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.subscriptionPrices(variables.appId, variables.subId),
      });
      notifications.show({
        title: "Sync complete",
        message: `Synced ${data.prices_synced} prices and ${data.price_points_synced} price points from Apple.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Sync failed",
        message:
          "Could not sync from Apple. Check your credentials and try again.",
        color: "red",
      });
    },
  });
}

export function usePreviewPrices() {
  return useMutation({
    mutationFn: async ({
      appId,
      subId,
      data,
    }: {
      appId: string;
      subId: string;
      data: PricePreviewRequest;
    }) => {
      const response = await api.post<PricePreviewResponse>(
        `/apps/${appId}/subscriptions/${subId}/prices/preview`,
        data,
      );
      return response.data;
    },
    onError: () => {
      notifications.show({
        title: "Preview failed",
        message: "Could not generate price preview. Please try again.",
        color: "red",
      });
    },
  });
}

export function useApplyPrices() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      subId,
      data,
    }: {
      appId: string;
      subId: string;
      data: PriceApplyRequest;
    }) => {
      const response = await api.post<PriceApplyResponse>(
        `/apps/${appId}/subscriptions/${subId}/prices/apply`,
        data,
      );
      return response.data;
    },
    onSuccess: (result, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.subscriptionPrices(
          variables.appId,
          variables.subId,
        ),
      });
      const skippedMsg =
        result.skipped > 0
          ? ` ${result.skipped} skipped (exceeded safety limit).`
          : "";
      notifications.show({
        title: "Prices applied",
        message: `Applied ${result.applied} price(s) to App Store Connect.${
          result.failed > 0 ? ` ${result.failed} failed.` : ""
        }${skippedMsg}`,
        color: result.skipped > 0 || result.failed > 0 ? "yellow" : "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Apply failed",
        message: "Could not apply prices to App Store Connect. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- Price Points Sync ----

export function usePricePointCacheStatus(appId: string, subId: string) {
  return useQuery({
    queryKey: queryKeys.pricePointCacheStatus(appId, subId),
    queryFn: async () => {
      const response = await api.get<PricePointCacheStatus>(
        `/apps/${appId}/subscriptions/${subId}/price-points/status`,
      );
      return response.data;
    },
    enabled: !!appId && !!subId,
  });
}

export function useSyncPricePoints() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      subId,
    }: {
      appId: string;
      subId: string;
    }) => {
      const response = await api.post<PricePointSyncResponse>(
        `/apps/${appId}/subscriptions/${subId}/price-points/sync`,
      );
      return response.data;
    },
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.pricePointCacheStatus(
          variables.appId,
          variables.subId,
        ),
      });
      // Currency displayed in the price grid is derived from the
      // price-points cache, so invalidate the prices query too.
      queryClient.invalidateQueries({
        queryKey: queryKeys.subscriptionPrices(
          variables.appId,
          variables.subId,
        ),
      });
      notifications.show({
        title: "Price points synced",
        message: `Cached ${data.price_points_total} price points across ${data.territories_synced} territories.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Sync failed",
        message: "Could not sync price points from Apple. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- IAP Hooks ----

export function useIAPs(appId: string) {
  return useQuery({
    queryKey: queryKeys.iaps(appId),
    queryFn: async () => {
      const response = await api.get<IAP[]>(`/apps/${appId}/iaps`);
      return response.data;
    },
    enabled: !!appId,
  });
}

export function useIAPPrices(appId: string, iapId: string) {
  return useQuery({
    queryKey: queryKeys.iapPrices(appId, iapId),
    queryFn: async () => {
      const response = await api.get<IAPPricesResponse>(
        `/apps/${appId}/iaps/${iapId}/prices`,
      );
      return response.data;
    },
    enabled: !!appId && !!iapId,
  });
}

export function useSyncIAPPrices() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      iapId,
    }: {
      appId: string;
      iapId: string;
    }) => {
      const response = await api.post<{
        prices_synced: number;
        price_points_synced: number;
      }>(`/apps/${appId}/iaps/${iapId}/sync`);
      return response.data;
    },
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.iapPrices(variables.appId, variables.iapId),
      });
      notifications.show({
        title: "Sync complete",
        message: `Synced ${data.prices_synced} prices from Apple.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Sync failed",
        message:
          "Could not sync from Apple. Check your credentials and try again.",
        color: "red",
      });
    },
  });
}

export function usePreviewIAPPrices() {
  return useMutation({
    mutationFn: async ({
      appId,
      iapId,
      data,
    }: {
      appId: string;
      iapId: string;
      data: PricePreviewRequest;
    }) => {
      const response = await api.post<IAPPricePreviewResponse>(
        `/apps/${appId}/iaps/${iapId}/prices/preview`,
        data,
      );
      return response.data;
    },
    onError: () => {
      notifications.show({
        title: "Preview failed",
        message: "Could not generate price preview. Please try again.",
        color: "red",
      });
    },
  });
}

export function useApplyIAPPrices() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      iapId,
      data,
    }: {
      appId: string;
      iapId: string;
      data: PriceApplyRequest;
    }) => {
      const response = await api.post<PriceApplyResponse>(
        `/apps/${appId}/iaps/${iapId}/prices/apply`,
        data,
      );
      return response.data;
    },
    onSuccess: (result, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.iapPrices(variables.appId, variables.iapId),
      });
      const skippedMsg =
        result.skipped > 0
          ? ` ${result.skipped} skipped (exceeded safety limit).`
          : "";
      notifications.show({
        title: "Prices applied",
        message: `Applied ${result.applied} price(s) to App Store Connect.${
          result.failed > 0 ? ` ${result.failed} failed.` : ""
        }${skippedMsg}`,
        color: result.skipped > 0 || result.failed > 0 ? "yellow" : "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Apply failed",
        message: "Could not apply prices to App Store Connect. Please try again.",
        color: "red",
      });
    },
  });
}

export function useResolveIAPPrice() {
  return useMutation({
    mutationFn: async ({
      appId,
      iapId,
      territory_code,
      price,
    }: {
      appId: string;
      iapId: string;
      territory_code: string;
      price: number;
    }) => {
      const response = await api.post<PriceResolveResponse>(
        `/apps/${appId}/iaps/${iapId}/prices/resolve`,
        { territory_code, price },
      );
      return response.data;
    },
  });
}

export function useIAPPricePointCacheStatus(appId: string, iapId: string) {
  return useQuery({
    queryKey: queryKeys.iapPricePointCacheStatus(appId, iapId),
    queryFn: async () => {
      const response = await api.get<PricePointCacheStatus>(
        `/apps/${appId}/iaps/${iapId}/price-points/status`,
      );
      return response.data;
    },
    enabled: !!appId && !!iapId,
  });
}

export function useSyncIAPPricePoints() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      iapId,
    }: {
      appId: string;
      iapId: string;
    }) => {
      const response = await api.post<PricePointSyncResponse>(
        `/apps/${appId}/iaps/${iapId}/price-points/sync`,
      );
      return response.data;
    },
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.iapPricePointCacheStatus(
          variables.appId,
          variables.iapId,
        ),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.iapPrices(variables.appId, variables.iapId),
      });
      notifications.show({
        title: "Price points synced",
        message: `Cached ${data.price_points_total} price points across ${data.territories_synced} territories.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Sync failed",
        message: "Could not sync price points from Apple. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- Localization Hooks ----

export function useSubscriptionLocalizations(appId: string, subId: string) {
  return useQuery({
    queryKey: queryKeys.subscriptionLocalizations(appId, subId),
    queryFn: async () => {
      const response = await api.get<Localization[]>(
        `/apps/${appId}/subscriptions/${subId}/localizations`,
      );
      return response.data;
    },
    enabled: !!appId && !!subId,
  });
}

export function useSaveSubscriptionLocalizations() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      subId,
      localizations,
    }: {
      appId: string;
      subId: string;
      localizations: LocalizationCreate[];
    }) => {
      const response = await api.post<BulkLocalizationResponse>(
        `/apps/${appId}/subscriptions/${subId}/localizations/bulk`,
        { localizations },
      );
      return response.data;
    },
    onSuccess: (result, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.subscriptionLocalizations(
          variables.appId,
          variables.subId,
        ),
      });
      notifications.show({
        title: "Localizations saved",
        message: `Created ${result.created}, updated ${result.updated} localizations.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Save failed",
        message: "Could not save localizations. Please try again.",
        color: "red",
      });
    },
  });
}

export function useIAPLocalizations(appId: string, iapId: string) {
  return useQuery({
    queryKey: queryKeys.iapLocalizations(appId, iapId),
    queryFn: async () => {
      const response = await api.get<Localization[]>(
        `/apps/${appId}/iaps/${iapId}/localizations`,
      );
      return response.data;
    },
    enabled: !!appId && !!iapId,
  });
}

export function useSaveIAPLocalizations() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      iapId,
      localizations,
    }: {
      appId: string;
      iapId: string;
      localizations: LocalizationCreate[];
    }) => {
      const response = await api.post<BulkLocalizationResponse>(
        `/apps/${appId}/iaps/${iapId}/localizations/bulk`,
        { localizations },
      );
      return response.data;
    },
    onSuccess: (result, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.iapLocalizations(variables.appId, variables.iapId),
      });
      notifications.show({
        title: "Localizations saved",
        message: `Created ${result.created}, updated ${result.updated} localizations.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Save failed",
        message: "Could not save localizations. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- Price Resolve Hook ----

export function useResolvePrice() {
  return useMutation({
    mutationFn: async ({
      appId,
      subId,
      territory_code,
      price,
    }: {
      appId: string;
      subId: string;
      territory_code: string;
      price: number;
    }) => {
      const response = await api.post<PriceResolveResponse>(
        `/apps/${appId}/subscriptions/${subId}/prices/resolve`,
        { territory_code, price },
      );
      return response.data;
    },
  });
}

// ---- Review Screenshot Hooks ----

export function useSubscriptionScreenshot(appId: string, subId: string) {
  return useQuery({
    queryKey: queryKeys.subscriptionScreenshot(appId, subId),
    queryFn: async () => {
      const response = await api.get<ReviewScreenshot | null>(
        `/apps/${appId}/subscriptions/${subId}/review-screenshot`,
      );
      return response.data;
    },
    enabled: !!appId && !!subId,
  });
}

export function useUploadSubscriptionScreenshot() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      subId,
      file,
    }: {
      appId: string;
      subId: string;
      file: File;
    }) => {
      const formData = new FormData();
      formData.append("file", file);
      const response = await api.post<ReviewScreenshot>(
        `/apps/${appId}/subscriptions/${subId}/review-screenshot`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return response.data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.subscriptionScreenshot(
          variables.appId,
          variables.subId,
        ),
      });
      notifications.show({
        title: "Screenshot uploaded",
        message: "Review screenshot uploaded successfully.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Upload failed",
        message: "Could not upload screenshot. Please try again.",
        color: "red",
      });
    },
  });
}

export function useIAPScreenshot(appId: string, iapId: string) {
  return useQuery({
    queryKey: queryKeys.iapScreenshot(appId, iapId),
    queryFn: async () => {
      const response = await api.get<ReviewScreenshot | null>(
        `/apps/${appId}/iaps/${iapId}/review-screenshot`,
      );
      return response.data;
    },
    enabled: !!appId && !!iapId,
  });
}

export function useUploadIAPScreenshot() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      iapId,
      file,
    }: {
      appId: string;
      iapId: string;
      file: File;
    }) => {
      const formData = new FormData();
      formData.append("file", file);
      const response = await api.post<ReviewScreenshot>(
        `/apps/${appId}/iaps/${iapId}/review-screenshot`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return response.data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.iapScreenshot(variables.appId, variables.iapId),
      });
      notifications.show({
        title: "Screenshot uploaded",
        message: "Review screenshot uploaded successfully.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Upload failed",
        message: "Could not upload screenshot. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- Territory & Index Hooks ----

export function useTerritories() {
  return useQuery({
    queryKey: queryKeys.territories,
    queryFn: async () => {
      const response = await api.get<Territory[]>("/territories");
      return response.data;
    },
    staleTime: 1000 * 60 * 60, // territories rarely change
  });
}

export function useIndexStatus() {
  return useQuery({
    queryKey: queryKeys.indexStatus,
    queryFn: async () => {
      const response = await api.get<IndexStatus>("/indices/status");
      return response.data;
    },
  });
}

export function useRefreshIndices() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      const response = await api.post<{ refreshed: Record<string, number> }>(
        "/indices/refresh",
      );
      return response.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.indexStatus });
      const total = Object.values(data.refreshed).reduce((sum, n) => sum + n, 0);
      notifications.show({
        title: "Indices refreshed",
        message: `Updated ${total} index entries across all types.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Refresh failed",
        message: "Could not refresh economic indices. Please try again.",
        color: "red",
      });
    },
  });
}

export function useGDPData() {
  return useQuery({
    queryKey: queryKeys.gdpData,
    queryFn: async () => {
      const response = await api.get<GDPDataRow[]>("/indices/gdp");
      return response.data;
    },
    staleTime: 1000 * 60 * 60,
  });
}

export function useRefreshGDP() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const response = await api.post<{ refreshed: Record<string, number> }>(
        "/indices/refresh",
        null,
        { params: { index_type: "gdp_per_capita_ppp" } },
      );
      return response.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.gdpData });
      queryClient.invalidateQueries({ queryKey: queryKeys.indexStatus });
      const count = data.refreshed.gdp_per_capita_ppp ?? 0;
      notifications.show({
        title: "GDP data refreshed",
        message: `Updated ${count} territories.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "GDP refresh failed",
        message: "Could not refresh GDP data. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- Preset Hooks ----

export function usePresets() {
  return useQuery({
    queryKey: queryKeys.presets,
    queryFn: async () => {
      const response = await api.get<PricePreset[]>("/presets");
      return response.data;
    },
  });
}

export function useCreatePreset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (data: PresetCreate) => {
      const response = await api.post<PricePreset>("/presets", data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.presets });
      notifications.show({
        title: "Preset saved",
        message: "Price preset has been saved successfully.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to save preset",
        message: "Could not save the preset. Please try again.",
        color: "red",
      });
    },
  });
}

export function useUpdatePreset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, data }: { id: number; data: PresetUpdate }) => {
      const response = await api.put<PricePreset>(`/presets/${id}`, data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.presets });
      notifications.show({
        title: "Preset updated",
        message: "Price preset has been updated.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to update preset",
        message: "Could not update the preset. Please try again.",
        color: "red",
      });
    },
  });
}

export function useDeletePreset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/presets/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.presets });
      notifications.show({
        title: "Preset deleted",
        message: "Price preset has been removed.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to delete preset",
        message: "Could not remove the preset. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- Keyword Hooks ----

export function useTrackedKeywords(appId: string) {
  return useQuery({
    queryKey: queryKeys.keywords(appId),
    queryFn: async () => {
      const response = await api.get<KeywordTrackingResponse[]>(
        `/apps/${appId}/keywords`,
      );
      return response.data;
    },
    enabled: !!appId,
  });
}

export function useAddKeyword() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      text,
      locale,
    }: {
      appId: string;
      text: string;
      locale: string;
    }) => {
      const response = await api.post<KeywordTrackingResponse>(
        `/apps/${appId}/keywords`,
        { text, locale },
      );
      return response.data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.keywords(variables.appId),
      });
      notifications.show({
        title: "Keyword added",
        message: `Now tracking "${variables.text}".`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to add keyword",
        message:
          "Could not add keyword. It may already be tracked.",
        color: "red",
      });
    },
  });
}

export function useRemoveKeyword() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      trackingId,
    }: {
      appId: string;
      trackingId: number;
    }) => {
      await api.delete(`/apps/${appId}/keywords/${trackingId}`);
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.keywords(variables.appId),
      });
      notifications.show({
        title: "Keyword removed",
        message: "Keyword tracking has been stopped.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to remove keyword",
        message: "Could not remove keyword tracking. Please try again.",
        color: "red",
      });
    },
  });
}

export function useKeywordRankings(appId: string, trackingId: string) {
  return useQuery({
    queryKey: queryKeys.keywordRankings(appId, trackingId),
    queryFn: async () => {
      const response = await api.get<KeywordRankingHistory[]>(
        `/apps/${appId}/keywords/${trackingId}/rankings`,
      );
      return response.data;
    },
    enabled: !!appId && !!trackingId,
  });
}

export function useRefreshKeywordRankings() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ appId }: { appId: string }) => {
      const response = await api.post<{ recorded: number }>(
        `/apps/${appId}/keywords/refresh`,
      );
      return response.data;
    },
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.keywords(variables.appId),
      });
      notifications.show({
        title: "Rankings refreshed",
        message: `Recorded ${data.recorded} ranking(s).`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Refresh failed",
        message: "Could not refresh keyword rankings. Please try again.",
        color: "red",
      });
    },
  });
}

export function useKeywordSuggestions(term: string, locale: string) {
  return useQuery({
    queryKey: queryKeys.keywordSuggestions(term, locale),
    queryFn: async () => {
      const response = await api.get<KeywordSuggestion[]>(
        "/keywords/suggestions",
        { params: { term, locale } },
      );
      return response.data;
    },
    enabled: term.length >= 2,
    staleTime: 1000 * 60 * 5,
  });
}

export function useKeywordSearch() {
  return useMutation({
    mutationFn: async ({
      term,
      country,
    }: {
      term: string;
      country: string;
    }) => {
      const response = await api.post<KeywordSearchResult[]>(
        "/keywords/search",
        null,
        { params: { term, country } },
      );
      return response.data;
    },
    onError: () => {
      notifications.show({
        title: "Search failed",
        message: "Could not search keywords. Please try again.",
        color: "red",
      });
    },
  });
}

export function useCrossLocalization() {
  return useQuery({
    queryKey: queryKeys.crossLocalization,
    queryFn: async () => {
      const response = await api.get<CrossLocalizationEntry[]>(
        "/keywords/cross-localization",
      );
      return response.data;
    },
    staleTime: 1000 * 60 * 60,
  });
}

export function useCompetitors(appId: string) {
  return useQuery({
    queryKey: queryKeys.competitors(appId),
    queryFn: async () => {
      const response = await api.get<CompetitorApp[]>(
        `/apps/${appId}/competitors`,
      );
      return response.data;
    },
    enabled: !!appId,
  });
}

export function useAddCompetitor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      asc_app_id,
      name,
      bundle_id,
    }: {
      appId: string;
      asc_app_id: string;
      name: string;
      bundle_id?: string;
    }) => {
      const response = await api.post<CompetitorApp>(
        `/apps/${appId}/competitors`,
        { asc_app_id, name, bundle_id },
      );
      return response.data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.competitors(variables.appId),
      });
      notifications.show({
        title: "Competitor added",
        message: `Now tracking competitor "${variables.name}".`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to add competitor",
        message: "Could not add the competitor. Please try again.",
        color: "red",
      });
    },
  });
}

export function useRemoveCompetitor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      appId,
      competitorId,
    }: {
      appId: string;
      competitorId: number;
    }) => {
      await api.delete(`/apps/${appId}/competitors/${competitorId}`);
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.competitors(variables.appId),
      });
      notifications.show({
        title: "Competitor removed",
        message: "Competitor has been removed.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Failed to remove competitor",
        message: "Could not remove the competitor. Please try again.",
        color: "red",
      });
    },
  });
}

export function useCompetitorKeywords() {
  return useMutation({
    mutationFn: async ({
      appId,
      competitorId,
    }: {
      appId: string;
      competitorId: number;
    }) => {
      const response = await api.post<CompetitorKeywordResult[]>(
        `/apps/${appId}/competitors/${competitorId}/keywords`,
      );
      return response.data;
    },
    onError: () => {
      notifications.show({
        title: "Check failed",
        message:
          "Could not check competitor keywords. Please try again.",
        color: "red",
      });
    },
  });
}

// ---- Export/Import Hooks ----

export function useExportPrices() {
  return useMutation({
    mutationFn: async ({
      subscriptionName,
      format,
      prices,
    }: {
      subscriptionName: string;
      format: "xlsx" | "csv";
      prices: PriceExportItem[];
    }) => {
      const response = await api.post(
        "/prices/export",
        {
          subscription_name: subscriptionName,
          format,
          prices,
        },
        { responseType: "blob" },
      );

      const extension = format === "csv" ? "csv" : "xlsx";
      const filename = `${subscriptionName.replace(/\s+/g, "_")}.${extension}`;
      const blob = new Blob([response.data]);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    },
    onSuccess: () => {
      notifications.show({
        title: "Export complete",
        message: "Price file has been downloaded.",
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Export failed",
        message: "Could not export prices. Please try again.",
        color: "red",
      });
    },
  });
}

export function useImportPrices() {
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const response = await api.post<PriceImportResponse>(
        "/prices/import",
        formData,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return response.data;
    },
    onSuccess: (data) => {
      notifications.show({
        title: "Import complete",
        message: `Imported ${data.count} price(s) from file.`,
        color: "green",
      });
    },
    onError: () => {
      notifications.show({
        title: "Import failed",
        message: "Could not import prices. Check the file format and try again.",
        color: "red",
      });
    },
  });
}

// ---- App Availability Hooks ----

export function useAppAvailability(appId: string) {
  return useQuery({
    queryKey: queryKeys.appAvailability(appId),
    queryFn: async () => {
      const response = await api.get<AppAvailabilityResponse>(
        `/apps/${appId}/availability`,
      );
      return response.data;
    },
    enabled: !!appId,
  });
}

export function useUpdateAppAvailability() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      appId,
      data,
    }: {
      appId: string;
      data: AppAvailabilityUpdateRequest;
    }) => {
      const response = await api.put<AppAvailabilityResponse>(
        `/apps/${appId}/availability`,
        data,
      );
      return response.data;
    },
    onSuccess: (data, variables) => {
      queryClient.setQueryData(
        queryKeys.appAvailability(variables.appId),
        data,
      );
      const disabled = data.territories.filter((t) => !t.available).length;
      notifications.show({
        title: "Availability updated",
        message: `Snapshot saved · ${disabled} territories disabled.`,
        color: "green",
      });
    },
    onError: (error: unknown) => {
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Could not update availability. Please try again.";
      notifications.show({
        title: "Update failed",
        message: detail,
        color: "red",
      });
    },
  });
}
