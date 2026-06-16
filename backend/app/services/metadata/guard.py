"""Server-side editability guard for metadata write paths.

CLAUDE.md convention: when ``editable_version_state == 'READY_FOR_DISTRIBUTION'``
only ``promotional_text`` is mutable on ``appStoreVersionLocalizations``, and the
``editable_fields`` list returned by ``GET /apps/{id}/metadata`` is the single
source of truth for what the UI (and any client) may write.

The bug this module closes: the UI honoured ``editable_fields`` but the server
did not enforce it on writes, so a hand-rolled HTTP client or MCP tool could
mutate a locked field anyway.

:func:`assert_fields_editable` is the one shared enforcement point. It reads the
per-app ``app_metadata_state.editable_fields_json`` (the same projection the
snapshot computes and the UI consumes) and rejects any attempted field that is
not in that list. It is called BEFORE the ASC network call on every single-locale
create/update path and inside the bulk apply loop.

Fail-closed: if there is no state row yet (never synced) the write is rejected —
the caller must sync first, which is also what the create paths already require.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metadata import AppMetadataState


class FieldsNotEditableError(Exception):
    """Raised when a write targets fields absent from ``editable_fields``.

    ``locked_fields`` carries the snake_case names the caller tried to write
    that the current version/app-info state does not permit. The REST layer
    maps this to 409 CONFLICT; the MCP layer maps it to ``ToolError``.
    """

    def __init__(self, locked_fields: list[str]) -> None:
        self.locked_fields = locked_fields
        joined = ", ".join(locked_fields)
        super().__init__(
            "Fields not editable in the current version state: "
            f"{joined}. Only the fields in editable_fields may be written."
        )


async def assert_fields_editable(
    session: AsyncSession,
    app_id: int,
    attempted_fields: Iterable[str],
) -> None:
    """Reject any attempted snake_case field not in ``editable_fields``.

    ``editable_fields`` (stored as ``editable_fields_json`` on
    :class:`AppMetadataState`) is the single source of truth — derive the
    guard from it, never from raw state strings, so guard and UI agree.

    Args:
        session: Active DB session.
        app_id: Local app id.
        attempted_fields: snake_case field names the write would set.

    Raises:
        FieldsNotEditableError: when one or more attempted fields are locked,
            or when there is no state row yet (fail closed — sync first).
    """
    attempted = list(attempted_fields)
    if not attempted:
        return

    result = await session.execute(
        select(AppMetadataState.editable_fields_json).where(
            AppMetadataState.app_id == app_id,
        )
    )
    editable_fields_json = result.scalar_one_or_none()
    # ``scalar_one_or_none`` returns None both when there is no row and when
    # the column itself is NULL; treat both as "nothing editable" (fail closed).
    editable = set(editable_fields_json or [])

    locked = [field for field in attempted if field not in editable]
    if locked:
        raise FieldsNotEditableError(locked)


# Snake_case fields that may appear on a metadata write body, partitioned by
# the localization ``kind`` they belong to. Mirrors the field partition in
# ``app.services.metadata.bulk`` but kept local so the guard has no import
# cycle with the route/service layer.
_FIELDS_BY_KIND: dict[str, frozenset[str]] = {
    "app_info": frozenset({"name", "subtitle", "privacy_policy_url"}),
    "version": frozenset({
        "description",
        "keywords",
        "promotional_text",
        "whats_new",
        "marketing_url",
        "support_url",
    }),
}


def attempted_fields_for(kind: str, set_fields: Iterable[str]) -> list[str]:
    """Project the caller-set field names onto the fields valid for ``kind``.

    ``set_fields`` is typically ``body.model_dump(exclude_unset=True).keys()``
    — only the fields the write actually touches. We intersect with the fields
    that belong to the given ``kind`` so unrelated keys (or a field sent on the
    wrong ``kind``) are not asserted against ``editable_fields``.
    """
    valid = _FIELDS_BY_KIND.get(kind, frozenset())
    return [field for field in set_fields if field in valid]


async def assert_body_fields_editable(
    session: AsyncSession,
    app_id: int,
    kind: str,
    set_fields: Iterable[str],
) -> None:
    """Guard a write body in one call: project ``set_fields`` for ``kind`` then
    assert they are editable.

    The single enforcement point shared by the REST and MCP write paths — they
    differ only in how they translate the raised :class:`FieldsNotEditableError`
    (REST → 409, MCP → ``ToolError``), so the projection + assertion live here
    and can never diverge between the two surfaces.

    Raises:
        FieldsNotEditableError: when one or more set fields are locked.
    """
    await assert_fields_editable(
        session, app_id, attempted_fields_for(kind, set_fields)
    )
