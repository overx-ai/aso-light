"""Run the app-idea digest once (spec 021): uv run python -m scripts.idea_digest"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

from app.core.config import settings
from app.services.idea_digest import (
    AppleCharts,
    HttpNotifier,
    IdeaDeliveryError,
    IdeaDigest,
    IdeaDigestBusyError,
    IdeaStore,
    PacedSearch,
)
from app.services.keyword_intel.niche import NicheFinder
from app.services.keywords.itunes_search import ITunesSearchService

logging.basicConfig(level=logging.WARNING)


def main() -> int:
    if not settings.IDEA_DIGEST_NOTIFY_URL:
        print("IDEA_DIGEST_NOTIFY_URL is not set", file=sys.stderr)
        return 1
    digest = IdeaDigest(
        IdeaStore(Path(settings.IDEA_DIGEST_DIR)),
        NicheFinder(search=PacedSearch(ITunesSearchService())),
        AppleCharts(),
        HttpNotifier(settings.IDEA_DIGEST_NOTIFY_URL),
    )
    try:
        print(asyncio.run(digest.run(date.today())).message)
    except IdeaDeliveryError as exc:
        print(f"{exc}; kept for the next run", file=sys.stderr)
        return 1
    except IdeaDigestBusyError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
