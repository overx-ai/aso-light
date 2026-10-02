"""Unit tests for the ASC Product Page Optimization (experiment) service.

Drives :class:`app.services.asc.experiment.ASCExperimentService` against a
``FakeASCClient`` that records every call and replays canned JSON:API
responses. Asserts the **v1 vs v2** endpoint split (experiments are v2,
treatments + localizations are v1), the request bodies + relationships, the
``MAX_TREATMENTS`` guard, the lifecycle PATCH bodies, the review submission
path, and the from-upload cleanup-on-failure behaviour.

No network, no DB. Mirrors ``tests/test_cpp.py`` and drives async entrypoints
through the shared ``run_async`` harness.
"""
from __future__ import annotations

import pytest

from app.services.asc.errors import ASCAPIError, ChildResourceNotFoundError
from app.services.asc.experiment import (
    MAX_TREATMENTS,
    ASCExperimentService,
    ExperimentLimitError,
    ReviewSubmissionBusyError,
)
from tests._async_harness import run_async

_V2 = "https://api.appstoreconnect.apple.com/v2"


class FakeASCClient:
    """Records ASC calls and returns scripted responses.

    ``responses`` maps ``(method, path)`` -> payload. ``_get_all_pages`` returns
    the ``data`` list of the matching GET payload. Every call is appended to
    ``calls`` as ``(method, path, json)`` and each GET's params into ``params``
    by path, for assertions. ``BASE_URL`` mirrors
    the real client so the service's v1->v2 prefix swap resolves correctly.
    """

    BASE_URL = "https://api.appstoreconnect.apple.com/v1"

    def __init__(self, responses: dict[tuple[str, str], dict] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.params: dict[str, dict | None] = {}
        self.put_binary_error: Exception | None = None

    def _lookup(self, method: str, path: str) -> dict:
        return self.responses.get((method, path), {"data": []})

    async def _get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(("GET", path, None))
        self.params[path] = params
        return self._lookup("GET", path)

    async def _get_all_pages(
        self, path: str, params: dict | None = None
    ) -> list[dict]:
        self.calls.append(("GET_ALL", path, None))
        self.params[path] = params
        return self._lookup("GET", path).get("data", [])

    async def _post(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("POST", path, json))
        return self._lookup("POST", path)

    async def _patch(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("PATCH", path, json))
        return self._lookup("PATCH", path)

    async def _delete(self, path: str) -> None:
        self.calls.append(("DELETE", path, None))

    async def _put_binary(self, url, data, content_type="application/octet-stream"):
        self.calls.append(("PUT", url, None))
        if self.put_binary_error is not None:
            raise self.put_binary_error


def _service(responses=None) -> tuple[ASCExperimentService, FakeASCClient]:
    client = FakeASCClient(responses)
    return ASCExperimentService(client), client  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Experiments (v2, except the app-level list which is v1)
# ------------------------------------------------------------------


def test_list_experiments_hits_v1_app_scoped_endpoint():
    svc, client = _service({
        ("GET", "/apps/app-1/appStoreVersionExperimentsV2"): {
            "data": [{"id": "exp-1", "attributes": {"name": "Hero test"}}],
        },
    })
    rows = run_async(svc.list_experiments("app-1"))
    assert rows[0]["id"] == "exp-1"
    assert ("GET_ALL", "/apps/app-1/appStoreVersionExperimentsV2", None) in client.calls


def test_get_experiment_hits_v2_endpoint():
    path = f"{_V2}/appStoreVersionExperiments/exp-1"
    svc, client = _service({("GET", path): {"data": {"id": "exp-1"}}})
    out = run_async(svc.get_experiment("exp-1"))
    assert out["id"] == "exp-1"
    assert ("GET", path, None) in client.calls


def test_create_experiment_posts_to_v2_with_app_relationship():
    path = f"{_V2}/appStoreVersionExperiments"
    svc, client = _service({("POST", path): {"data": {"id": "exp-new"}}})
    out = run_async(svc.create_experiment("app-1", "Hero test", 30))
    assert out["id"] == "exp-new"

    _m, post_path, body = next(c for c in client.calls if c[0] == "POST")
    assert post_path == path
    data = body["data"]
    assert data["type"] == "appStoreVersionExperiments"
    assert data["attributes"] == {
        "name": "Hero test", "trafficProportion": 30, "platform": "IOS",
    }
    assert data["relationships"]["app"]["data"] == {"type": "apps", "id": "app-1"}


def test_update_experiment_sends_only_provided_attributes_to_v2():
    path = f"{_V2}/appStoreVersionExperiments/exp-1"
    svc, client = _service({("PATCH", path): {"data": {"id": "exp-1"}}})
    run_async(svc.update_experiment("exp-1", traffic_proportion=75))
    _m, patch_path, body = next(c for c in client.calls if c[0] == "PATCH")
    assert patch_path == path
    assert body["data"]["attributes"] == {"trafficProportion": 75}
    assert body["data"]["id"] == "exp-1"


def test_update_experiment_with_no_fields_raises():
    svc, _client = _service()
    with pytest.raises(ValueError):
        run_async(svc.update_experiment("exp-1"))


_EXP_PATH = f"{_V2}/appStoreVersionExperiments/exp-1"
_IOS_EXPERIMENT = {
    ("GET", _EXP_PATH): {"data": {"id": "exp-1", "attributes": {"platform": "IOS"}}},
}
_OPEN_SUBMISSIONS = ("GET", "/reviewSubmissions")


def test_submit_for_review_goes_through_a_new_review_submission():
    # Live, 2026-09-30: PATCHing state was refused ("The attribute 'state' can
    # not be included in a 'UPDATE' operation"); a test is submitted like a CPP.
    svc, client = _service({
        **_IOS_EXPERIMENT,
        ("POST", "/reviewSubmissions"): {"data": {"id": "sub-1"}},
        ("POST", "/reviewSubmissionItems"): {"data": {"id": "item-1"}},
        ("PATCH", "/reviewSubmissions/sub-1"): {"data": {"id": "sub-1"}},
    })

    run_async(svc.submit_experiment_for_review("app-1", "exp-1"))

    posts = {c[1]: c[2] for c in client.calls if c[0] == "POST"}
    created = posts["/reviewSubmissions"]["data"]
    assert created["attributes"] == {"platform": "IOS"}
    assert created["relationships"]["app"]["data"] == {"type": "apps", "id": "app-1"}
    item = posts["/reviewSubmissionItems"]["data"]["relationships"]
    assert item["reviewSubmission"]["data"] == {"type": "reviewSubmissions", "id": "sub-1"}
    assert item["appStoreVersionExperimentV2"]["data"] == {
        "type": "appStoreVersionExperiments", "id": "exp-1",
    }
    _m, _p, body = next(c for c in client.calls if c[:2] == ("PATCH", "/reviewSubmissions/sub-1"))
    assert body["data"]["attributes"] == {"submitted": True}
    assert not any(c[0] == "PATCH" and c[1] == _EXP_PATH for c in client.calls)


def test_submit_for_review_reuses_an_empty_open_submission():
    svc, client = _service({
        **_IOS_EXPERIMENT,
        _OPEN_SUBMISSIONS: {"data": [{"id": "sub-open"}]},
        ("POST", "/reviewSubmissionItems"): {"data": {"id": "item-1"}},
    })

    run_async(svc.submit_experiment_for_review("app-1", "exp-1"))

    assert not any(c[:2] == ("POST", "/reviewSubmissions") for c in client.calls)
    assert ("PATCH", "/reviewSubmissions/sub-open") in [c[:2] for c in client.calls]


def test_submit_for_review_reads_the_open_submission_for_the_experiments_platform():
    svc, client = _service({
        **_IOS_EXPERIMENT,
        _OPEN_SUBMISSIONS: {"data": [{"id": "sub-open"}]},
    })

    run_async(svc.submit_experiment_for_review("app-1", "exp-1"))

    query = client.params["/reviewSubmissions"]
    assert query["filter[app]"] == "app-1"
    assert query["filter[platform]"] == "IOS"
    assert query["filter[state]"] == "READY_FOR_REVIEW"
    assert client.params["/reviewSubmissions/sub-open/items"]["include"] == (
        "appStoreVersionExperimentV2"
    )


def _item(item_id: str, relationship: str, target_id: str) -> dict:
    return {
        "id": item_id,
        "relationships": {relationship: {"data": {"type": "x", "id": target_id}}},
    }


def test_submit_for_review_retry_resubmits_a_submission_already_holding_this_experiment():
    # The item POST landed but the submitted PATCH did not: the retry must not
    # read its own item as someone else's, nor add it twice.
    svc, client = _service({
        **_IOS_EXPERIMENT,
        _OPEN_SUBMISSIONS: {"data": [{"id": "sub-open"}]},
        ("GET", "/reviewSubmissions/sub-open/items"): {
            "data": [_item("item-1", "appStoreVersionExperimentV2", "exp-1")],
        },
    })

    run_async(svc.submit_experiment_for_review("app-1", "exp-1"))

    assert not any(c[0] == "POST" for c in client.calls)
    assert ("PATCH", "/reviewSubmissions/sub-open") in [c[:2] for c in client.calls]


@pytest.mark.parametrize("items", [
    [_item("cpp-item", "appCustomProductPageVersion", "cpp-v1")],
    [_item("other-test", "appStoreVersionExperimentV2", "exp-2")],
    [
        _item("item-1", "appStoreVersionExperimentV2", "exp-1"),
        _item("cpp-item", "appCustomProductPageVersion", "cpp-v1"),
    ],
    [{"id": "no-linkage"}],
], ids=["cpp", "another-test", "ours-and-a-cpp", "unreadable"])
def test_submit_for_review_refuses_an_open_submission_holding_other_items(items):
    svc, client = _service({
        **_IOS_EXPERIMENT,
        _OPEN_SUBMISSIONS: {"data": [{"id": "sub-open"}]},
        ("GET", "/reviewSubmissions/sub-open/items"): {"data": items},
    })

    with pytest.raises(ReviewSubmissionBusyError, match="sub-open"):
        run_async(svc.submit_experiment_for_review("app-1", "exp-1"))
    assert [c for c in client.calls if c[0] in {"POST", "PATCH"}] == []


@pytest.mark.parametrize(("action", "started"), [("stop_experiment", False), ("start_experiment", True)])
def test_start_and_stop_patch_started_never_state(action, started):
    svc, client = _service({("PATCH", _EXP_PATH): {"data": {"id": "exp-1"}}})
    run_async(getattr(svc, action)("exp-1"))
    _m, _p, body = next(c for c in client.calls if c[0] == "PATCH")
    assert body["data"]["attributes"] == {"started": started}


def test_delete_experiment_issues_v2_delete():
    svc, client = _service()
    run_async(svc.delete_experiment("exp-1"))
    assert ("DELETE", f"{_V2}/appStoreVersionExperiments/exp-1", None) in client.calls


# ------------------------------------------------------------------
# Treatments (v1)
# ------------------------------------------------------------------


def test_list_treatments_hits_v2_experiment_relationship():
    path = f"{_V2}/appStoreVersionExperiments/exp-1/appStoreVersionExperimentTreatments"
    svc, client = _service({
        ("GET", path): {"data": [{"id": "trt-1", "attributes": {"name": "A"}}]},
    })
    rows = run_async(svc.list_treatments("exp-1"))
    assert rows[0]["id"] == "trt-1"
    assert ("GET_ALL", path, None) in client.calls


def test_create_treatment_posts_v1_with_v2_experiment_relationship():
    list_path = (
        f"{_V2}/appStoreVersionExperiments/exp-1"
        "/appStoreVersionExperimentTreatments"
    )
    svc, client = _service({
        ("GET", list_path): {"data": []},  # under the cap
        ("POST", "/appStoreVersionExperimentTreatments"): {"data": {"id": "trt-new"}},
    })
    out = run_async(svc.create_treatment("exp-1", "Bright icon", app_icon_name="Alt1"))
    assert out["id"] == "trt-new"

    _m, path, body = next(c for c in client.calls if c[0] == "POST")
    assert path == "/appStoreVersionExperimentTreatments"
    data = body["data"]
    assert data["attributes"] == {"name": "Bright icon", "appIconName": "Alt1"}
    rel = data["relationships"]["appStoreVersionExperimentV2"]["data"]
    assert rel == {"type": "appStoreVersionExperiments", "id": "exp-1"}


def test_create_treatment_enforces_max_treatments():
    list_path = (
        f"{_V2}/appStoreVersionExperiments/exp-1"
        "/appStoreVersionExperimentTreatments"
    )
    svc, _client = _service({
        ("GET", list_path): {
            "data": [{"id": f"trt-{i}"} for i in range(MAX_TREATMENTS)],
        },
    })
    with pytest.raises(ExperimentLimitError):
        run_async(svc.create_treatment("exp-1", "One too many"))


def test_update_treatment_sends_provided_fields_v1():
    svc, client = _service({
        ("PATCH", "/appStoreVersionExperimentTreatments/trt-1"): {"data": {"id": "trt-1"}},
    })
    run_async(svc.update_treatment("trt-1", name="Renamed"))
    _m, path, body = next(c for c in client.calls if c[0] == "PATCH")
    assert path == "/appStoreVersionExperimentTreatments/trt-1"
    assert body["data"]["attributes"] == {"name": "Renamed"}


def test_delete_treatment_issues_v1_delete():
    svc, client = _service()
    run_async(svc.delete_treatment("trt-1"))
    assert (
        "DELETE", "/appStoreVersionExperimentTreatments/trt-1", None
    ) in client.calls


# ------------------------------------------------------------------
# Treatment localizations (v1)
# ------------------------------------------------------------------


def test_find_or_create_localization_reuses_matching_locale():
    list_path = (
        "/appStoreVersionExperimentTreatments/trt-1"
        "/appStoreVersionExperimentTreatmentLocalizations"
    )
    svc, client = _service({
        ("GET", list_path): {
            "data": [
                {"id": "loc-en", "attributes": {"locale": "en-US"}},
                {"id": "loc-de", "attributes": {"locale": "de-DE"}},
            ],
        },
    })
    loc_id = run_async(svc.find_or_create_localization_id("trt-1", "de-DE"))
    assert loc_id == "loc-de"
    assert not any(c[0] == "POST" for c in client.calls)


def test_find_or_create_localization_creates_when_absent():
    list_path = (
        "/appStoreVersionExperimentTreatments/trt-1"
        "/appStoreVersionExperimentTreatmentLocalizations"
    )
    svc, client = _service({
        ("GET", list_path): {"data": []},
        ("POST", "/appStoreVersionExperimentTreatmentLocalizations"): {
            "data": {"id": "loc-ru", "attributes": {"locale": "ru"}},
        },
    })
    loc_id = run_async(svc.find_or_create_localization_id("trt-1", "ru"))
    assert loc_id == "loc-ru"
    _m, path, body = next(c for c in client.calls if c[0] == "POST")
    assert path == "/appStoreVersionExperimentTreatmentLocalizations"
    assert body["data"]["attributes"] == {"locale": "ru"}
    rel = body["data"]["relationships"]["appStoreVersionExperimentTreatment"]["data"]
    assert rel == {"type": "appStoreVersionExperimentTreatments", "id": "trt-1"}


# ------------------------------------------------------------------
# Screenshots + from-upload
# ------------------------------------------------------------------


def test_get_treatment_screenshots_hits_localization_sets_endpoint():
    path = (
        "/appStoreVersionExperimentTreatmentLocalizations/loc-1/appScreenshotSets"
    )
    svc, client = _service({
        ("GET", path): {
            "data": [{
                "id": "set-1",
                "attributes": {"screenshotDisplayType": "APP_IPHONE_67"},
                "relationships": {"appScreenshots": {"data": [{"id": "shot-1"}]}},
            }],
            "included": [{
                "type": "appScreenshots",
                "id": "shot-1",
                "attributes": {
                    "fileName": "hero.png",
                    "imageAsset": {
                        "templateUrl": "https://cdn/{w}x{h}.{f}",
                        "width": 1290, "height": 2796,
                    },
                },
            }],
        },
    })
    sets = run_async(svc.get_treatment_screenshots("loc-1"))
    assert sets[0]["display_type"] == "APP_IPHONE_67"
    assert sets[0]["screenshots"][0]["source_url"] == "https://cdn/1290x2796.png"
    assert ("GET", path, None) in client.calls


def _upload_responses(loc_created: bool) -> dict:
    """Scripted responses for a from-upload run (localization + set + asset)."""
    loc_list = (
        "/appStoreVersionExperimentTreatments/trt-1"
        "/appStoreVersionExperimentTreatmentLocalizations"
    )
    responses = {
        ("POST", "/appScreenshotSets"): {"data": {"id": "set-1"}},
        ("POST", "/appScreenshots"): {
            "data": {
                "id": "shot-1",
                "attributes": {
                    "uploadOperations": [
                        {"url": "https://upload", "length": 3, "offset": 0},
                    ],
                },
            },
        },
        ("PATCH", "/appScreenshots/shot-1"): {"data": {"id": "shot-1"}},
    }
    if loc_created:
        responses[("GET", loc_list)] = {"data": []}
        responses[("POST", "/appStoreVersionExperimentTreatmentLocalizations")] = {
            "data": {"id": "loc-1", "attributes": {"locale": "en-US"}},
        }
    else:
        responses[("GET", loc_list)] = {
            "data": [{"id": "loc-1", "attributes": {"locale": "en-US"}}],
        }
    return responses


def test_populate_treatment_from_upload_uploads_all_files():
    svc, client = _service(_upload_responses(loc_created=True))
    result = run_async(svc.populate_treatment_from_upload(
        "trt-1", "en-US", "APP_IPHONE_67",
        [("a.png", b"aaa"), ("b.png", b"bbb")],
    ))
    assert result["uploaded_count"] == 2
    assert result["localization_id"] == "loc-1"
    assert sum(1 for c in client.calls if c == ("PUT", "https://upload", None)) == 2


def test_populate_treatment_cleans_up_created_localization_on_failure():
    svc, client = _service(_upload_responses(loc_created=True))
    client.put_binary_error = ASCAPIError(500, {"errors": [{"detail": "boom"}]})
    with pytest.raises(ASCAPIError):
        run_async(svc.populate_treatment_from_upload(
            "trt-1", "en-US", "APP_IPHONE_67", [("a.png", b"aaa")],
        ))
    # The freshly-created localization is best-effort deleted.
    assert (
        "DELETE",
        "/appStoreVersionExperimentTreatmentLocalizations/loc-1",
        None,
    ) in client.calls


def test_populate_treatment_keeps_reused_localization_on_failure():
    svc, client = _service(_upload_responses(loc_created=False))
    client.put_binary_error = ASCAPIError(500, {"errors": [{"detail": "boom"}]})
    with pytest.raises(ASCAPIError):
        run_async(svc.populate_treatment_from_upload(
            "trt-1", "en-US", "APP_IPHONE_67", [("a.png", b"aaa")],
        ))
    # A reused (pre-existing) localization must NOT be deleted.
    assert not any(c[0] == "DELETE" for c in client.calls)


# ------------------------------------------------------------------
# Child-membership guards (IDOR protection)
# ------------------------------------------------------------------


def test_assert_experiment_in_app_passes_for_member():
    svc, _client = _service({
        ("GET", "/apps/app-1/appStoreVersionExperimentsV2"): {
            "data": [{"id": "exp-1"}, {"id": "exp-2"}],
        },
    })
    # No raise for a member.
    run_async(svc.assert_experiment_in_app("app-1", "exp-2"))


def test_assert_experiment_in_app_rejects_non_member():
    svc, _client = _service({
        ("GET", "/apps/app-1/appStoreVersionExperimentsV2"): {
            "data": [{"id": "exp-1"}],
        },
    })
    with pytest.raises(ChildResourceNotFoundError):
        run_async(svc.assert_experiment_in_app("app-1", "exp-from-other-app"))


def test_assert_treatment_in_experiment_rejects_non_member():
    path = f"{_V2}/appStoreVersionExperiments/exp-1/appStoreVersionExperimentTreatments"
    svc, _client = _service({("GET", path): {"data": [{"id": "trt-1"}]}})
    run_async(svc.assert_treatment_in_experiment("exp-1", "trt-1"))
    with pytest.raises(ChildResourceNotFoundError):
        run_async(svc.assert_treatment_in_experiment("exp-1", "trt-elsewhere"))


def test_assert_localization_in_treatment_rejects_non_member():
    path = (
        "/appStoreVersionExperimentTreatments/trt-1"
        "/appStoreVersionExperimentTreatmentLocalizations"
    )
    svc, _client = _service({("GET", path): {"data": [{"id": "loc-1"}]}})
    run_async(svc.assert_localization_in_treatment("trt-1", "loc-1"))
    with pytest.raises(ChildResourceNotFoundError):
        run_async(svc.assert_localization_in_treatment("trt-1", "loc-elsewhere"))


# ------------------------------------------------------------------
# REST PATCH {state}: routed to the review submission or `started`
# ------------------------------------------------------------------


def _patch_route(monkeypatch, body: dict, responses: dict | None = None):
    from types import SimpleNamespace

    from app.api.v1 import experiment as api
    from app.schemas.experiment import ExperimentUpdateIn

    client = FakeASCClient({
        ("GET", "/apps/app-1/appStoreVersionExperimentsV2"): {"data": [{"id": "exp-1"}]},
        **_IOS_EXPERIMENT,
        ("PATCH", _EXP_PATH): {"data": {"id": "exp-1"}},
        ("POST", "/reviewSubmissions"): {"data": {"id": "sub-1"}},
        **(responses or {}),
    })

    class _Session:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc):
            return False

    async def verified_app(*_args):
        return SimpleNamespace(asc_app_id="app-1")

    async def asc_client(*_args):
        return _Session()

    monkeypatch.setattr(api, "_get_verified_app", verified_app)
    monkeypatch.setattr(api, "_get_asc_client_for_app", asc_client)
    run_async(api.update_experiment(
        1, "exp-1", ExperimentUpdateIn(**body), current_user={"user_id": 1}, session=None,
    ))
    return client


