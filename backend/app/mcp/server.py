"""FastMCP server instance + ASGI sub-app for the ASO-Light backend."""

from __future__ import annotations

from fastmcp import FastMCP

from app.mcp.auth import PATTokenVerifier

mcp: FastMCP = FastMCP(
    name="aso-light",
    instructions=(
        "Tools to manage an iOS app's App Store Connect presence: pricing, "
        "subscriptions/IAPs, metadata, keywords, reviews, visibility, ASO "
        "audit, and competitor analysis. The 'swap.subscription_product' / "
        "'swap.iap' tools handle productId swaps end-to-end (ASC + "
        "RevenueCat) and return the iOS-side checklist explaining what the "
        "app code must change."
    ),
    auth=PATTokenVerifier(),
)


# Importing the tool modules registers tools, resources, and prompts on `mcp`.
from app.mcp.tools import (  # noqa: E402, F401
    account,
    apps,
    asa,
    aso,
    availability,
    clash,
    indices,
    keywords,
    metadata,
    pricing,
    presets,
    reviews,
    revenuecat,
    swap,
    territories,
    visibility,
)
from app.mcp import resources, prompts  # noqa: E402, F401


# Mount path is "/mcp" in main.py, so the FastMCP app lives at "/" within itself.
mcp_app = mcp.http_app(path="/")
