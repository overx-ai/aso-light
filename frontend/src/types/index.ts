export interface User {
  id: number;
  email: string;
  name: string;
}

export interface ASCCredential {
  id: number;
  name: string;
  issuer_id: string;
  key_id: string;
  created_at: string;
}

export interface App {
  id: number;
  name: string;
  bundle_id: string;
  platform: string;
  icon_url: string | null;
  asc_app_id: string;
}

export interface Territory {
  id: number;
  code: string;
  name: string;
  currency_code: string;
  vat_rate: number;
}

export interface ASCCredentialCreate {
  name: string;
  issuer_id: string;
  key_id: string;
  private_key_file: File;
}

export interface AppSyncResponse {
  synced: number;
  apps: App[];
}

export interface CredentialTestResult {
  success: boolean;
  message: string;
  apps_count?: number;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  name: string;
}

// ---- Pricing Types ----

export interface SubscriptionGroup {
  id: number;
  asc_group_id: string;
  name: string;
  app_id: number;
  subscriptions: Subscription[];
}

export interface Subscription {
  id: number;
  asc_subscription_id: string;
  name: string;
  product_id: string;
  group_id: number;
}

export interface PricePoint {
  territory_code: string;
  territory_name: string;
  currency_code: string;
  customer_price: number;
  proceeds: number;
  price_point_id: string | null;
  vat_rate: number;
}

export interface SubscriptionPrices {
  subscription_id: number;
  subscription_name: string;
  product_id: string;
  prices: PricePoint[];
}

export interface PricePreviewRequest {
  index_type: string;
  base_price: number;
  base_territory_code: string;
  apply_vat: boolean;
  charming_mode: string;
}

export interface PricePreviewItem {
  territory_code: string;
  territory_name: string;
  currency_code: string;
  current_price: number | null;
  suggested_price: number;
  nearest_apple_price: number | null;
  price_point_id: string | null;
  diff_percent: number | null;
  would_be_skipped: boolean;
}

export interface PricePreviewResponse {
  subscription_id: number;
  subscription_name: string;
  index_type: string;
  base_price: number;
  items: PricePreviewItem[];
}

export interface PriceApplyRequest {
  items: { territory_code: string; price_point_id: string }[];
}

export interface PriceApplySkippedItem {
  territory_code: string;
  reason: string;
  current_price: number;
  new_price: number;
  diff_percent: number;
}

export interface PriceApplyResponse {
  applied: number;
  failed: number;
  skipped: number;
  errors: string[];
  skipped_items: PriceApplySkippedItem[];
}

export interface PricePointSyncResponse {
  territories_synced: number;
  price_points_total: number;
}

export interface PricePointCacheStatus {
  cached_territories: number;
  synced_at: string | null;
}

export interface IAP {
  id: number;
  asc_iap_id: string;
  name: string;
  product_id: string;
  iap_type: string;
  app_id: number;
}

export interface IAPPricePoint {
  territory_code: string;
  territory_name: string;
  currency_code: string;
  customer_price: number;
  proceeds: number;
  price_point_id: string | null;
}

export interface IAPPricesResponse {
  iap_id: number;
  iap_name: string;
  product_id: string;
  prices: IAPPricePoint[];
}

export interface IAPPricePreviewResponse {
  iap_id: number;
  iap_name: string;
  index_type: string;
  base_price: number;
  items: PricePreviewItem[];
}

// ---- Localization Types ----

export interface Localization {
  id: string;
  locale: string;
  name: string;
  description: string;
}

export interface LocalizationCreate {
  locale: string;
  name: string;
  description: string;
}

export interface BulkLocalizationResponse {
  created: number;
  updated: number;
  localizations: Localization[];
}

export interface PriceResolveResponse {
  territory_code: string;
  currency_code: string;
  customer_price: number;
  proceeds: number;
  price_point_id: string;
}

export interface ReviewScreenshot {
  id: string;
  file_name: string;
  file_size: number;
  image_url: string | null;
}

export interface IndexStatus {
  [key: string]: {
    last_refresh: string | null;
    count: number;
  };
}

// ---- Price Preset Types ----

export interface PricePreset {
  id: number;
  name: string;
  base_territory_code: string;
  base_price: number;
  index_type: string;
  apply_vat: boolean;
  charming_mode: string;
  created_at: string;
}

export interface PresetCreate {
  name: string;
  base_territory_code: string;
  base_price: number;
  index_type: string;
  apply_vat: boolean;
  charming_mode: string;
}

export interface PresetUpdate {
  name?: string;
  base_territory_code?: string;
  base_price?: number;
  index_type?: string;
  apply_vat?: boolean;
  charming_mode?: string;
}

// ---- Keyword Types ----

export interface KeywordResponse {
  id: number;
  text: string;
  locale: string;
  popularity: number | null;
  popularity_updated_at: string | null;
}

export interface KeywordTrackingResponse {
  id: number;
  keyword: KeywordResponse;
  app_id: number;
  latest_rank: number | null;
  rank_change: number | null;
  added_at: string;
}

export interface RankDataPoint {
  date: string;
  rank: number | null;
  territory_code: string;
}

export interface KeywordRankingHistory {
  keyword_text: string;
  territory_code: string;
  data_points: RankDataPoint[];
}

export interface KeywordSuggestion {
  term: string;
}

export interface KeywordSearchResult {
  position: number;
  app_id: string;
  name: string;
  bundle_id: string;
  icon_url: string;
}

export interface CrossLocalizationEntry {
  territory_code: string;
  locale: string;
  is_indexed: boolean;
}

export interface CompetitorApp {
  id: number;
  asc_app_id: string;
  name: string;
  bundle_id: string | null;
  app_id: number;
}

export interface CompetitorKeywordResult {
  keyword_text: string;
  competitor_rank: number | null;
  our_rank: number | null;
  territory_code: string;
}

// ---- Export/Import Types ----

export interface PriceExportItem {
  territory_code: string;
  territory_name: string;
  currency_code: string;
  customer_price: number;
  proceeds: number;
}

export interface PriceImportItem {
  territory_code: string;
  customer_price: number;
}

export interface PriceImportResponse {
  items: PriceImportItem[];
  count: number;
}
