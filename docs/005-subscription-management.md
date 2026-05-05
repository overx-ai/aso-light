# 005 - Subscription Management

**Prerequisites**: [002 - ASC Integration](002-asc-integration.md), [004 - Localization Management](004-localization-management.md)
**Related**: [001 - Pricing System](001-pricing-system.md)
**Spec**: [006 - Subscription Management](specs/006-subscription-management.md)

## Overview

Drive the full auto-renewable-subscription lifecycle from our UI: create / rename subscription **groups**, create / update **subscriptions**, manage **group localizations**, and create / delete **introductory offers**. State transitions and submit-for-review are out of scope — the user flips state in App Store Connect manually.

This complements doc [004 - Localization Management](004-localization-management.md), which covers per-subscription display-name localizations.

## Out of Scope

- Promotional offers, offer codes
- Subscription state transitions (`PREPARE_FOR_SUBMISSION` → `READY_FOR_REVIEW`)
- Submit for review

## Apple's Constraints

| Field | Mutability | Notes |
|-------|-----------|-------|
| `productId` | **immutable** after create | Reverse-DNS, must be unique across the app |
| `subscriptionPeriod` | **immutable** after create | One of: ONE_WEEK, ONE_MONTH, TWO_MONTHS, THREE_MONTHS, SIX_MONTHS, ONE_YEAR |
| `name`, `groupLevel`, `familySharable`, `reviewNote` | mutable | PATCH `/v1/subscriptions/{id}` |
| `subscriptionGroup.referenceName` | mutable | PATCH `/v1/subscriptionGroups/{id}` |
| Group localization `locale` | **immutable** | Replace by deleting + recreating |

Introductory offer durations include extra short values not allowed for the base subscription period: `THREE_DAYS`, `TWO_WEEKS` (in addition to the six period values).

Intro-offer constraints (enforced both server-side via `IntroOfferCreate` validators and client-side in the modal):

| Mode | Requires `price_point_id` | `number_of_periods` |
|------|---------------------------|---------------------|
| `FREE_TRIAL` | no (rejected if present) | must be 1 |
| `PAY_AS_YOU_GO` | yes | 1–12 |
| `PAY_UP_FRONT` | yes | must be 1 |

## Backend

### Service Methods

**File**: `backend/app/services/asc/pricing.py` — `ASCPricingService`

| Method | ASC Endpoint |
|--------|--------------|
| `create_subscription_group(app_id, reference_name)` | `POST /v1/subscriptionGroups` |
| `update_subscription_group(group_id, reference_name)` | `PATCH /v1/subscriptionGroups/{id}` |
| `list_subscription_group_localizations(group_id)` | `GET /v1/subscriptionGroups/{id}/subscriptionGroupLocalizations` |
| `create_subscription_group_localization(group_id, locale, name, custom_app_name=None)` | `POST /v1/subscriptionGroupLocalizations` |
| `update_subscription_group_localization(localization_id, name, custom_app_name=None)` | `PATCH /v1/subscriptionGroupLocalizations/{id}` |
| `create_subscription(group_id, product_id, name, period, family_sharable, available_in_all_territories, group_level, review_note=None)` | `POST /v1/subscriptions` |
| `update_subscription(subscription_id, name=…, group_level=…, family_sharable=…, review_note=…)` | `PATCH /v1/subscriptions/{id}` |
| `list_subscription_introductory_offers(subscription_id)` | `GET /v1/subscriptions/{id}/introductoryOffers?include=territory,subscriptionPricePoint` |
| `create_subscription_introductory_offer(subscription_id, offer_mode, duration, number_of_periods, territory_id=None, price_point_id=None, start_date=None, end_date=None)` | `POST /v1/subscriptionIntroductoryOffers` |
| `delete_subscription_introductory_offer(offer_id)` | `DELETE /v1/subscriptionIntroductoryOffers/{id}` |

`update_subscription(...)` raises `ValueError` if no editable fields are provided. `productId` and `subscriptionPeriod` are intentionally absent from the signature.

### API Endpoints

**File**: `backend/app/api/v1/pricing.py`

| Method | Path | Body |
|--------|------|------|
| POST | `/{app_id}/subscription-groups` | `SubscriptionGroupCreate` |
| PATCH | `/{app_id}/subscription-groups/{group_id}` | `SubscriptionGroupUpdate` |
| GET | `/{app_id}/subscription-groups/{group_id}/localizations` | — |
| POST | `/{app_id}/subscription-groups/{group_id}/localizations` | `GroupLocalizationCreate` |
| PATCH | `/{app_id}/subscription-groups/{group_id}/localizations/{localization_id}` | `GroupLocalizationUpdate` |
| POST | `/{app_id}/subscription-groups/{group_id}/subscriptions` | `SubscriptionCreate` |
| PATCH | `/{app_id}/subscriptions/{subscription_id}` | `SubscriptionUpdate` |
| GET | `/{app_id}/subscriptions/{subscription_id}/intro-offers` | — |
| POST | `/{app_id}/subscriptions/{subscription_id}/intro-offers` | `IntroOfferCreate` |
| DELETE | `/{app_id}/subscriptions/{subscription_id}/intro-offers/{offer_id}` | — |

Ownership: every route calls `_get_verified_app` (asserts `app.credential_id → credential.user_id`) and either `_get_verified_subscription` or the new `_get_verified_subscription_group` helper before any ASC call.

### DB Sync

`POST /subscription-groups` and `POST /subscription-groups/{id}/subscriptions` mirror the new resource into local tables (`subscription_groups`, `subscriptions`) immediately after the ASC call succeeds. Group localizations and introductory offers round-trip through ASC and are **not** cached locally — they're re-fetched on every modal open.