def _writes(client: FakeASCClient) -> list[tuple[str, str]]:
    return [c[:2] for c in client.calls if c[0] in {"POST", "PATCH"}]


def test_rest_patch_state_waiting_for_review_goes_through_a_review_submission(monkeypatch):
    client = _patch_route(monkeypatch, {"name": "1.6 shots", "state": "WAITING_FOR_REVIEW"})

    assert _writes(client) == [
        ("PATCH", _EXP_PATH),
        ("POST", "/reviewSubmissions"),
        ("POST", "/reviewSubmissionItems"),
        ("PATCH", "/reviewSubmissions/sub-1"),
    ]
    experiment_patch = next(c[2] for c in client.calls if c[:2] == ("PATCH", _EXP_PATH))
    assert experiment_patch["data"]["attributes"] == {"name": "1.6 shots"}


def test_rest_patch_state_stopped_sets_started_false(monkeypatch):
    client = _patch_route(monkeypatch, {"state": "STOPPED"})

    assert _writes(client) == [("PATCH", _EXP_PATH)]
    assert client.calls[-1][2]["data"]["attributes"] == {"started": False}


def test_rest_patch_with_nothing_to_update_is_a_400(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        _patch_route(monkeypatch, {})
    assert caught.value.status_code == 400


def test_rest_submit_into_a_busy_submission_is_a_409_naming_it(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        _patch_route(monkeypatch, {"state": "WAITING_FOR_REVIEW"}, {
            _OPEN_SUBMISSIONS: {"data": [{"id": "sub-open"}]},
            ("GET", "/reviewSubmissions/sub-open/items"): {
                "data": [_item("cpp-item", "appCustomProductPageVersion", "cpp-v1")],
            },
        })
    assert caught.value.status_code == 409
    assert "sub-open" in caught.value.detail
