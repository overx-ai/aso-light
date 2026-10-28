from __future__ import annotations

from fastapi import status


class ListingError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST


class InvalidProposalError(ListingError):
    pass


class UnknownStorefrontError(ListingError):
    pass


class UnknownLocaleError(ListingError):
    pass


class ListingNotBrainstormedError(ListingError):
    status_code = status.HTTP_409_CONFLICT


class NoProposalDraftError(ListingError):
    status_code = status.HTTP_404_NOT_FOUND


class TranslatorNotConfiguredError(ListingError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class ListingQuotaError(ListingError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class ListingUpstreamError(ListingError):
    status_code = status.HTTP_502_BAD_GATEWAY
