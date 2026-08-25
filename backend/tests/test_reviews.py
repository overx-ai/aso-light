"""Unit tests for Customer Reviews: ASCReviewService + route-layer helpers.

Drives :class:`app.services.asc.reviews.ASCReviewService` against a
``FakeASCClient`` that records every call, mirroring the pattern in
``tests/test_experiment.py``. Also covers the pure serialization helpers in
``app.api.v1.reviews`` (``_serialize_review``, ``_extract_cursor``,
``_territory_to_locale``) and the request/response schema validation in
``app.schemas.review``.

No network, no DB, no FastAPI TestClient — this repo has no HTTP-level test
convention anywhere in ``tests/``, so route dispatch (auth, 502/503 mapping)
is exercised at the unit level only, same as every other ASC-backed router.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.reviews import (
    _extract_cursor,
    _serialize_review,
    _territory_to_locale,
)
from app.schemas.review import DraftIn, ReplyIn, TranslateReviewIn
from app.services.asc.reviews import RESPONSE_BODY_MAX_LEN, ASCReviewService
from tests._async_harness import run_async


class FakeASCClient:
    """Records ASC calls and returns scripted responses.

    ``responses`` maps ``(method, path)`` -> payload. Every call is appended
    to ``calls`` as ``(method, path, params_or_json)`` for assertions.
    """

    def __init__(self, responses: dict[tuple[str, str], dict] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def _lookup(self, method: str, path: str) -> dict:
        return self.responses.get((method, path), {"data": {}})

    async def _get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(("GET", path, params))
        return self._lookup("GET", path)

    async def _post(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("POST", path, json))
        return self._lookup("POST", path)

    async def _patch(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("PATCH", path, json))
        return self._lookup("PATCH", path)

    async def _delete(self, path: str) -> None:
        self.calls.append(("DELETE", path, None))


def _service(responses=None) -> tuple[ASCReviewService, FakeASCClient]:
    client = FakeASCClient(responses)
    return ASCReviewService(client), client  # type: ignore[arg-type]


# ------------------------------------------------------------------
# ASCReviewService — reads
# ------------------------------------------------------------------


def test_list_reviews_hits_app_scoped_endpoint_with_expected_params():
    svc, client = _service()
    run_async(svc.list_reviews("app-1"))

    _m, path, params = client.calls[0]
    assert path == "/v1/apps/app-1/customerReviews"
    assert params["include"] == "response"
    assert params["sort"] == "-createdDate"
    assert "filter[territory]" not in params
    assert "filter[rating]" not in params
    assert "cursor" not in params


def test_list_reviews_uppercases_territory_and_stringifies_rating():
    svc, client = _service()
    run_async(svc.list_reviews("app-1", territory="usa", rating=1))

    _m, _path, params = client.calls[0]
    assert params["filter[territory]"] == "USA"
    assert params["filter[rating]"] == "1"


def test_list_reviews_passes_cursor_through():
    svc, client = _service()
    run_async(svc.list_reviews("app-1", cursor="abc123"))

    _m, _path, params = client.calls[0]
    assert params["cursor"] == "abc123"


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (1, 1), (200, 200), (500, 200), (-5, 1)],
)
def test_list_reviews_clamps_limit_to_valid_range(requested, expected):
    svc, client = _service()
    run_async(svc.list_reviews("app-1", limit=requested))

    _m, _path, params = client.calls[0]
    assert params["limit"] == expected


def test_get_review_hits_expected_path_with_include():
    svc, client = _service()
    run_async(svc.get_review("rev-1"))

    _m, path, params = client.calls[0]
    assert path == "/v1/customerReviews/rev-1"
    assert params["include"] == "response"


# ------------------------------------------------------------------
# ASCReviewService — response CRUD
# ------------------------------------------------------------------


def test_create_response_posts_expected_json_api_body():
    svc, client = _service({
        ("POST", "/v1/customerReviewResponses"): {
            "data": {"id": "resp-1", "attributes": {"responseBody": "Thanks!"}},
        },
    })
    out = run_async(svc.create_response("rev-1", "Thanks!"))

    assert out["id"] == "resp-1"
    _m, path, body = client.calls[0]
    assert path == "/v1/customerReviewResponses"
    data = body["data"]
    assert data["type"] == "customerReviewResponses"
    assert data["attributes"] == {"responseBody": "Thanks!"}
    assert data["relationships"]["review"]["data"] == {
        "type": "customerReviews", "id": "rev-1",
    }


def test_update_response_patches_expected_json_api_body():
    svc, client = _service({
        ("PATCH", "/v1/customerReviewResponses/resp-1"): {
            "data": {"id": "resp-1", "attributes": {"responseBody": "Updated"}},
        },
    })
    out = run_async(svc.update_response("resp-1", "Updated"))

    assert out["id"] == "resp-1"
    _m, path, body = client.calls[0]
    assert path == "/v1/customerReviewResponses/resp-1"
    data = body["data"]
    assert data["type"] == "customerReviewResponses"
    assert data["id"] == "resp-1"
    assert data["attributes"] == {"responseBody": "Updated"}


def test_delete_response_issues_expected_delete():
    svc, client = _service()
    run_async(svc.delete_response("resp-1"))

    assert ("DELETE", "/v1/customerReviewResponses/resp-1", None) in client.calls


# ------------------------------------------------------------------
# Route-layer helpers: app.api.v1.reviews
# ------------------------------------------------------------------


def test_serialize_review_resolves_included_response():
    raw = {
        "id": "rev-1",
        "attributes": {
            "rating": 1,
            "title": "Crashes",
            "body": "It crashes every time I open it.",
            "territory": "USA",
            "reviewerNickname": "user123",
            "createdDate": "2026-01-01T00:00:00Z",
        },
        "relationships": {
            "response": {"data": {"type": "customerReviewResponses", "id": "resp-1"}},
        },
    }
    included = [{
        "type": "customerReviewResponses",
        "id": "resp-1",
        "attributes": {
            "responseBody": "Sorry to hear that!",
            "lastModifiedDate": "2026-01-02T00:00:00Z",
            "state": "PUBLISHED",
        },
    }]

    out = _serialize_review(raw, included)

    assert out.id == "rev-1"
    assert out.rating == 1
    assert out.theme == "bug"
    assert out.response is not None
    assert out.response.id == "resp-1"
    assert out.response.body == "Sorry to hear that!"
    assert out.response.state == "PUBLISHED"


def test_serialize_review_without_response_relationship():
    raw = {
        "id": "rev-2",
        "attributes": {
            "rating": 5,
            "title": "Great",
            "body": "Love this app!",
            "territory": "USA",
        },
    }

    out = _serialize_review(raw, included=[])

    assert out.response is None
    assert out.theme == "praise"


def test_serialize_review_handles_missing_body():
    raw = {"id": "rev-3", "attributes": {"rating": 3, "territory": "GBR"}}

    out = _serialize_review(raw)

    assert out.body is None


def test_extract_cursor_parses_next_link():
    payload = {"links": {"next": "https://api/v1/x?cursor=abc123&limit=50"}}
    assert _extract_cursor(payload) == "abc123"


def test_extract_cursor_returns_none_when_no_next_link():
    assert _extract_cursor({}) is None
    assert _extract_cursor({"links": {}}) is None


@pytest.mark.parametrize(
    ("territory", "expected_locale"),
    [
        ("USA", "en-US"),
        ("deu", "de-DE"),
        ("JPN", "ja-JP"),
        (None, "en-US"),
        ("ZZZ", "en-US"),
    ],
)
def test_territory_to_locale_mapping(territory, expected_locale):
    assert _territory_to_locale(territory) == expected_locale


# ------------------------------------------------------------------
# Schemas: char limits + defaults
# ------------------------------------------------------------------


def test_reply_in_accepts_max_length():
    ReplyIn(body="x" * RESPONSE_BODY_MAX_LEN)


def test_reply_in_rejects_over_max_length():
    with pytest.raises(ValidationError):
        ReplyIn(body="x" * (RESPONSE_BODY_MAX_LEN + 1))


def test_reply_in_rejects_empty_body():
    with pytest.raises(ValidationError):
        ReplyIn(body="")


def test_response_body_max_len_matches_apple_documented_cap():
    assert RESPONSE_BODY_MAX_LEN == 5970


def test_draft_in_defaults_to_neutral_tone_and_no_theme():
    draft = DraftIn()
    assert draft.tone == "neutral"
    assert draft.theme is None


def test_translate_review_in_rejects_short_locale():
    with pytest.raises(ValidationError):
        TranslateReviewIn(target_locale="d")
