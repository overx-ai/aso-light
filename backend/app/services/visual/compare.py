"""Visual old-vs-new screenshot comparison compositor.

Builds a two-row BEFORE/AFTER montage so a creative can be judged before
shipping:

* **BEFORE** (top row) — the live DEFAULT product page's screenshots.
* **AFTER** (bottom row) — a selected Custom Product Page's screenshots.

Both sides are resolved for a single ``locale`` + ``display_type`` (device
family) via :class:`app.services.asc.cpp.ASCCustomProductPageService`, their
CDN ``source_url`` assets are downloaded with plain ``httpx`` (Apple's
rendered image URLs need no auth), and Pillow lays them out on a white
canvas with per-row titles. The result is returned as PNG bytes for both
the MCP ``screenshots.compare`` tool (wrapped in a FastMCP ``Image``) and
the REST ``/screenshots/compare`` endpoint (served as ``image/png``).
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.services.asc.cpp import ASCCustomProductPageService

if TYPE_CHECKING:
    from app.services.asc.client import ASCClient

# Max concurrent CDN downloads per montage (both rows share the budget).
MAX_CONCURRENT_DOWNLOADS = 6

# Layout constants (pixels).
THUMB_WIDTH = 300
GAP = 16
MARGIN = 24
TITLE_HEIGHT = 36
CAPTION_HEIGHT = 22
BG_COLOR = (255, 255, 255)
TITLE_COLOR = (17, 17, 17)
CAPTION_COLOR = (102, 102, 102)
PLACEHOLDER_BG = (240, 240, 240)
PLACEHOLDER_FG = (170, 170, 170)
# Fallback aspect for placeholder cells / empty rows (iPhone 6.7" portrait).
DEFAULT_ASPECT = 2796 / 1290


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a TrueType font, falling back to Pillow's bitmap default.

    The bundled DejaVu font ships with Pillow; if it is unavailable we use
    the built-in default so compositing never hard-fails on font lookup.
    """
    for name in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _collect_screenshots(
    sets: list[dict], display_type: str
) -> list[dict]:
    """Flatten the screenshot sets for a single ``display_type``.

    ``get_cpp_screenshots`` / ``get_default_screenshots`` return one entry
    per set keyed by ``display_type``; we keep only the requested device
    family and concatenate its screenshots in order.
    """
    shots: list[dict] = []
    for set_obj in sets:
        if set_obj.get("display_type") != display_type:
            continue
        shots.extend(set_obj.get("screenshots", []))
    return shots


async def _download_image(
    http: httpx.AsyncClient, source_url: str | None
) -> Image.Image | None:
    """Download and decode one screenshot asset (returns ``None`` on failure).

    The CDN URL is unauthenticated, so a plain client suffices. Any
    network/decoding error is swallowed and rendered as a placeholder cell
    rather than failing the whole montage.
    """
    if not source_url:
        return None
    try:
        response = await http.get(source_url)
        if response.status_code >= 400:
            return None
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except (httpx.HTTPError, OSError):
        return None


def _scale_to_width(
    image: Image.Image, width: int
) -> Image.Image:
    """Scale an image to ``width`` preserving aspect ratio."""
    if image.width == width:
        return image
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.LANCZOS)


def _placeholder(width: int, height: int, label: str) -> Image.Image:
    """Render a grey placeholder cell for a missing/failed screenshot."""
    cell = Image.new("RGB", (width, height), PLACEHOLDER_BG)
    draw = ImageDraw.Draw(cell)
    font = _load_font(16)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        ((width - tw) / 2, (height - th) / 2),
        label,
        fill=PLACEHOLDER_FG,
        font=font,
    )
    return cell


