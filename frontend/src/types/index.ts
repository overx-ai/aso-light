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

export type GDPTier = "top" | "mid" | "low" | "special";

export interface GDPBracketConfig {
  tier_prices_usd: Record<GDPTier, number>;
  tier_thresholds_usd: { top_min: number; mid_min: number };
  manual_overrides: Record<string, GDPTier>;
  special_territories: string[];
}

export interface GDPDataRow {
  territory_code: string;
  territory_name: string;
  currency_code: string;
  gdp_per_capita_ppp: number | null;
}

export interface PricePreviewRequest {
  index_type: string;
  base_price: number;
  base_territory_code: string;
  apply_vat: boolean;
  charming_mode: string;
  gdp_config?: GDPBracketConfig | null;
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

export interface PriceApplyItem {
  territory_code: string;
  price_point_id: string;
  force?: boolean;
}

export interface IntroOfferApplyConfig {
  duration: IntroOfferDuration;
  number_of_periods: number;
}

export interface PriceApplyRequest {
  items: PriceApplyItem[];
  intro_offer?: IntroOfferApplyConfig | null;
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
  intro_offer_synced?: boolean;
  intro_offer_error?: string | null;
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

// ---- App Availability Types ----

export interface TerritoryAvailability {
  territory_code: string;
  territory_name: string;
  available: boolean;
  preorder_enabled: boolean;
}

export interface AppAvailabilityResponse {
  available_in_new_territories: boolean;
  territories: TerritoryAvailability[];
}

export interface AppAvailabilityUpdateRequest {
  available_in_new_territories: boolean;
  disabled_territories: string[];
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
  config: Record<string, unknown> | null;
  created_at: string;
}

export interface PresetCreate {
  name: string;
  base_territory_code: string;
  base_price: number;
  index_type: string;
  apply_vat: boolean;
  charming_mode: string;
  config?: Record<string, unknown> | null;
}

export interface PresetUpdate {
  name?: string;
  base_territory_code?: string;
  base_price?: number;
  index_type?: string;
  apply_vat?: boolean;
  charming_mode?: string;
  config?: Record<string, unknown> | null;
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

// ---- Subscription / group write paths ----

export type SubscriptionPeriod =
  | "ONE_WEEK"
  | "ONE_MONTH"
  | "TWO_MONTHS"
  | "THREE_MONTHS"
  | "SIX_MONTHS"
  | "ONE_YEAR";

// Intro-offer durations include shorter codes (THREE_DAYS, TWO_WEEKS)
// that the regular subscriptionPeriod enum does not support.
export type IntroOfferDuration =
  | "THREE_DAYS"
  | "ONE_WEEK"
  | "TWO_WEEKS"
  | "ONE_MONTH"
  | "TWO_MONTHS"
  | "THREE_MONTHS"
  | "SIX_MONTHS"
  | "ONE_YEAR";

export type IntroOfferMode = "FREE_TRIAL" | "PAY_AS_YOU_GO" | "PAY_UP_FRONT";

export interface SubscriptionGroupCreate {
  reference_name: string;
}

export interface SubscriptionGroupUpdate {
  reference_name: string;
}

export interface GroupLocalization {
  id: string;
  locale: string;
  name: string;
  custom_app_name: string | null;
  state: string | null;
}

export interface GroupLocalizationCreate {
  locale: string;
  name: string;
  custom_app_name?: string | null;
}

export interface GroupLocalizationUpdate {
  name: string;
  custom_app_name?: string | null;
}

export interface SubscriptionCreate {
  product_id: string;
  name: string;
  period: SubscriptionPeriod;
  family_sharable: boolean;
  available_in_all_territories: boolean;
  group_level: number;
  review_note?: string | null;
}

export interface SubscriptionUpdate {
  name?: string | null;
  group_level?: number | null;
  family_sharable?: boolean | null;
  review_note?: string | null;
}

export interface IntroOffer {
  id: string;
  territory_code: string | null;
  offer_mode: IntroOfferMode;
  duration: IntroOfferDuration;
  number_of_periods: number;
  price_point_id: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface SubscriptionAvailability {
  subscription_id: number;
  territories: string[];
}

export interface IntroOfferCreate {
  territory_code?: string | null;
  offer_mode: IntroOfferMode;
  duration: IntroOfferDuration;
  number_of_periods: number;
  price_point_id?: string | null;
  start_date?: string | null;
  end_date?: string | null;
}

// ---- Metadata Editor + Cross-Localization Types ----

export type MetadataKind = "app_info" | "version";

export interface AppMetadataLocalization {
  id: number;
  app_id: number;
  kind: MetadataKind;
  asc_localization_id: string;
  asc_parent_id: string;
  locale: string;
  name: string | null;
  subtitle: string | null;
  description: string | null;
  keywords: string | null;
  promotional_text: string | null;
  whats_new: string | null;
  marketing_url: string | null;
  support_url: string | null;
  privacy_policy_url: string | null;
  synced_at: string;
}

export interface AppMetadataState {
  editable_version_id: string | null;
  editable_version_state: string | null;
  app_info_id: string | null;
  editable_fields: string[];
  last_synced_at: string;
}

export interface AppMetadataSnapshot {
  app_info: AppMetadataLocalization[];
  versions: AppMetadataLocalization[];
  state: AppMetadataState;
}

export interface BulkPreviewItem {
  locale: string;
  current_value: string | null;
  new_value: string | null;
  char_overflow_by: number;
  would_skip: boolean;
  reason: string | null;
}

export interface BulkPreviewOut {
  items: BulkPreviewItem[];
}

export interface BulkPreviewIn {
  field: string;
  value: string | null;
  target_locales: string[];
}

export interface BulkApplyIn extends BulkPreviewIn {
  force?: boolean;
}

export type BulkApplyStatus = "applied" | "skipped" | "failed";

export interface BulkApplyResult {
  locale: string;
  status: BulkApplyStatus;
  error: string | null;
}

export interface BulkApplyOut {
  applied: number;
  skipped: number;
  failed: number;
  results: BulkApplyResult[];
}

export interface TranslateIn {
  source_locale: string;
  target_locales: string[];
  fields: string[];
}

export interface TranslateSuggestionItem {
  locale: string;
  field: string;
  suggestion: string;
  cached: boolean;
}

export interface TranslateOut {
  items: TranslateSuggestionItem[];
}

export type KeywordPlacement = "title" | "subtitle" | "keywords" | "none";

export interface KeywordCoverageItem {
  keyword: string;
  locale: string;
  placement: KeywordPlacement;
}

export interface KeywordCoverageOut {
  items: KeywordCoverageItem[];
}

export interface CrossLocalizationGridItem {
  territory_code: string;
  locale: string;
  gdp_per_capita_usd: number | null;
  has_metadata: boolean;
}

export interface CrossLocalizationGridOut {
  items: CrossLocalizationGridItem[];
}

// ---- Reviews ----

export interface ReviewResponseOut {
  id: string;
  body: string;
  last_modified_date: string | null;
  state: string | null;
}

export interface ReviewOut {
  id: string;
  rating: number;
  title: string | null;
  body: string | null;
  territory: string | null;
  reviewer_nickname: string | null;
  created_date: string | null;
  response: ReviewResponseOut | null;
}

export interface ReviewListOut {
  items: ReviewOut[];
  next_cursor: string | null;
}

export type ReplyTone = "neutral" | "apologetic" | "appreciative";

export interface DraftReplyIn {
  tone: ReplyTone;
}

export interface DraftReplyOut {
  suggestion: string;
  locale: string;
}

export interface TranslateReviewIn {
  target_locale: string;
}

export interface TranslateReviewOut {
  translation: string;
  cached: boolean;
}

export interface ReplyIn {
  body: string;
}

// ---- Keyword visibility tracker ----

export interface VisibilityResultOut {
  position: number;
  track_id: string;
  name: string;
  bundle_id: string;
  icon_url: string;
}

export interface VisibilitySnapshotOut {
  id: number;
  polled_at: string;
  results_count: number;
  results: VisibilityResultOut[];
}

export interface VisibilityWatchOut {
  id: number;
  text: string;
  country: string;
  last_polled_at: string | null;
  latest_snapshot: VisibilitySnapshotOut | null;
}

export interface VisibilityWatchListOut {
  items: VisibilityWatchOut[];
}

export interface VisibilitySnapshotListOut {
  items: VisibilitySnapshotOut[];
}

export interface VisibilityWatchCreate {
  text: string;
  country: string;
}

export interface SovEntry {
  track_id: string;
  name: string;
  icon_url: string;
  appearances: number;
  polls: number;
  sov_pct: number;
}

export interface SovOut {
  watch_id: number;
  text: string;
  country: string;
  polls: number;
  days: number;
  entries: SovEntry[];
}

export interface FullSovOut {
  items: SovOut[];
}

export type AnomalyKind = "surge" | "drop" | "new" | "gone";

export interface AnomalyOut {
  kind: AnomalyKind;
  track_id: string;
  name: string;
  icon_url: string;
  prev_median_position: number | null;
  latest_position: number | null;
  delta: number;
}

export interface WatchAnomaliesOut {
  watch_id: number;
  text: string;
  country: string;
  polls: number;
  anomalies: AnomalyOut[];
}

export interface AnomaliesOut {
  items: WatchAnomaliesOut[];
}

// ---- ASO Check ----

export type AsoIssueSeverity = "error" | "warning" | "info";

export interface AsoIssueOut {
  severity: AsoIssueSeverity;
  locale: string | null;
  field: string | null;
  code: string;
  message: string;
  suggestion: string | null;
}

export interface AsoIssueSummary {
  errors: number;
  warnings: number;
  infos: number;
  locales_audited: number;
}

export interface AsoCheckOut {
  summary: AsoIssueSummary;
  items: AsoIssueOut[];
}

// ---- App Clash ----

export interface ClashRow {
  track_id: string;
  is_self: boolean;
  name: string | null;
  subtitle: string | null;
  seller: string | null;
  primary_genre: string | null;
  average_rating: number | null;
  rating_count: number | null;
  release_date: string | null;
  version: string | null;
  file_size_mb: number | null;
  price: number | null;
  currency: string | null;
  formatted_price: string | null;
  icon_url: string | null;
  bundle_id: string | null;
  description_excerpt: string | null;
}

export interface AppClashOut {
  country: string;
  rows: ClashRow[];
}

export interface LocaleUpsertIn {
  name?: string | null;
  subtitle?: string | null;
  description?: string | null;
  keywords?: string | null;
  promotional_text?: string | null;
  whats_new?: string | null;
  marketing_url?: string | null;
  support_url?: string | null;
  privacy_policy_url?: string | null;
}

// ---- Clone-and-version-bump ----

export interface CloneScope {
  localizations: boolean;
  price_schedule: boolean;
  intro_offers: boolean;
  screenshot: boolean;
  auto_archive: boolean;
  group_availability: boolean;
}

export interface ClonePreviewResponse {
  suggested_product_id: string;
  source_product_id: string;
  locale_count: number;
  priced_territory_count: number;
  intro_offer_count: number;
  has_screenshot: boolean;
  revenuecat_connected: boolean;
  revenuecat_old_product_found: boolean;
  revenuecat_attached_entitlements: number;
  revenuecat_attached_packages: number;
}

export interface CloneRequest {
  new_product_id: string;
  new_name?: string | null;
  scope: CloneScope;
  swap_revenuecat: boolean;
}

export interface CloneStepStatus {
  name: string;
  status: "pending" | "running" | "done" | "skipped" | "failed" | "partial";
  detail?: string | null;
  completed?: number | null;
  total?: number | null;
}

export interface CloneOperationOut {
  id: number;
  app_id: number;
  source_kind: "subscription" | "iap";
  source_local_id: number;
  source_product_id: string;
  target_product_id: string;
  source_asc_id: string;
  target_asc_id: string | null;
  scope: CloneScope;
  asc_steps: CloneStepStatus[];
  revenuecat_steps: CloneStepStatus[];
  status: "pending" | "partial" | "done" | "failed";
  error_log: string[];
  created_at: string;
  completed_at: string | null;
}

// ---- RevenueCat ----

export interface RevenueCatCredentialResponse {
  id: number;
  name: string;
  project_id: string;
  rc_app_id: string | null;
  created_at: string;
}

export interface RevenueCatCredentialCreate {
  name: string;
  project_id: string;
  rc_app_id?: string | null;
  secret_key: string;
}

export interface RCConnectionTestResponse {
  success: boolean;
  message: string;
  apps_count?: number | null;
}

export interface RCProduct {
  id: string;
  store_identifier: string;
  type?: string | null;
  display_name?: string | null;
  app_id?: string | null;
  is_archived?: boolean | null;
}

export interface RCEntitlement {
  id: string;
  lookup_key: string;
  display_name?: string | null;
  is_archived?: boolean | null;
  products?: Array<{
    id?: string;
    store_identifier?: string;
    display_name?: string;
  }>;
}

export interface RCOffering {
  id: string;
  lookup_key: string;
  display_name?: string | null;
  is_current?: boolean | null;
  is_archived?: boolean | null;
  metadata?: Record<string, unknown> | null;
}

export interface RCPackage {
  id: string;
  lookup_key: string;
  display_name?: string | null;
  position?: number | null;
  products?: Array<{
    id?: string;
    store_identifier?: string;
    display_name?: string;
  }>;
}
