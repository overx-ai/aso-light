"""Read + write ASC service for Customer Reviews and their responses."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient


# App Store Connect documents the response body cap as 5970 characters.
RESPONSE_BODY_MAX_LEN = 5970


class ASCReviewService:
    """Customer Reviews + customerReviewResponses CRUD against ASC.

    All reads return the raw JSON:API ``data`` shape so the route layer
    can serialize directly into Pydantic schemas. Writes return the
    created/updated resource dict.
    """

    def __init__(self, client: "ASCClient") -> None:
        self.client = client

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_reviews(
        self,
        asc_app_id: str,
        *,
        territory: str | None = None,
        rating: int | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List customer reviews for an app.

        Returns the raw single-page payload from ASC (``{data, included, links, meta}``)
        so the caller can pass back ``links.next`` cursor for pagination.

        Apple does NOT support a ``has_response`` filter directly; the route
        layer filters in memory after fetching. Because that filter is applied
        after pagination, a page that looked full-size on the wire can shrink
        (or even empty out) once filtered, while ``next_cursor`` still advances
        a full page at a time — this is a known UX quirk, not a data-loss bug.
        """
        params: dict[str, Any] = {
            "limit": min(max(limit, 1), 200),
            "include": "response",
            "sort": "-createdDate",
            "fields[customerReviews]": (
                "rating,title,body,reviewerNickname,createdDate,territory"
            ),
            "fields[customerReviewResponses]": (
                "responseBody,lastModifiedDate,state"
            ),
        }
        if territory:
            params["filter[territory]"] = territory.upper()
        if rating is not None:
            params["filter[rating]"] = str(rating)
        if cursor:
            params["cursor"] = cursor

        return await self.client._get(
            f"/v1/apps/{asc_app_id}/customerReviews", params=params,
        )

    async def get_review(self, review_id: str) -> dict[str, Any]:
        """Fetch a single review with its response (if any)."""
        return await self.client._get(
            f"/v1/customerReviews/{review_id}",
            params={
                "include": "response",
                "fields[customerReviews]": (
                    "rating,title,body,reviewerNickname,createdDate,territory"
                ),
                "fields[customerReviewResponses]": (
                    "responseBody,lastModifiedDate,state"
                ),
            },
        )

    # ------------------------------------------------------------------
    # Writes (response CRUD)
    # ------------------------------------------------------------------

    async def create_response(
        self, review_id: str, body: str,
    ) -> dict[str, Any]:
        """Create a customerReviewResponse against the given review."""
        payload = {
            "data": {
                "type": "customerReviewResponses",
                "attributes": {"responseBody": body},
                "relationships": {
                    "review": {
                        "data": {"type": "customerReviews", "id": review_id},
                    },
                },
            },
        }
        result = await self.client._post(
            "/v1/customerReviewResponses", json=payload,
        )
        return result.get("data", {})

    async def update_response(
        self, response_id: str, body: str,
    ) -> dict[str, Any]:
        """Update an existing response's body."""
        payload = {
            "data": {
                "type": "customerReviewResponses",
                "id": response_id,
                "attributes": {"responseBody": body},
            },
        }
        result = await self.client._patch(
            f"/v1/customerReviewResponses/{response_id}", json=payload,
        )
        return result.get("data", {})

    async def delete_response(self, response_id: str) -> None:
        """Remove a response — Apple wipes it from the listing."""
        await self.client._delete(f"/v1/customerReviewResponses/{response_id}")
