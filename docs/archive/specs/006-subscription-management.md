---
id: 006
title: "Subscription Management (groups, subs, localizations, intro offers)"
status: done
created: 2026-04-30
tasks: []
---

# 006 - Subscription Management

## Problem
Today the app can only **read** subscriptions and **manage prices/localizations** of subs that already exist in App Store Connect. Onboarding a new sub product still requires opening ASC manually. We want to drive the full lifecycle — group, subscription, group localizations, introductory offers — from our UI. Submission for review remains a manual step in ASC.

## Scope
In:
- Subscription **group** create + update (incl. `subscriptionGroupLocalizations` CRUD)
- **Subscription** create + update (`POST /v1/subscriptions`, `PATCH /v1/subscriptions/{id}`)
- **Introductory offers** list + create + delete (no update — Apple model is delete + recreate)
In, already implemented (not changing):
- Subscription localizations CRUD
- Price points / prices preview + apply
Out:
- Promotional offers, offer codes
- Subscription state transitions (PREPARE_FOR_SUBMISSION → READY_FOR_REVIEW)
- Submit for review — done manually in ASC by the user

## Architecture

### Backend — ASC service additions
File: `backend/app/services/asc/pricing.py`. Add to `ASCPricingService`:

```python
# Groups
create_subscription_group(app_id, reference_name) -> dict        # POST /v1/subscriptionGroups
update_subscription_group(group_id, reference_name) -> dict      # PATCH /v1/subscriptionGroups/{id}

# Group localizations
list_subscription_group_localizations(group_id) -> list[dict]    # GET .../subscriptionGroupLocalizations
create_subscription_group_localization(group_id, locale, name, custom_app_name=None) -> dict  # POST
update_subscription_group_localization(loc_id, name, custom_app_name=None) -> dict            # PATCH

# Subscriptions
create_subscription(group_id, product_id, name, period,
                    family_sharable, available_in_all_territories,
                    group_level, review_note=None) -> dict       # POST /v1/subscriptions
update_subscription(subscription_id, name=None, group_level=None,
                    family_sharable=None, review_note=None) -> dict  # PATCH /v1/subscriptions/{id}

# Introductory offers
list_subscription_introductory_offers(subscription_id) -> list[dict]   # GET
create_subscription_introductory_offer(subscription_id, territory_code,
                                       offer_mode, duration, num_periods,
                                       price_point_id=None,
                                       start_date=None, end_date=None) -> dict
delete_subscription_introductory_offer(offer_id) -> None
```

Apple constants:
- `subscriptionPeriod` ∈ {ONE_WEEK, ONE_MONTH, TWO_MONTHS, THREE_MONTHS, SIX_MONTHS, ONE_YEAR}
- intro `offerMode` ∈ {FREE_TRIAL, PAY_AS_YOU_GO, PAY_UP_FRONT}
- intro `duration` uses the same period enum
- intro `numberOfPeriods` ≥ 1; FREE_TRIAL and PAY_UP_FRONT don't carry price; PAY_AS_YOU_GO requires a `subscriptionPricePoint` relationship

### Schemas — `backend/app/schemas/pricing.py`
Add:

```python
SubscriptionPeriod = Literal["ONE_WEEK","ONE_MONTH","TWO_MONTHS","THREE_MONTHS","SIX_MONTHS","ONE_YEAR"]
IntroOfferMode = Literal["FREE_TRIAL","PAY_AS_YOU_GO","PAY_UP_FRONT"]

class SubscriptionGroupCreate(BaseModel):
    reference_name: str

class SubscriptionGroupUpdate(BaseModel):
    reference_name: str

class GroupLocalizationCreate(BaseModel):
    locale: str
    name: str
    custom_app_name: str | None = None

class GroupLocalizationUpdate(BaseModel):
    name: str
    custom_app_name: str | None = None

class GroupLocalizationResponse(BaseModel):
    id: str
    locale: str
    name: str
    custom_app_name: str | None = None
    state: str | None = None  # ASC reports localization state

class SubscriptionCreate(BaseModel):
    product_id: str               # reverse-DNS, must be unique across the app
    name: str                     # internal reference name
    period: SubscriptionPeriod
    family_sharable: bool = False
    available_in_all_territories: bool = True
    group_level: int = 1
    review_note: str | None = None

class SubscriptionUpdate(BaseModel):
    name: str | None = None
    group_level: int | None = None
    family_sharable: bool | None = None
    review_note: str | None = None

class IntroOfferCreate(BaseModel):
    territory_code: str | None = None        # None == worldwide (Apple alpha-3 internally)
    offer_mode: IntroOfferMode
    duration: SubscriptionPeriod
    number_of_periods: int = Field(ge=1, le=12)
    price_point_id: str | None = None        # required for PAY_AS_YOU_GO
    start_date: date | None = None
    end_date: date | None = None

class IntroOfferResponse(BaseModel):
    id: str
    territory_code: str | None
    offer_mode: IntroOfferMode
    duration: SubscriptionPeriod
    number_of_periods: int
    price_point_id: str | None
    start_date: date | None
    end_date: date | None
```