async def build_comparison(
    asc_client: ASCClient,
    asc_app_id: str,
    cpp_id: str,
    locale: str,
    display_type: str,
) -> bytes:
    """Composite a BEFORE/AFTER screenshot montage as PNG bytes.

    Args:
        asc_client: An authenticated :class:`ASCClient`.
        asc_app_id: The App Store Connect numeric app id.
        cpp_id: The Custom Product Page id whose screenshots are the
            "after" (new) side.
        locale: The App Store locale (e.g. ``en-US``) to compare.
        display_type: Apple's ``screenshotDisplayType`` (e.g.
            ``APP_IPHONE_67``) selecting the device family.

    Returns:
        PNG bytes of the two-row montage. Mismatched screenshot counts are
        aligned by index; missing cells render as grey placeholders.
    """
    service = ASCCustomProductPageService(asc_client)

    # Resolve the DEFAULT (live) page screenshots for the locale.
    default_shots: list[dict] = []
    default_loc_id = await service.get_default_version_localization_id(
        asc_app_id, locale
    )
    if default_loc_id:
        default_sets = await service.get_default_screenshots(default_loc_id)
        default_shots = _collect_screenshots(default_sets, display_type)

    # Resolve the CPP page screenshots for the locale.
    cpp_shots: list[dict] = []
    cpp_loc_id = await service.get_cpp_localization_id(cpp_id, locale)
    if cpp_loc_id:
        cpp_sets = await service.get_cpp_screenshots(cpp_loc_id)
        cpp_shots = _collect_screenshots(cpp_sets, display_type)

    cpp = await service.get_cpp(cpp_id)
    cpp_name = cpp.get("attributes", {}).get("name") or cpp_id

    # Download every asset with bounded concurrency (both rows at once).
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:

        async def fetch(shot: dict) -> Image.Image | None:
            async with semaphore:
                return await _download_image(http, shot.get("source_url"))

        default_images, cpp_images = await asyncio.gather(
            asyncio.gather(*(fetch(shot) for shot in default_shots)),
            asyncio.gather(*(fetch(shot) for shot in cpp_shots)),
        )

    # Compositing is CPU-bound (Pillow resize/paste/encode); offload it so the
    # montage build never blocks the event loop.
    return await asyncio.to_thread(
        _render_montage,
        before_images=list(default_images),
        after_images=list(cpp_images),
        before_title="BEFORE — default page",
        after_title=f"AFTER — {cpp_name}",
    )


def _render_montage(
    before_images: list[Image.Image | None],
    after_images: list[Image.Image | None],
    before_title: str,
    after_title: str,
) -> bytes:
    """Lay out the two labeled rows on a white canvas and encode to PNG.

    Each cell is scaled to ``THUMB_WIDTH``; rows are aligned by index so
    mismatched counts pad with placeholders. Row height is the tallest
    scaled image in either row (so both rows share a baseline).
    """
    columns = max(len(before_images), len(after_images), 1)

    scaled_before = [
        _scale_to_width(img, THUMB_WIDTH) if img is not None else None
        for img in before_images
    ]
    scaled_after = [
        _scale_to_width(img, THUMB_WIDTH) if img is not None else None
        for img in after_images
    ]

    real_heights = [
        img.height
        for img in (*scaled_before, *scaled_after)
        if img is not None
    ]
    cell_height = (
        max(real_heights)
        if real_heights
        else round(THUMB_WIDTH * DEFAULT_ASPECT)
    )

    row_content_height = TITLE_HEIGHT + cell_height + CAPTION_HEIGHT
    canvas_width = MARGIN * 2 + columns * THUMB_WIDTH + (columns - 1) * GAP
    canvas_height = MARGIN * 2 + row_content_height * 2 + GAP

    canvas = Image.new("RGB", (canvas_width, canvas_height), BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(22)
    caption_font = _load_font(15)

    def _draw_row(images: list[Image.Image | None], title: str, top: int) -> None:
        draw.text((MARGIN, top), title, fill=TITLE_COLOR, font=title_font)
        cell_top = top + TITLE_HEIGHT
        for index in range(columns):
            left = MARGIN + index * (THUMB_WIDTH + GAP)
            image = images[index] if index < len(images) else None
            if image is None:
                cell = _placeholder(THUMB_WIDTH, cell_height, "—")
                canvas.paste(cell, (left, cell_top))
            else:
                # Top-align within the shared cell height.
                canvas.paste(image, (left, cell_top))
            caption = f"#{index + 1}"
            draw.text(
                (left, cell_top + cell_height + 4),
                caption,
                fill=CAPTION_COLOR,
                font=caption_font,
            )

    _draw_row(scaled_before, before_title, MARGIN)
    _draw_row(
        scaled_after, after_title, MARGIN + row_content_height + GAP
    )

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()
