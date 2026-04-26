# 002 - App Store Connect API Integration

**Prerequisites**: [000 - Architecture](000-architecture.md)
**Related**: [001 - Pricing System](001-pricing-system.md), [004 - Localization Management](004-localization-management.md)

## Overview

ASO-Light communicates with Apple's App Store Connect API (v1 and v2) to read and write app metadata, subscription prices, IAP prices, localizations, and review screenshots. All API calls are made server-side — the private key never reaches the browser.

> **v1 vs v2**: Subscriptions use v1 endpoints. IAPs created via ASC v2 require v2 endpoints for listing localizations, price schedules, and price points. Create/update mutations still use v1 top-level resources.

## Authentication

### JWT Token Generation

Apple requires ES256-signed JWTs for ASC API authentication.

**File**: `backend/app/services/asc/client.py` — `ASCClient._generate_token()`

```
Header:  {"alg": "ES256", "kid": "<KEY_ID>", "typ": "JWT"}
Payload: {"iss": "<ISSUER_ID>", "iat": <now>, "exp": <now+1200>, "aud": "appstoreconnect-v1"}
Sign:    ES256 with the .p8 private key
```

Tokens expire after **20 minutes** max (Apple's limit). ASCClient proactively refreshes with a 60-second safety margin.

### Credential Setup

Users upload their `.p8` private key through the UI. The backend:
1. Validates it starts with `-----BEGIN PRIVATE KEY-----`
2. Encrypts with Fernet symmetric encryption
3. Stores encrypted bytes in `ASCCredential.private_key_encrypted` (TEXT column)

**File**: `backend/app/core/security.py` — `encrypt_value()` / `decrypt_value()`

The Fernet key is stored in `.env` as `FERNET_KEY`. Generate with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## ASCClient Base Class

**File**: `backend/app/services/asc/client.py`

| Method | Purpose |
|--------|---------|
| `_get(path, params)` | GET request (with throttle + retry) |
| `_post(path, json)` | POST request |
| `_patch(path, json)` | PATCH request |
| `_delete(path)` | DELETE request |
| `_put_binary(url, data, content_type)` | PUT binary to pre-signed URL (no auth headers) |
| `_get_all_pages(path, params)` | Paginate through all cursor pages |
| `_throttle()` | Global rate limiter (150ms min interval) |
| `from_credential(credential)` | Factory: decrypt key + create client |

### Rate Limiter

**File**: `backend/app/services/asc/client.py` — `_throttle()`

All requests pass through a global throttle:

```
_MIN_REQUEST_INTERVAL = 0.15  # 150ms → ~7 req/s max
_MAX_RETRIES = 6
_BACKOFF_BASE = 1.0           # exponential: 1s, 2s, 4s, 8s, 16s, 32s
```

- `_rate_lock` (asyncio.Lock) ensures only one request at a time checks/updates `_last_request_at`
- On 429: sets `_backoff_until` to `now + backoff_delay`, all concurrent requests wait
- Pagination uses the same throttle + retry loop

### Error Handling
- **401**: Token expired → regenerate and retry once
- **429**: Rate limited → exponential backoff (up to 6 retries, max ~32s delay)
- **4xx/5xx**: Raise `ASCAPIError` with parsed Apple error messages

**File**: `backend/app/services/asc/errors.py`

### Pagination

ASC uses cursor-based pagination:
```json
{
  "data": [...],
  "links": { "self": "...", "next": "...?cursor=abc" }
}
```
`_get_all_pages()` follows `links.next` until exhausted, accumulating all `data` items.

## Key API Endpoints Used

### Apps
```
GET /v1/apps?fields[apps]=name,bundleId&limit=200
GET /v1/apps/{app_id}
```
> Note: `platform` was removed from `fields[apps]` — Apple deprecated it.

### Subscription Pricing
```
GET /v1/apps/{app_id}/subscriptionGroups
GET /v1/subscriptionGroups/{group_id}/subscriptions
GET /v1/subscriptions/{sub_id}/prices?include=subscriptionPricePoint,territory
GET /v1/subscriptions/{sub_id}/pricePoints?include=territory&filter[territory]={code}
GET /v1/subscriptionPricePoints/{id}/equalizations
POST /v1/subscriptionPrices  ← Create/update price
```

### In-App Purchase Pricing (v2 API)
```
GET  /v1/apps/{app_id}/inAppPurchasesV2
GET  /v2/inAppPurchases/{iap_id}/iapPriceSchedule?include=manualPrices
GET  /v2/inAppPurchases/{iap_id}/pricePoints?filter[territory]={code}&include=territory
POST /v1/inAppPurchasePriceSchedules  ← Batch price schedule (all territories at once)
```
> **Note**: `GET /v1/inAppPurchases/{id}/iapPriceSchedule` does NOT work for v2-created IAPs. Must use `/v2/inAppPurchases/{id}/iapPriceSchedule`. Price point IDs in the schedule response are base64-encoded and require decoding to extract territory and price tier.

### Review Screenshots (3-step upload)
```
POST  /v1/reviewSubmissionItems                        ← Reserve upload slot
PUT   {upload_operations[0].url}                       ← Binary upload to pre-signed S3 URL (NO auth header)
PATCH /v1/reviewSubmissionItems/{id}                   ← Commit upload
GET   /v1/subscriptions/{id}/appStoreReviewScreenshot  ← Subscription screenshot
GET   /v1/inAppPurchases/{id}/appStoreReviewScreenshot ← IAP screenshot
```
> **Important**: The PUT to Apple's S3 pre-signed URL must NOT include the ASC Bearer token — it will be rejected. `ASCClient._put_binary()` uses a separate `httpx.AsyncClient` without auth headers.

### Subscription Localizations
```
GET  /v1/subscriptions/{sub_id}/subscriptionLocalizations
POST /v1/subscriptionLocalizations
PATCH /v1/subscriptionLocalizations/{id}
```

### IAP Localizations (v2 API required for listing)
```
GET  /v2/inAppPurchases/{id}?include=inAppPurchaseLocalizations  ← v2 only!
POST /v1/inAppPurchaseLocalizations
PATCH /v1/inAppPurchaseLocalizations/{id}
```
> **Note**: IAP localizations don't exist as a nested relationship on `/v1/inAppPurchases`. Must use `/v2/inAppPurchases/{id}?include=...` to list them. See [004 - Localization Management](004-localization-management.md).

## JSON:API Response Format

All ASC responses follow JSON:API spec:
```json
{
  "data": {
    "type": "apps",
    "id": "12345",
    "attributes": { "name": "My App", "bundleId": "com.example.app" },
    "relationships": { "subscriptionGroups": { "data": [...] } }
  },
  "included": [...]
}
```

The pricing service resolves `included` resources (price points, territories) within the service layer before returning flat dicts to the API router.

**File**: `backend/app/services/asc/pricing.py`

## Territory Code Mapping

ASC API uses ISO 3166-1 **alpha-3** codes (e.g., `USA`, `GBR`, `ARE`), but our DB uses **alpha-2** codes (e.g., `US`, `GB`, `AE`). The canonical mapping is in `backend/app/data/territories.py` — `ALPHA2_TO_ALPHA3` dict (shared across all services). The `_get_territory_map()` helper in `pricing.py` indexes territories by both formats.

## Cache-First Pricing Architecture

Pricing endpoints **never** call ASC API during normal page loads:

| Endpoint | Behavior |
|----------|----------|
| `GET .../prices` | Read from `subscription_prices` DB table |
| `POST .../prices/preview` | Calculate from exchange rates + read cached price points |
| `POST .../sync` | **Explicit** — fetches ~175 current prices from ASC (~2.5s) |

The `pricePoints` endpoint (all available Apple price tiers) is **not** fetched in bulk — it returns ~140k records across 700 pages and takes 20+ minutes. Price point lookup is done per-territory on demand during apply.

## Rate Limits

Apple's ASC API rate limits are not publicly documented but are real. The `ASCClient` implements:

- **Global throttle**: 150ms minimum interval between any two requests (~7 req/s)
- **Exponential backoff**: On 429 → wait 1s, 2s, 4s, 8s, 16s, 32s (max 6 retries)
- **Global backoff**: When any request gets 429, ALL concurrent requests pause until backoff expires
- **asyncio.Lock**: Ensures atomic check-and-update of timing state across concurrent coroutines

Typical operation speeds:
- Price sync (~175 territories): ~2.5s
- Price point cache sync (~175 territories, concurrency=2): ~30s
- Apply prices (~175 territories): ~30s

## Security Notes

- The `.p8` private key gives **full ASC access** — treat it like a password
- Never log the decrypted key or the JWT payload
- Keys are decrypted in memory only for the duration of an API call, then discarded
- `FERNET_KEY` in `.env` must be kept secret; rotate it if compromised (requires re-encrypting all stored keys)
