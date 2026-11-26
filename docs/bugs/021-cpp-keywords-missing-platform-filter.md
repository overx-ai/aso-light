---
id: 021
title: "cpp_keywords_get / cpp_keywords_set fail: ASC now requires filter[platform] on searchKeywords"
status: open
priority: p1
created: 2026-11-26
updated: 2026-11-26
files: backend/app/services/asc/cpp.py
---

# BUG 021 - CPP keywords: missing platform filter

> **TL;DR** — Every `cpp_keywords_get` call fails with `ASC API error: Filter 'platform' is required for this operation.` `list_app_keyword_ids` calls `GET /v1/apps/{id}/searchKeywords` with only `filter[locale]`. Add `"filter[platform]": "IOS"`.

## Reproduction

`cpp_keywords_get(app_id=3, cpp_id="73693b49-6fb2-4604-8456-00ba6dcdb671", locale="en-US")` (Refresher Coherence CPP, 2026-11-26) fails with the error above, for all 6 locales.

The same request made directly with `filter[platform]=IOS` returns 17 en-US keyword ids. The linkage endpoint (`/appCustomProductPageLocalizations/{id}/relationships/searchKeywords`) does not need the filter: POST returned 204, and the GET read the keywords back.

## Fix

In `CppService.list_app_keyword_ids` (`backend/app/services/asc/cpp.py`), add `"filter[platform]": "IOS"` to the params. Take the platform from the app record if aso-light ever handles macOS apps. Add a regression test in `tests/test_cpp_keywords.py` that asserts the param is sent. `cpp_keywords_set` diffs against the same list, so it is fixed by the same change.