### Schemas

**File**: `backend/app/schemas/pricing.py`

| Schema | Notes |
|--------|-------|
| `SubscriptionPeriod` | `Literal["ONE_WEEK","ONE_MONTH","TWO_MONTHS","THREE_MONTHS","SIX_MONTHS","ONE_YEAR"]` |
| `IntroOfferDuration` | Adds `THREE_DAYS`, `TWO_WEEKS` to the period set |
| `IntroOfferMode` | `Literal["FREE_TRIAL","PAY_AS_YOU_GO","PAY_UP_FRONT"]` |
| `SubscriptionGroupCreate / Update` | `reference_name` (1–64 chars) |
| `GroupLocalizationCreate / Update / Response` | locale, name (≤30), `custom_app_name` (optional) |
| `SubscriptionCreate` | `product_id`, `name`, `period`, `family_sharable`, `available_in_all_territories`, `group_level` (1–10), `review_note` |
| `SubscriptionUpdate` | All fields optional, model validator rejects empty bodies |
| `IntroOfferCreate` | Alpha-2 `territory_code` normalised; mode/price/period validators (see table above) |
| `IntroOfferResponse` | Alpha-2 `territory_code` (or `None` for worldwide / unknown) |

### Territory Codes

The ASC API uses alpha-3 (`USA`, `GBR`); our DB and schemas use alpha-2 (`US`, `GB`). Conversion uses `ALPHA2_TO_ALPHA3` from `backend/app/data/territories.py` and the inverse `ALPHA3_TO_ALPHA2` defined in `backend/app/api/v1/pricing.py`. `_parse_intro_offer` returns `None` for the territory when the alpha-3 code can't be mapped.

## Frontend

### Pricing Page Wiring

**File**: `frontend/src/pages/PricingPage.tsx` — `SubscriptionsTab`

Action-icon row to the right of the group / subscription selectors:

| Icon | Visibility | Opens |
|------|------------|-------|
| ＋ | always | `SubscriptionGroupFormModal` (create) |
| ✎ | a group is selected | `SubscriptionGroupFormModal` (edit) |
| 🌐 | a group is selected | `GroupLocalizationsModal` |
| ＋ (grape) | a group is selected | `SubscriptionFormModal` (create) |
| ✎ | a subscription is selected | `SubscriptionFormModal` (edit) |
| 🎁 (teal) | a subscription is selected | `IntroOffersModal` |

### Modal Components

**Files** (all under `frontend/src/components/pricing/`):

| Component | Purpose |
|-----------|---------|
| `SubscriptionGroupFormModal.tsx` | Create or rename a group (single field). |
| `GroupLocalizationsModal.tsx` | List + add + inline-edit group localizations (locale + name + optional custom-app-name). Resets draft state on open / group change. |
| `SubscriptionFormModal.tsx` | Create / edit a subscription. Period field is hidden in edit mode (Apple-immutable). |
| `IntroOffersModal.tsx` | Table of existing offers with delete; form to add a new offer. Periods locked to 1 for `FREE_TRIAL` / `PAY_UP_FRONT`; `price_point_id` field shown only for paid modes. |

### Hooks

**File**: `frontend/src/lib/hooks.ts`

| Hook | Endpoint |
|------|----------|
| `useCreateSubscriptionGroup(appId)` | POST group |
| `useUpdateSubscriptionGroup(appId)` | PATCH group |
| `useGroupLocalizations(appId, groupId)` | GET group localizations |
| `useCreateGroupLocalization(appId, groupId)` | POST group localization |
| `useUpdateGroupLocalization(appId, groupId)` | PATCH group localization |
| `useCreateSubscription(appId, groupId)` | POST subscription |
| `useUpdateSubscription(appId)` | PATCH subscription |
| `useIntroOffers(appId, subId)` | GET intro offers |
| `useCreateIntroOffer(appId, subId)` | POST intro offer |
| `useDeleteIntroOffer(appId, subId)` | DELETE intro offer |

All mutations show success / error toasts via the shared `notifications` helper. The local `ascErrorMessage(error, fallback)` helper extracts the backend `detail` from axios errors so toasts surface ASC validation errors verbatim.

### Types

**File**: `frontend/src/types/index.ts`

`SubscriptionPeriod`, `IntroOfferDuration`, `IntroOfferMode`, `SubscriptionGroupCreate / Update`, `GroupLocalization`, `GroupLocalizationCreate / Update`, `SubscriptionCreate`, `SubscriptionUpdate`, `IntroOffer`, `IntroOfferCreate`.

## Tests

**File**: `backend/tests/test_subscription_management.py`

17 tests covering:
- JSON:API body shape for every new ASC service method (`create_subscription_group`, `update_subscription_group`, group localizations, `create_subscription`, `update_subscription`, intro offer create + delete)
- `update_subscription` rejects empty payload
- Intro-offer validators reject FREE_TRIAL with price, PAY_AS_YOU_GO without price, PAY_UP_FRONT with `number_of_periods != 1`
- Alpha-2 normalisation (`"us"` → `"US"`)
- Worldwide intro offers omit the territory relationship

## Known Eventual-Consistency Window

If the ASC create call succeeds but the local DB insert fails (e.g., transient connection loss), ASC has the resource and we don't. A subsequent group / subscription sync (`GET /apps/{id}/subscriptions`) rediscovers it and upserts the row, so this self-heals on the next refresh. No 2-phase rollback is implemented.