### Routes — `backend/app/api/v1/pricing.py`
Add:

| Method | Path | Purpose |
|---|---|---|
| POST | `/{app_id}/subscription-groups` | Create group + sync to DB |
| PATCH | `/{app_id}/subscription-groups/{group_id}` | Rename group |
| GET | `/{app_id}/subscription-groups/{group_id}/localizations` | List group localizations |
| POST | `/{app_id}/subscription-groups/{group_id}/localizations` | Create group localization |
| PATCH | `/{app_id}/subscription-groups/{group_id}/localizations/{loc_id}` | Update group localization |
| POST | `/{app_id}/subscription-groups/{group_id}/subscriptions` | Create subscription + sync to DB |
| PATCH | `/{app_id}/subscriptions/{subscription_id}` | Update subscription metadata |
| GET | `/{app_id}/subscriptions/{subscription_id}/intro-offers` | List intro offers |
| POST | `/{app_id}/subscriptions/{subscription_id}/intro-offers` | Create intro offer |
| DELETE | `/{app_id}/subscriptions/{subscription_id}/intro-offers/{offer_id}` | Delete intro offer |

Ownership: every route does the standard `_get_verified_app` guard (`app.credential_id → credential.user_id == current_user_id`). Group routes additionally verify the group belongs to the app; subscription routes use the existing `_get_verified_subscription`.

### Model
Subscription model already stores `name`, `product_id`, `asc_subscription_id`. Reuse on create/update — re-use the same upsert pattern from `list_subscriptions` route.

No new tables. Group localizations and intro offers round-trip through ASC; we don't cache them locally for v1.

### Frontend
`frontend/src/lib/hooks.ts` — add hooks:
- `useCreateSubscriptionGroup`, `useUpdateSubscriptionGroup`
- `useGroupLocalizations`, `useCreateGroupLocalization`, `useUpdateGroupLocalization`
- `useCreateSubscription`, `useUpdateSubscription`
- `useIntroOffers`, `useCreateIntroOffer`, `useDeleteIntroOffer`

`frontend/src/types/index.ts` — corresponding types.

`frontend/src/pages/PricingPage.tsx` — add buttons:
- "New group" → modal for `referenceName`
- per-group "Edit" + "Localizations" + "New subscription"
- per-sub "Edit metadata" + "Intro offers"

New components:
- `components/pricing/SubscriptionFormModal.tsx` — create/edit sub
- `components/pricing/GroupLocalizationsModal.tsx` — list/CRUD group localizations
- `components/pricing/IntroOffersModal.tsx` — list/create/delete intro offers per territory

### Tests
- `backend/tests/test_subscription_management.py` — happy-path mocks for ASC client `_post` / `_patch` / `_delete`, asserting JSON:API request shape (including the `subscriptionGroup` / `subscription` relationship blocks Apple requires).
- One smoke test that route → service flows correctly with a stubbed `ASCPricingService`.

## Verification
1. `cd backend && uv run pytest tests/test_subscription_management.py -q`
2. `make dev` → http://localhost:8000/docs — confirm new endpoints render in Swagger.
3. Manual end-to-end (with real ASC creds):
   - Create a group with a unique `referenceName`.
   - Add an English group localization (name, optional custom-app-name).
   - Create a subscription inside the group with `productId=com.test.aso.monthly1`, monthly period, group level 1.
   - Update the subscription's name; confirm via `GET /apps/{id}/subscriptions`.
   - Create a `FREE_TRIAL` intro offer for `US`, 1 month, 1 period.
   - Delete the intro offer.
4. Verify in App Store Connect web UI that all changes are reflected.

## Notes
- Apple uses **alpha-3** territory codes in ASC API; our DB uses **alpha-2**. Use `ALPHA2_TO_ALPHA3` from `app/data/territories.py` for intro offer territory inputs.
- Apple returns 409 if `productId` already exists — surface as a clean 409 with the validator detail.
- For PAY_AS_YOU_GO intro offers, `price_point_id` must be a real `subscriptionPricePoint` for the same subscription + territory; we already have a resolver (`PriceResolveRequest`) the UI can call before submitting.
