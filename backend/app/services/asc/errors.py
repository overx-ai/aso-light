"""Custom exceptions for App Store Connect API interactions."""


class CredentialDecryptError(Exception):
    """The stored .p8 cannot be decrypted or is not a valid PEM private key.

    Distinct from a transport / API error: indicates the data at rest is
    corrupt or was encrypted with a different key. Callers should map this
    to a 400-class response telling the user to re-upload their .p8.
    """


class ASCRequestInvalidError(Exception):
    """The request is malformed before it ever reaches Apple.

    A distinct type rather than a bare ``ValueError`` on purpose: the service
    methods that raise it also parse JSON, and ``json.JSONDecodeError`` *is* a
    ``ValueError``. A caller catching ``ValueError`` around them would turn a
    malformed upstream body into a confident 400 whose detail is a raw Python
    message — which CLAUDE.md forbids.

    REST maps this to 400; MCP maps it to ``ToolError``.
    """


class IAPScheduleUnsyncedError(Exception):
    """Applying IAP prices would reset territories we have never read.

    Apple replaces the ENTIRE ``iapPriceSchedule`` on every apply, so the
    caller re-submits untouched territories from the local ``IAPPrice`` cache.
    An empty cache while Apple holds manual prices means that padding is
    impossible and the apply would silently reset live prices.

    REST maps this to 409; MCP maps it to ``ToolError``.
    """


class ChildResourceNotFoundError(Exception):
    """A child resource id does not belong to its verified parent.

    Raised by the membership-assertion helpers in
    :mod:`app.services.asc.pricing` when a caller passes a
    localization / intro-offer id that is not a child of the parent
    (subscription / IAP / group) they authorized against, and by
    :mod:`app.services.reviews.ownership` when a caller passes a
    review_id / response_id that a DB-backed map has not recorded as
    belonging to the app they authorized against (ASC exposes no reverse
    "which app owns this review" lookup, so reviews can't use the same
    live re-list-and-check approach the pricing helpers use). Either way
    this is the IDOR guard: the parent is owned by the caller, but the
    child must also be proven to belong to that parent before any
    read/mutate/delete.

    REST maps this to 404; MCP maps it to ``ToolError``.
    """


class ASCAPIError(Exception):
    """Error returned from the App Store Connect API."""

    def __init__(self, status_code: int, response_body: dict):
        self.status_code = status_code
        self.response_body = response_body
        errors = response_body.get("errors", [])
        messages = [
            e.get("detail", e.get("title", "Unknown error")) for e in errors
        ]
        self.message = "; ".join(messages) or f"ASC API error {status_code}"
        super().__init__(self.message)


class ASCRateLimitError(ASCAPIError):
    """Rate limit (429) from App Store Connect API."""

    def __init__(self, response_body: dict, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(429, response_body)
