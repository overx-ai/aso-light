# 004 - Localization Management

**Prerequisites**: [002 - ASC Integration](002-asc-integration.md)
**Related**: [001 - Pricing System](001-pricing-system.md)

## Overview

Manage subscription and IAP display names and descriptions across multiple locales via the App Store Connect API. Localizations are pushed directly to ASC — no local DB storage.

## Apple's Limits

| Field | Max Length |
|-------|-----------|
| Name | 30 characters |
| Description | 55 characters |

The frontend validates these limits before allowing save.

## ASC API Endpoints

### Subscription Localizations (v1)

```
GET  /v1/subscriptions/{id}/subscriptionLocalizations
POST /v1/subscriptionLocalizations
PATCH /v1/subscriptionLocalizations/{id}
```

Standard nested relationship — works on the v1 resource.

### IAP Localizations (v2 only)

```
GET /v2/inAppPurchases/{id}?include=inAppPurchaseLocalizations
POST /v1/inAppPurchaseLocalizations
PATCH /v1/inAppPurchaseLocalizations/{id}
```

**Important**: IAP localizations can only be **listed** via the v2 API (`/v2/inAppPurchases/{id}` with `include=`). The nested path `/v1/inAppPurchases/{id}/inAppPurchaseLocalizations` does **not** exist. Create and update still use v1 top-level resources.

**File**: `backend/app/services/asc/pricing.py` — `ASCPricingService`

## Backend Architecture

### Service Methods

**File**: `backend/app/services/asc/pricing.py`

| Method | Purpose |
|--------|---------|
| `list_subscription_localizations(sub_id)` | GET v1 nested relationship |
| `create_subscription_localization(sub_id, locale, name, desc)` | POST v1 |
| `update_subscription_localization(loc_id, name, desc)` | PATCH v1 |
| `list_iap_localizations(iap_id)` | GET v2 with include (see above) |
| `create_iap_localization(iap_id, locale, name, desc)` | POST v1 |
| `update_iap_localization(loc_id, name, desc)` | PATCH v1 |

### API Endpoints

**File**: `backend/app/api/v1/pricing.py`

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/{app_id}/subscriptions/{sub_id}/localizations` | List |
| POST | `/{app_id}/subscriptions/{sub_id}/localizations` | Create single |
| PUT | `/{app_id}/subscriptions/{sub_id}/localizations/{id}` | Update single |
| POST | `/{app_id}/subscriptions/{sub_id}/localizations/bulk` | Bulk sync |
| GET | `/{app_id}/iaps/{iap_id}/localizations` | List |
| POST | `/{app_id}/iaps/{iap_id}/localizations` | Create single |
| PUT | `/{app_id}/iaps/{iap_id}/localizations/{id}` | Update single |
| POST | `/{app_id}/iaps/{iap_id}/localizations/bulk` | Bulk sync |

### Bulk Sync Logic

**File**: `backend/app/api/v1/pricing.py` — `_bulk_sync_localizations()`

The bulk endpoint compares requested locales against existing ones:
- If locale already exists → PATCH (update name/description)
- If locale is new → POST (create)

Returns `{ created: int, updated: int, localizations: [...] }`.

### Schemas

**File**: `backend/app/schemas/pricing.py`

| Schema | Fields |
|--------|--------|
| `LocalizationCreate` | locale, name, description |
| `LocalizationUpdate` | name, description |
| `LocalizationResponse` | id, locale, name, description |
| `BulkLocalizationRequest` | localizations: list[LocalizationCreate] |
| `BulkLocalizationResponse` | created, updated, localizations |

## Frontend

### Localizations Tab

**File**: `frontend/src/pages/PricingPage.tsx` — `LocalizationsTab`

Third tab in the Pricing page. Uses a `SegmentedControl` to toggle between subscriptions and IAPs. Same dropdown pattern for selecting subscription group → subscription or IAP.

### LocalizationEditor Component

**File**: `frontend/src/components/pricing/LocalizationEditor.tsx`

Reusable editor for both subscriptions and IAPs:
- Table with locale badge, name input (max 30), description textarea (max 55)
- Validation errors shown inline when limits exceeded
- "Save All" disabled when validation fails
- "Add locale" searchable dropdown (38 common App Store locales)
- Remove button per row
- **JSON import**: paste array or object format to bulk-fill the table

### JSON Import Formats

Array format:
```json
[
  {"locale": "en-US", "name": "Monthly Premium", "description": "All features, billed monthly."}
]
```

Object format:
```json
{
  "en-US": {"name": "Monthly Premium", "description": "All features, billed monthly."}
}
```

### Hooks

**File**: `frontend/src/lib/hooks.ts`

| Hook | Purpose |
|------|---------|
| `useSubscriptionLocalizations(appId, subId)` | GET list |
| `useSaveSubscriptionLocalizations()` | POST bulk sync |
| `useIAPLocalizations(appId, iapId)` | GET list |
| `useSaveIAPLocalizations()` | POST bulk sync |

### Types

**File**: `frontend/src/types/index.ts`

`Localization`, `LocalizationCreate`, `BulkLocalizationResponse`
