"""Schemas for the swap.subscription_product / swap.iap tools.

A "swap" is the existing clone+auto-archive flow with a tailored response that
explicitly tells the operator what their iOS app must change. We extend
:class:`CloneOperationOut` rather than introduce a parallel type so all swap
results are inspectable through the existing CloneOperation endpoints.
"""

from __future__ import annotations

from app.schemas.clone import CloneOperationOut


class SwapResponse(CloneOperationOut):
    """Result of a productId swap, with iOS-side guidance attached."""

    ios_checklist: list[str]
    ios_doc_url: str
    transition_window_note: str
