"""The asset-sync engine shared by App Store screenshots and app previews.

``appScreenshotSets -> appScreenshots`` and ``appPreviewSets -> appPreviews``
have one shape under every parent localization (the main listing, a Custom
Product Page, a PPO treatment): a set per device type holding an ordered list
of assets, uploaded by reserve -> PUT -> commit. :class:`AssetKind` names the
resources and attributes that differ between the two trees, so the set/asset
calls, the export-directory scan and the plan/apply of
:class:`LocalizationAssetService` (specs 013, 015, 016, 017) exist once.

``app.services.asc.screenshots`` and ``app.services.asc.previews`` bind the
engine to their kind and say how one export file is read and checked.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from app.core.config import settings
from app.schemas.previews import PREVIEW_TYPES
from app.schemas.screenshots import (
    MAX_SCREENSHOT_FILES,
    SCREENSHOT_DISPLAY_TYPES,
    SyncAction,
)
from app.services.asc.errors import ASCAPIError

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient

logger = logging.getLogger(__name__)

# Apple's ``assetDeliveryState.state`` / ``videoDeliveryState.state`` values
# that matter to us. A committed asset lands in ``UPLOAD_COMPLETE`` (a video
# then passes through ``PROCESSING``) and is promoted to ``COMPLETE``
# asynchronously; ``FAILED`` means Apple rejected the asset even though every
# HTTP call returned 2xx.
ASSET_STATE_COMPLETE = "COMPLETE"
ASSET_STATE_FAILED = "FAILED"
ASSET_STATE_AWAITING_UPLOAD = "AWAITING_UPLOAD"
_NOT_PROCESSING_STATES = frozenset(
    {None, ASSET_STATE_AWAITING_UPLOAD, ASSET_STATE_FAILED}
)

# ==================================================================
# Media rules — what Apple accepts per kind (checked 2026-10-17)
# ==================================================================

# Portrait (width, height). The 6.9" iPhone has no display type of its own:
# App Store Connect files its 1320x2868 screenshots under APP_IPHONE_67.
DISPLAY_TYPE_BY_SIZE: dict[tuple[int, int], str] = {
    (1320, 2868): "APP_IPHONE_67",
    (1290, 2796): "APP_IPHONE_67",
    (1284, 2778): "APP_IPHONE_65",
    (1242, 2688): "APP_IPHONE_65",
    (1242, 2208): "APP_IPHONE_55",
    (2064, 2752): "APP_IPAD_PRO_3GEN_129",
    (2048, 2732): "APP_IPAD_PRO_3GEN_129",
    (1668, 2420): "APP_IPAD_PRO_3GEN_11",
    (1668, 2388): "APP_IPAD_PRO_3GEN_11",
    (422, 514): "APP_WATCH_ULTRA",
    (410, 502): "APP_WATCH_ULTRA",
    (416, 496): "APP_WATCH_SERIES_10",
    (396, 484): "APP_WATCH_SERIES_7",
    (368, 448): "APP_WATCH_SERIES_4",
    (312, 390): "APP_WATCH_SERIES_3",
}
# Every current iPhone takes one 886x1920 preview; the 13" iPad takes 1200x1600.
PREVIEW_TYPE_BY_SIZE: dict[tuple[int, int], str] = {
    (886, 1920): "IPHONE_67",
    (1200, 1600): "IPAD_PRO_3GEN_129",
}
PREVIEW_MIN_S = 15
PREVIEW_MAX_S = 30
PREVIEW_MAX_BYTES = 500 * 1024 * 1024
PREVIEW_MAX_PER_TYPE = 3
PREVIEW_MAX_FPS = 30
PREVIEW_VIDEO_CODEC = "h264"
PREVIEW_AUDIO_CHANNELS = 2
PREVIEW_SUFFIXES = frozenset({".mp4", ".mov", ".m4v"})
PREVIEW_POSTER_SUFFIX = ".poster.txt"
PREVIEW_DEFAULT_FRAME_TIME_CODE = "00:00:05:00"


@dataclass(frozen=True, eq=False)
class AssetKind:
    """The resource names and limits that differ between screenshots and previews.

    ``asset_type`` is both the asset resource type and the set's relationship
    to its assets; ``asset_set`` is the asset's relationship back to its set.
    """

    noun: str
    set_type: str
    asset_type: str
    asset_set: str
    type_attribute: str
    state_attribute: str
    list_fields: str
    max_files: int
    type_param: str
    valid_types: frozenset[str]
    type_by_size: Mapping[tuple[int, int], str]

    @property
    def type_label(self) -> str:
        return self.type_param.replace("_", " ")

    @property
    def types_param(self) -> str:
        return f"{self.type_param}s"

    def type_for_size(self, width: int, height: int) -> str | None:
        return self.type_by_size.get((min(width, height), max(width, height)))


SCREENSHOT = AssetKind(
    noun="screenshot",
    set_type="appScreenshotSets",
    asset_type="appScreenshots",
    asset_set="appScreenshotSet",
    type_attribute="screenshotDisplayType",
    state_attribute="assetDeliveryState",
    list_fields="fileName,fileSize,imageAsset,assetDeliveryState,sourceFileChecksum",
    max_files=MAX_SCREENSHOT_FILES,
    type_param="display_type",
    valid_types=SCREENSHOT_DISPLAY_TYPES,
    type_by_size=DISPLAY_TYPE_BY_SIZE,
)
PREVIEW = AssetKind(
    noun="preview",
    set_type="appPreviewSets",
    asset_type="appPreviews",
    asset_set="appPreviewSet",
    type_attribute="previewType",
    state_attribute="videoDeliveryState",
    list_fields="fileName,fileSize,videoDeliveryState,sourceFileChecksum",
    max_files=PREVIEW_MAX_PER_TYPE,
    type_param="preview_type",
    valid_types=PREVIEW_TYPES,
    type_by_size=PREVIEW_TYPE_BY_SIZE,
)


# ==================================================================
# Set + asset calls, parameterised by kind
# ==================================================================


def source_checksum(file_bytes: bytes) -> str:
    # Apple requires md5 for sourceFileChecksum — this is a content checksum
    # for upload integrity, not a security primitive.
    return hashlib.md5(file_bytes).hexdigest()  # noqa: S324


def _missing_id_error(detail: str) -> ASCAPIError:
    """The shaped ASC error every caller already turns into a one-line message."""
    return ASCAPIError(502, {"errors": [{"detail": detail}]})


async def asset_set_ids(
    client: ASCClient, kind: AssetKind, localization_type: str, localization_id: str
) -> dict[str, str]:
    """``set type -> set id`` for a localization, without reading any asset."""
    existing = await client._get_all_pages(
        f"/{localization_type}/{localization_id}/{kind.set_type}",
        params={f"fields[{kind.set_type}]": kind.type_attribute, "limit": 200},
    )
    ids: dict[str, str] = {}
    for set_obj in existing:
        type_value = (set_obj.get("attributes") or {}).get(kind.type_attribute)
        if type_value:
            ids.setdefault(type_value, set_obj["id"])
    return ids


async def find_or_create_asset_set(
    client: ASCClient,
    kind: AssetKind,
    localization_type: str,
    localization_id: str,
    relationship_key: str,
    type_value: str,
) -> str:
    """The localization's set for ``type_value``, created when absent.

    ``relationship_key`` is the set's relationship back to its localization
    (e.g. ``appCustomProductPageLocalization``); it is the same for both kinds.
    """
    existing = await asset_set_ids(client, kind, localization_type, localization_id)
    if type_value in existing:
        return existing[type_value]

    body = {
        "data": {
            "type": kind.set_type,
            "attributes": {kind.type_attribute: type_value},
            "relationships": {
                relationship_key: {
                    "data": {"type": localization_type, "id": localization_id},
                },
            },
        }
    }
    response = await client._post(f"/{kind.set_type}", json=body)
    return response["data"]["id"]


async def upload_asset(
    client: ASCClient,
    kind: AssetKind,
    set_id: str,
    file_bytes: bytes,
    file_name: str,
    attributes: Mapping[str, str] | None = None,
) -> dict:
    """Upload an asset to a known set via the 3-step reserve/PUT/commit flow.

    1. ``POST /v1/{asset_type}`` reserves the asset (``attributes`` are extra
       reservation attributes, e.g. a preview's ``previewFrameTimeCode``) and
       returns ``uploadOperations`` pre-signed PUT URLs.
    2. ``PUT`` the source bytes to each upload operation URL (no auth headers —
       Apple rejects Bearer tokens on the pre-signed S3 URLs).
    3. ``PATCH`` ``uploaded=true`` with the source file's md5 checksum.

    Returns:
        The committed asset resource dict.
    """
    checksum = source_checksum(file_bytes)

    reserve_body = {
        "data": {
            "type": kind.asset_type,
            "attributes": {
                "fileName": file_name,
                "fileSize": len(file_bytes),
                **(attributes or {}),
            },
            "relationships": {
                kind.asset_set: {
                    "data": {"type": kind.set_type, "id": set_id},
                },
            },
        }
    }
    reservation = await client._post(f"/{kind.asset_type}", json=reserve_body)

    reserved = (reservation or {}).get("data") or {}
    asset_id = str(reserved.get("id") or "")
    if not asset_id:
        # Without an id the commit below would PATCH the collection, not a
        # resource.
        raise _missing_id_error(
            f"App Store Connect returned no {kind.noun} id when "
            f"reserving {file_name!r}; nothing was uploaded."
        )
    operations = (reserved.get("attributes") or {}).get("uploadOperations", [])

    for op in operations:
        content_type = "application/octet-stream"
        for hdr in op.get("requestHeaders", []):
            if hdr.get("name", "").lower() == "content-type":
                content_type = hdr["value"]
        offset = op.get("offset", 0)
        await client._put_binary(
            op["url"],
            file_bytes[offset : offset + op["length"]],
            content_type=content_type,
        )

    commit_body = {
        "data": {
            "type": kind.asset_type,
            "id": asset_id,
            "attributes": {
                "uploaded": True,
                "sourceFileChecksum": checksum,
            },
        }
    }
    response = await client._patch(f"/{kind.asset_type}/{asset_id}", json=commit_body)
    return response.get("data", {})


async def list_set_resources(
    client: ASCClient, kind: AssetKind, set_id: str
) -> list[dict]:
    """A set's raw asset resources **in set order**, delivery state included.

    Apple returns the assets in the set's own display order, which is what
    makes a positional (locale, type, position) replace well-defined.
    """
    return await client._get_all_pages(
        f"/{kind.set_type}/{set_id}/{kind.asset_type}",
        params={f"fields[{kind.asset_type}]": kind.list_fields, "limit": 200},
    )


def shape_asset(resource: dict, kind: AssetKind) -> dict:
    """The fields the sync compares: name, size, MD5 and Apple's delivery state."""
    attrs = resource.get("attributes", {}) or {}
    delivery = attrs.get(kind.state_attribute) or {}
    return {
        "id": resource.get("id", ""),
        "file_name": attrs.get("fileName"),
        "state": delivery.get("state"),
        "checksum": attrs.get("sourceFileChecksum"),
        "file_size": attrs.get("fileSize"),
        "errors": [
            err.get("description") or err.get("code") or "unknown error"
            for err in (delivery.get("errors") or [])
        ],
    }


async def set_asset_order(
    client: ASCClient, kind: AssetKind, set_id: str, asset_ids: list[str]
) -> None:
    """Replace a set's display order with the full ordered id list."""
    body = {"data": [{"type": kind.asset_type, "id": asset_id} for asset_id in asset_ids]}
    await client._patch(
        f"/{kind.set_type}/{set_id}/relationships/{kind.asset_type}", json=body
    )


async def delete_asset(client: ASCClient, kind: AssetKind, asset_id: str) -> None:
    await client._delete(f"/{kind.asset_type}/{asset_id}")


async def delete_asset_set(client: ASCClient, kind: AssetKind, set_id: str) -> None:
    """Apple keeps an emptied set around as a *configured* device type with
    zero assets, which is exactly the shape that fails review. Callers that
    empty a set should prune it."""
    await client._delete(f"/{kind.set_type}/{set_id}")


# ==================================================================
# Sync targets
# ==================================================================


class NotEditableError(Exception):
    """No version of the parent accepts asset writes; ``message`` names why."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class EditableVersion:
    """The version (or PPO treatment) asset writes target."""

    id: str
    state: str | None
    version_string: str | None


def locales_of(localizations: list[dict]) -> dict[str, str]:
    """``locale -> localization id`` from raw localization resources."""
    result: dict[str, str] = {}
    for resource in localizations:
        locale = (resource.get("attributes", {}) or {}).get("locale")
        if locale:
            result[locale] = resource["id"]
    return result


class LocalizationAssetService:
    """Set/asset operations of one kind on one parent localization type, and
    the plan/apply of an export directory into it.

    A subclass binds the *kind* (how an export file is read and checked) and a
    *source* (the parent type, the set's relationship back to it, the
    version's locales and, where the source allows it, creating a missing
    localization). The main listing, a Custom Product Page and a PPO
    treatment are the sources, so the sync loop below exists once.
    """

    kind: ClassVar[AssetKind]
    localization_type: ClassVar[str]
    set_relationship: ClassVar[str]

    def __init__(self, client: ASCClient) -> None:
        self.client = client

    async def localizations_by_locale(self, version_id: str) -> dict[str, str]:
        raise NotImplementedError

    async def ensure_localization(self, version_id: str, locale: str) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} does not create localizations ({locale})."
        )

    def read_export_file(self, path: Path) -> tuple[str, ExportFile] | None:
        """``(set type, file)`` for one export file, ``None`` for a companion
        file the kind reads alongside another; raises :class:`FileRefused`."""
        raise NotImplementedError

    def read_planned_bytes(self, export_file: ExportFile) -> bytes:
        """The file's bytes, refused unless they still hash to the planned MD5."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Sets + assets
    # ------------------------------------------------------------------

    async def asset_set_ids(self, localization_id: str) -> dict[str, str]:
        return await asset_set_ids(
            self.client, self.kind, self.localization_type, localization_id
        )

    async def ensure_asset_set(self, localization_id: str, type_value: str) -> str:
        return await find_or_create_asset_set(
            self.client,
            self.kind,
            self.localization_type,
            localization_id,
            self.set_relationship,
            type_value,
        )

    async def list_set_assets(self, set_id: str) -> list[dict]:
        return [
            shape_asset(resource, self.kind)
            for resource in await list_set_resources(self.client, self.kind, set_id)
        ]

    async def upload_to_set(
        self,
        set_id: str,
        file_bytes: bytes,
        file_name: str,
        attributes: Mapping[str, str] | None = None,
    ) -> dict:
        return await upload_asset(
            self.client, self.kind, set_id, file_bytes, file_name, attributes
        )

    async def reorder_set(self, set_id: str, asset_ids: list[str]) -> None:
        await set_asset_order(self.client, self.kind, set_id, asset_ids)

    async def delete_asset(self, asset_id: str) -> None:
        await delete_asset(self.client, self.kind, asset_id)

    async def delete_set(self, set_id: str) -> None:
        """Delete a set (use after emptying it, so no orphan set is left)."""
        await delete_asset_set(self.client, self.kind, set_id)

    async def read_back_sets(self, localization_id: str) -> list[dict]:
        """The localization's sets in the ``{"id", "display_type",
        "screenshots"}`` shape the sync inventory folds, assets in set order."""
        return [
            {
                "id": set_id,
                "display_type": type_value,
                "screenshots": await self.list_set_assets(set_id),
            }
            for type_value, set_id in (
                await self.asset_set_ids(localization_id)
            ).items()
        ]

    # ------------------------------------------------------------------
    # Sync (spec 013, generalised over the source by spec 015 and over the
    # asset kind by spec 017)
    # ------------------------------------------------------------------

    async def plan_sync(self, steps: list[SyncStep]) -> set[str]:
        """Attach each step's current set and assets; return the set types the
        synced locales hold that the export does not touch."""
        set_ids_by_locale: dict[str, dict[str, str]] = {}
        for step in steps:
            if step.error or step.localization_id is None:
                continue
            if step.locale not in set_ids_by_locale:
                set_ids_by_locale[step.locale] = await self.asset_set_ids(
                    step.localization_id
                )
            step.set_id = set_ids_by_locale[step.locale].get(step.display_type)
            if step.set_id:
                step.existing = await self.list_set_assets(step.set_id)
        synced = {step.display_type for step in steps if not step.error}
        return {
            display_type
            for set_ids in set_ids_by_locale.values()
            for display_type in set_ids
            if display_type not in synced
        }

    async def apply_sync(
        self,
        steps: list[SyncStep],
        version_id: str,
        on_step: Callable[[SyncStep], Awaitable[None]] | None = None,
    ) -> None:
        """Apply every non-``skip`` step, calling ``on_step`` after each step,
        skipped or not. A localization created for one step is handed to its
        locale's other steps rather than looked up again."""
        for step in steps:
            if step.action != "skip":
                await self._apply_or_record(step, version_id)
                for sibling in steps:
                    if sibling.locale == step.locale and sibling.localization_id is None:
                        sibling.localization_id = step.localization_id
            if on_step is not None:
                await on_step(step)

    async def _apply_or_record(self, step: SyncStep, version_id: str) -> None:
        """Apply one step; on an ASC failure re-plan it from the live set and
        apply once more, then record a second failure on the step instead of
        aborting the sync. The re-plan also sweeps what the first attempt left
        half done, such as a reserved asset whose upload never committed."""
        try:
            await self.apply_sync_step(step, version_id)
            return
        except ASCAPIError as exc:
            logger.warning(
                "Sync row %s/%s failed (%s), re-planning it once",
                step.locale,
                step.display_type,
                exc,
            )
        step.set_id = None
        step.existing = []
        try:
            await self.plan_sync([step])
            if step.action != "skip":
                await self.apply_sync_step(step, version_id)
        except ASCAPIError as exc:
            logger.warning(
                "Sync row %s/%s failed again (%s), recorded on the row",
                step.locale,
                step.display_type,
                exc,
            )
            step.failed = str(exc)

    async def apply_sync_step(self, step: SyncStep, version_id: str) -> None:
        """Make one set exactly the step's files, in order, slot by slot.

        Each changed slot's old asset is deleted before its replacement is
        uploaded, so a full set never passes Apple's cap mid-replace. Each
        file is re-read and must still hash to the planned MD5, so what is
        uploaded is exactly what the plan checked, and nothing in the slot (or
        a new set, or a new localization) is written until it is.
        """
        set_id = step.set_id
        order: list[str] = []
        for index, export_file in enumerate(step.files):
            current = step.existing[index] if index < len(step.existing) else None
            if current is not None and _slot_matches(current, export_file):
                order.append(current["id"])
                continue
            file_bytes = await asyncio.to_thread(self.read_planned_bytes, export_file)
            if current is not None:
                await self.delete_asset(current["id"])
            set_id = set_id or await self._ensure_step_set(step, version_id)
            uploaded = await self.upload_to_set(
                set_id, file_bytes, export_file.path.name, export_file.attributes
            )
            if not uploaded.get("id"):
                raise _missing_id_error(
                    f"App Store Connect returned no {self.kind.noun} id "
                    f"for {step.locale}/{export_file.path.name}."
                )
            order.append(uploaded["id"])
        for extra in step.existing[len(step.files) :]:
            await self.delete_asset(extra["id"])
        await self.reorder_set(set_id, order)

    async def _ensure_step_set(self, step: SyncStep, version_id: str) -> str:
        if step.localization_id is None:
            step.localization_id = await self.ensure_localization(
                version_id, step.locale
            )
        return await self.ensure_asset_set(step.localization_id, step.display_type)


# ==================================================================
# Sync from an export directory (spec 013)
# ==================================================================

# The studio's Custom Product Page exports live beside the locales (spec 015).
SYNC_SKIPPED_DIRS = frozenset({"variants"})


class SyncPathError(Exception):
    """The export directory itself is refused; nothing was read or written."""


class ExportChangedError(Exception):
    """A planned file no longer holds the bytes the plan checked."""


class FileRefused(Exception):
    """One export file is refused; the message says why."""


@dataclass(frozen=True)
class ExportFile:
    """``attributes`` are extra reservation attributes sent with its upload."""

    path: Path
    md5: str
    size: int
    attributes: dict[str, str] = field(default_factory=dict, hash=False)


@dataclass
class SyncStep:
    """One locale x set type of a sync, or one ``error`` row.

    ``display_type`` is the set's type: a ``screenshotDisplayType`` or a
    ``previewType``.
    """

    locale: str
    display_type: str | None = None
    files: list[ExportFile] = field(default_factory=list)
    error: str | None = None
    localization_id: str | None = None
    set_id: str | None = None
    existing: list[dict] = field(default_factory=list)
    failed: str | None = None

    def _changed(self) -> list[int]:
        return [
            index
            for index, export_file in enumerate(self.files)
            if index >= len(self.existing)
            or not _slot_matches(self.existing[index], export_file)
        ]

    @property
    def processing(self) -> int:
        return sum(
            1
            for existing, export_file in zip(self.existing, self.files)
            if _slot_processing(existing, export_file)
        )

    @property
    def uploads(self) -> int:
        return len(self._changed())

    @property
    def deletes(self) -> int:
        replaced = sum(1 for index in self._changed() if index < len(self.existing))
        return replaced + max(len(self.existing) - len(self.files), 0)

    @property
    def action(self) -> SyncAction:
        if self.error:
            return "error"
        if self.localization_id is None:
            return "create_localization"
        if not self.existing:
            return "upload"
        if not self.uploads and not self.deletes:
            return "skip"
        return "replace"


def _slot_processing(existing: dict, export_file: ExportFile) -> bool:
    # Apple withholds sourceFileChecksum for a while after a commit, even once the
    # state reads COMPLETE, so the same name and byte size in the same slot is
    # taken as the file. The studio re-exports the same names after every edit:
    # the name alone is no proof. No state, a reservation never committed
    # (AWAITING_UPLOAD) or a FAILED asset is a change.
    return (
        existing.get("checksum") is None
        and existing.get("state") not in _NOT_PROCESSING_STATES
        and existing.get("file_name") == export_file.path.name
        and existing.get("file_size") == export_file.size
    )


def _slot_matches(existing: dict, export_file: ExportFile) -> bool:
    return (
        existing.get("state") != ASSET_STATE_FAILED
        and existing.get("checksum") == export_file.md5
    ) or _slot_processing(existing, export_file)


def _sync_roots() -> list[Path]:
    return [
        Path(root).expanduser().resolve() for root in settings.SCREENSHOT_SYNC_ROOTS
    ]


def within_sync_roots(path: Path) -> bool:
    real = path.resolve()
    return any(real.is_relative_to(root) for root in _sync_roots())


def resolve_sync_dir(directory: str) -> Path:
    """The export directory, refused unless its realpath is under an allowed root."""
    root = Path(directory)
    if not root.is_absolute():
        raise SyncPathError(f"dir must be an absolute path, got {directory!r}.")
    if not within_sync_roots(root):
        allowed = ", ".join(str(r) for r in _sync_roots()) or "none"
        raise SyncPathError(
            f"{directory} resolves outside SCREENSHOT_SYNC_ROOTS ({allowed})."
        )
    if not root.is_dir():
        raise SyncPathError(f"{directory} is not a directory.")
    return root.resolve()


def _scan_locale(
    service: LocalizationAssetService,
    locale_dir: Path,
    localization_id: str | None,
    types: list[str] | None,
) -> list[SyncStep]:
    kind = service.kind
    locale = locale_dir.name
    if not within_sync_roots(locale_dir):
        return [SyncStep(locale, error=f"{locale}/ resolves outside SCREENSHOT_SYNC_ROOTS.")]

    steps: list[SyncStep] = []
    by_type: dict[str, list[ExportFile]] = {}
    for path in sorted(locale_dir.iterdir()):
        if path.name.startswith("."):
            continue
        try:
            read = service.read_export_file(path)
        except FileRefused as exc:
            steps.append(SyncStep(locale, error=f"{locale}/{path.name}: {exc}."))
            continue
        if read is None:
            continue
        type_value, export_file = read
        if types and type_value not in types:
            continue
        by_type.setdefault(type_value, []).append(export_file)

    if len(by_type) > 1 and not types:
        listing = "; ".join(
            f"{type_value}: {', '.join(f.path.name for f in files)}"
            for type_value, files in sorted(by_type.items())
        )
        mixed = (
            f"{locale}/ mixes {kind.type_label}s ({listing}). Pass "
            f"{kind.types_param}, or export one directory per device family."
        )
        steps.append(SyncStep(locale, error=mixed))
        return steps

    for type_value, files in sorted(by_type.items()):
        if len(files) > kind.max_files:
            over_cap = (
                f"{locale}/ holds {len(files)} {type_value} files; "
                f"Apple's cap is {kind.max_files}."
            )
            steps.append(SyncStep(locale, type_value, error=over_cap))
        else:
            steps.append(
                SyncStep(locale, type_value, files, localization_id=localization_id)
            )
    if not steps:
        steps.append(SyncStep(locale, error=f"{locale}/ holds no {kind.noun}s."))
    return steps


@dataclass
class ExportScan:
    steps: list[SyncStep]
    locales: set[str]
    skipped: list[str]


@dataclass(frozen=True)
class SyncTarget:
    """Where a sync writes: a source's editable version and its localizations.

    ``creatable`` are locales the source may add a localization for during
    apply (a CPP takes any of the app's own locales). ``label`` names the
    locale owner in the error for a directory that is none of them.
    ``reference`` is per locale the set types the source must also hold
    (the main listing's families, for a CPP's screenshots); see
    :func:`missing_families`.
    """

    service: LocalizationAssetService
    version: EditableVersion
    localizations: dict[str, str]
    label: str
    creatable: frozenset[str] = frozenset()
    reference: dict[str, frozenset[str]] = field(default_factory=dict)


async def missing_families(
    target: SyncTarget, steps: list[SyncStep]
) -> dict[str, list[str]]:
    """Per locale, the set types ``target.reference`` holds that the page
    lacks once ``steps`` are done: what it holds now plus what the plan adds."""
    planned: dict[str, set[str]] = {}
    for step in steps:
        if not step.error and step.display_type:
            planned.setdefault(step.locale, set()).add(step.display_type)
    missing: dict[str, list[str]] = {}
    for locale, wanted in target.reference.items():
        localization_id = target.localizations.get(locale)
        if localization_id is None and locale not in planned:
            continue
        lacking = wanted - planned.get(locale, set())
        if lacking and localization_id is not None:
            lacking -= set(await target.service.asset_set_ids(localization_id))
        if lacking:
            missing[locale] = sorted(lacking)
    return missing


def scan_export_dir(
    root: Path,
    target: SyncTarget,
    *,
    locales: list[str] | None = None,
    types: list[str] | None = None,
) -> ExportScan:
    """Group ``<root>/<locale>/*`` into sync steps against the target's locales.

    A directory that is not one of the target's locales (nor creatable) is an
    error, never a skip: a silently skipped ``nl/`` once shipped a
    "successful" push of nothing. Dot-entries, ``variants/`` and top-level
    files are not locales.
    """
    steps: list[SyncStep] = []
    seen: set[str] = set()
    skipped: list[str] = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        if (
            name.startswith(".")
            or name in SYNC_SKIPPED_DIRS
            or not entry.is_dir()
            or (locales and name not in locales)
        ):
            skipped.append(name)
            continue
        seen.add(name)
        localization_id = target.localizations.get(name)
        if localization_id is None and name not in target.creatable:
            steps.append(
                SyncStep(
                    name,
                    error=(
                        f"{name}/ is not a locale on {target.label}. Locale "
                        "directories are App Store Connect codes (nl-NL, not nl) "
                        "it already has; add one with metadata_create_locale."
                    ),
                )
            )
            continue
        steps.extend(_scan_locale(target.service, entry, localization_id, types))

    for locale in locales or []:
        if locale not in seen:
            steps.append(SyncStep(locale, error=f"{locale}/ is not in {root}."))
    if not steps:
        raise SyncPathError(f"{root} holds no locale directories.")
    return ExportScan(steps=steps, locales=seen, skipped=skipped)
