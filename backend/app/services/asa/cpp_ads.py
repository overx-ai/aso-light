"""Apple Search Ads Campaign Management API helpers — Custom Product Page ads.

Wires an App Store Connect Custom Product Page (CPP) to an Apple Search Ads
ad group by creating an **Ad** inside the ad group whose creative references
the CPP id. Once an Ad referencing a CPP is enabled, the ad group serves that
tailored page instead of the default product page.

Resource hierarchy (ASA v5 Campaign Management API):

    /campaigns/{campaignId}/adgroups/{adGroupId}/ads

Each function takes an :class:`ASAClient` and returns the parsed ``data`` from
Apple. No DB access here; the MCP tool layer owns auth + persistence.

.. note::
    The exact request-body field that references a Custom Product Page on an
    ASA ``Ad`` is **not fully confirmed from public docs**. An ASA ``Ad``
    references a *creative*; for a Custom Product Page ad that creative is the
    ASC ``appCustomProductPage`` id. We send it as ``creativeId`` (the field
    ASA's Ad schema exposes for the creative reference) and also mirror it as
    ``productPageId`` defensively. See the ``# TODO: confirm against ASA Ad
    schema`` markers below — adjust the field name once verified against a live
    ``GET .../ads`` response or the current Apple Ads API reference.
"""
from __future__ import annotations

from typing import Any

from app.services.asa.client import ASAClient


def _ads_path(campaign_id: int, adgroup_id: int) -> str:
    """Build the ASA ads collection path for an ad group."""
    return f"/campaigns/{campaign_id}/adgroups/{adgroup_id}/ads"


async def list_ads(
    client: ASAClient, *, org_id: int, campaign_id: int, adgroup_id: int,
) -> list[dict[str, Any]]:
    """List the Ads in an ad group.

    ``POST /campaigns/{campaignId}/adgroups/{adGroupId}/ads/find`` — uses the
    paginated ``find`` selector like the other ASA listings (campaigns /
    ad groups). Each returned Ad carries the creative reference identifying
    which Custom Product Page (if any) it serves.
    """
    return await client.get_all_pages(
        "POST", f"{_ads_path(campaign_id, adgroup_id)}/find", org_id=org_id,
    )


async def assign_cpp(
    client: ASAClient,
    *,
    org_id: int,
    campaign_id: int,
    adgroup_id: int,
    cpp_id: str,
    name: str,
) -> dict[str, Any]:
    """Create an Ad in the ad group that serves a Custom Product Page.

    ``POST /campaigns/{campaignId}/adgroups/{adGroupId}/ads``

    Args:
        client: An authenticated :class:`ASAClient`.
        org_id: The ASA org id (sent via the ``X-AP-Context`` header).
        campaign_id: The ASA (Apple-side) campaign id.
        adgroup_id: The ASA (Apple-side) ad group id.
        cpp_id: The ASC ``appCustomProductPage`` id to serve.
        name: A human-readable name for the Ad.

    Returns:
        The created ``Ad`` resource dict from Apple.

    .. note::
        # TODO: confirm against ASA Ad schema — the field that references a
        CPP on an Ad's creative is uncertain. We send ``creativeId`` (the
        documented creative reference) set to the CPP id, and also include
        ``productPageId`` as a defensive alias. Trim to the verified field
        once a live Ad response confirms the shape.
    """
    body: dict[str, Any] = {
        "name": name,
        "status": "ENABLED",
        # TODO: confirm against ASA Ad schema — CPP reference field name.
        "creativeId": cpp_id,
        "productPageId": cpp_id,
    }
    payload = await client.request(
        "POST",
        _ads_path(campaign_id, adgroup_id),
        org_id=org_id,
        json=body,
    )
    return payload.get("data") or {}


async def unassign_cpp(
    client: ASAClient,
    *,
    org_id: int,
    campaign_id: int,
    adgroup_id: int,
    ad_id: int,
) -> None:
    """Remove a Custom Product Page Ad from the ad group.

    ``DELETE /campaigns/{campaignId}/adgroups/{adGroupId}/ads/{adId}``

    Deleting the Ad stops the ad group from serving the CPP; the ad group
    falls back to the default product page (or another remaining Ad). Apple
    returns ``204 No Content`` on success.
    """
    await client.request(
        "DELETE",
        f"{_ads_path(campaign_id, adgroup_id)}/{ad_id}",
        org_id=org_id,
    )
