"""Listing optimizer (spec 019): brainstorm, market gaps, proposals, localize, export.

Everything here is suggestion-only. Localize returns ``metadata_bulk_preview``
payloads; the only path that writes to ASC stays the existing bulk apply.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.stopwords import language_of, stopwords_for_locales
from app.models.app import App
from app.models.keyword import KeywordRanking, KeywordTracking
from app.models.keyword_intel import KeywordIntelCache
from app.models.listing_draft import ListingDraft
from app.models.metadata import AppMetadataLocalization, AppMetadataState
from app.models.territory import Territory
from app.schemas.keyword_intel import FreeTermScore, VariantPickOut
from app.schemas.listing import (
    DEFAULT_LISTING_STOREFRONT,
    BrainstormOut,
    BrainstormTerm,
    ExportLocale,
    ExportOut,
    ExportSource,
    GapCell,
    GapsOut,
    ListingBulkPreview,
    ListingProposal,
    LocalizeOut,
    ProposalsOut,
    RankingsRefreshOut,
    ScoredProposal,
)
from app.services.keyword_intel.base import KeywordIntel, upsert_intel
from app.services.keyword_intel.free import (
    MAX_FREE_TERMS,
    MAX_PACK_LOCALES,
    MAX_PACK_TERMS,
    SEED_EXPANSION_TOP,
    FreeKeywordIntel,
    asa_popularity_by_term,
    brand_token,
    listing_terms_from_lookup,
    own_listing_terms,
    storefront_for_locale,
)
from app.services.keyword_intel.packer import PackCandidate
from app.services.keywords.concurrency import gather_bounded
from app.services.keywords.cross_localization import get_indexed_locales_for_territory
from app.services.keywords.tracker import REFRESH_CONCURRENCY, KeywordRankingTracker
from app.services.listing.errors import (
    InvalidProposalError,
    ListingNotBrainstormedError,
    ListingQuotaError,
    ListingUpstreamError,
    NoProposalDraftError,
    TranslatorNotConfiguredError,
    UnknownLocaleError,
    UnknownStorefrontError,
)
from app.services.listing.proposals import (
    PROPOSAL_FIELDS,
    covered_popularity,
    is_valid_proposal,
    keyword_tokens,
    parse_proposals,
)
from app.services.metadata.coloring import classify_keyword
from app.services.metadata.translate import (
    PROPOSALS_FIELD_KIND,
    AbstractTranslator,
    TranslationOverflowError,
    TranslationQuotaExceededError,
    TranslatorUnavailableError,
    keyword_variants,
    translate_with_cache,
)
from app.services.metadata.validation import validate_field

logger = logging.getLogger(__name__)

BRAINSTORM_SOURCE = "listing_brainstorm"
VARIANT_SOURCE = "listing_variant"
STATUS_USING = "using"
STATUS_MISSING = "missing"
STATUS_CANDIDATE = "candidate"

MISSING_MIN_VALUE = 10.0
# Seeds and candidates share 014's per-call term budget (hint calls dominate).
BRAINSTORM_MAX_SEEDS = 10
BRAINSTORM_MAX_CANDIDATES = MAX_FREE_TERMS - BRAINSTORM_MAX_SEEDS
BRAINSTORM_VARIANT_TERMS = 5
MAX_PROPOSAL_CANDIDATES = 30
NAME_FIELDS: tuple[str, ...] = ("name", "subtitle")
KEYWORDS_FIELD = "keywords"


def _term_out(score: FreeTermScore) -> BrainstormTerm:
    value = PackCandidate(score.term, score.popularity, score.difficulty).value
    return BrainstormTerm(
        term=score.term,
        popularity=score.popularity,
        difficulty=score.difficulty,
        value=round(value, 2),
        popularity_source=score.popularity_source,
    )


def _row_out(row: KeywordIntelCache) -> BrainstormTerm:
    return BrainstormTerm(
        term=row.keyword,
        popularity=row.volume_score or 0,
        difficulty=row.difficulty_score or 0,
        value=float((row.extra or {}).get("value", 0.0)),
        popularity_source=row.popularity_source,
    )


def _variant_out(row: KeywordIntelCache) -> VariantPickOut:
    extra = row.extra or {}
    return VariantPickOut(
        source_term=row.keyword,
        locale=row.locale,
        best=extra.get("best", row.keyword),
        score=row.volume_score or 0,
        alternatives=extra.get("alternatives", []),
        not_searched_here=bool(extra.get("not_searched_here")),
    )


def _proposal_hash(proposal: ListingProposal, storefront: str) -> str:
    canonical = json.dumps([storefront, proposal.model_dump()], sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ListingOptimizerService:
    def __init__(
        self,
        session: AsyncSession,
        app: App,
        *,
        translator: AbstractTranslator | None,
        intel: FreeKeywordIntel | None = None,
    ) -> None:
        self.session = session
        self.app = app
        self.translator = translator
        self.intel = intel or FreeKeywordIntel()
        self._snapshot_cache: dict[str, dict[str, AppMetadataLocalization]] | None = None

    # ---- snapshot helpers ---------------------------------------------

    async def _snapshot(self) -> dict[str, dict[str, AppMetadataLocalization]]:
        if self._snapshot_cache is None:
            rows = (await self.session.execute(
                select(AppMetadataLocalization).where(AppMetadataLocalization.app_id == self.app.id)
            )).scalars().all()
            snapshot: dict[str, dict[str, AppMetadataLocalization]] = {}
            for row in rows:
                snapshot.setdefault(row.locale, {})[row.kind] = row
            self._snapshot_cache = snapshot
        return self._snapshot_cache

    async def live_locales(self) -> list[str]:
        return sorted(await self._snapshot())

    async def _field(self, locale: str, field: str) -> str | None:
        kind = "app_info" if field in NAME_FIELDS else "version"
        row = (await self._snapshot()).get(locale, {}).get(kind)
        return getattr(row, field, None) if row else None

    async def _current(self, locale: str) -> ListingProposal:
        return ListingProposal(**{
            f: (await self._field(locale, f)) or "" for f in PROPOSAL_FIELDS
        })

    async def _source_locale(self, storefront: str) -> str:
        indexed = get_indexed_locales_for_territory(storefront)
        if not indexed:
            raise UnknownStorefrontError(f"No indexed locales are known for storefront {storefront}")
        live = set(await self.live_locales())
        return next((loc for loc in indexed if loc in live), indexed[0])

    async def _editable_fields(self) -> set[str]:
        fields = (await self.session.execute(
            select(AppMetadataState.editable_fields_json).where(AppMetadataState.app_id == self.app.id)
        )).scalar_one_or_none()
        return set(fields or [])

    async def _variants_for(self, term: str, source_locale: str, locale: str) -> list[str]:
        return await keyword_variants(
            self.translator, self.session, self.app.id, term, source_locale, locale,
        )

    # ---- brainstorm ---------------------------------------------------

    async def brainstorm(
        self,
        storefront: str = DEFAULT_LISTING_STOREFRONT,
        competitor_track_id: str | None = None,
    ) -> BrainstormOut:
        """``using`` = the listing's own terms; ``missing`` = scored candidates not in it.

        A competitor (``competitor_track_id``) goes through the same path via the
        iTunes lookup source, and nothing is stored for it.
        """
        storefront = storefront.upper()
        source_locale = await self._source_locale(storefront)
        brand = brand_token(self.app.name)
        if competitor_track_id:
            stopwords = stopwords_for_locales([source_locale])
            using = await listing_terms_from_lookup(
                competitor_track_id, storefront, stopwords=stopwords, brand=None,
            )
            seeds = await listing_terms_from_lookup(
                competitor_track_id, storefront, stopwords=stopwords, brand=None,
                include_description=False,
            )
        else:
            using = await own_listing_terms(self.session, self.app, source_locale)
            seeds = await own_listing_terms(
                self.session, self.app, source_locale, include_description=False,
            )

        asa = await asa_popularity_by_term(self.session, self.app.id, storefront)
        seed_list = sorted(seeds, key=lambda t: (-len(t.split()), t))[:BRAINSTORM_MAX_SEEDS]
        seed_scores = await self.intel.score_terms(seed_list, storefront, asa_popularity=asa, brand=brand)
        validated = sorted(
            (s for s in seed_scores.scores if s.popularity > 0), key=lambda s: -s.popularity,
        )
        expansion = await self.intel.seed_expansion(
            [s.term for s in validated[:SEED_EXPANSION_TOP]], storefront, exclude=using,
        )
        pool = [
            term for term in dict.fromkeys([*expansion, *(c.term for c in seed_scores.candidates)])
            if term not in using
        ][:BRAINSTORM_MAX_CANDIDATES]
        candidate_scores = await self.intel.score_terms(pool, storefront, asa_popularity=asa, brand=brand)

        using_terms = sorted((_term_out(s) for s in seed_scores.scores), key=lambda t: -t.value)
        scored = sorted((_term_out(s) for s in candidate_scores.scores), key=lambda t: -t.value)
        missing = [t for t in scored if t.value > MISSING_MIN_VALUE and t.term not in using]

        variants: list[VariantPickOut] = []
        if not competitor_track_id:
            tracked = await self._tracked_texts()
            variant_terms = list(dict.fromkeys([*tracked, *(t.term for t in missing)]))
            variants = await self._pick_variants(
                variant_terms[:BRAINSTORM_VARIANT_TERMS], source_locale,
            )
            await self._store_brainstorm(storefront, using_terms, scored, {t.term for t in missing})
            await self.store_variants(variants)

        return BrainstormOut(
            storefront=storefront,
            source_locale=source_locale,
            competitor_track_id=competitor_track_id,
            using=using_terms,
            missing=missing,
            variants=variants,
        )

    async def _pick_variants(self, terms: Sequence[str], source_locale: str) -> list[VariantPickOut]:
        locales = [
            loc for loc in await self.live_locales()
            if language_of(loc) != language_of(source_locale)
        ][:MAX_PACK_LOCALES]
        picks: list[VariantPickOut] = []
        for locale in locales:
            # Sequential: variant translation shares this session (translation cache).
            options = [await self._variants_for(t, source_locale, locale) or [t] for t in terms]

            async def pick(term_variants: list[str], locale: str = locale):
                return await self.intel.pick_best_variant(
                    term_variants, locale, storefront_for_locale(locale),
                )

            results = await gather_bounded(options, pick, concurrency=REFRESH_CONCURRENCY)
            picks.extend(
                VariantPickOut(
                    source_term=term, locale=locale, best=p.best, score=p.score,
                    alternatives=p.alternatives, not_searched_here=p.not_searched_here,
                )
                for term, p in zip(terms, results)
            )
        return picks

    async def _tracked_texts(self) -> list[str]:
        trackings = (await self.session.execute(
            select(KeywordTracking)
            .options(selectinload(KeywordTracking.keyword))
            .where(KeywordTracking.app_id == self.app.id)
        )).scalars().all()
        seen: dict[str, str] = {}
        for tracking in trackings:
            seen.setdefault(tracking.keyword.text.casefold(), tracking.keyword.text)
        return list(seen.values())

    async def _store_brainstorm(
        self,
        storefront: str,
        using: list[BrainstormTerm],
        scored: list[BrainstormTerm],
        missing: set[str],
    ) -> None:
        await self.session.execute(delete(KeywordIntelCache).where(
            KeywordIntelCache.app_id == self.app.id,
            KeywordIntelCache.source == BRAINSTORM_SOURCE,
            KeywordIntelCache.locale == storefront,
        ))

        def row(term: BrainstormTerm, status: str) -> KeywordIntel:
            return KeywordIntel(
                keyword=term.term, locale=storefront, source=BRAINSTORM_SOURCE,
                volume_score=term.popularity, difficulty_score=term.difficulty,
                popularity_source=term.popularity_source,
                extra={"status": status, "value": term.value},
            )

        rows = [row(t, STATUS_USING) for t in using]
        rows += [row(t, STATUS_MISSING if t.term in missing else STATUS_CANDIDATE) for t in scored]
        await upsert_intel(self.session, self.app.id, rows)
        await self.session.flush()

    async def store_variants(self, picks: Sequence[VariantPickOut]) -> None:
        await upsert_intel(self.session, self.app.id, [
            KeywordIntel(
                keyword=p.source_term, locale=p.locale, source=VARIANT_SOURCE,
                volume_score=p.score,
                extra={
                    "best": p.best,
                    "alternatives": p.alternatives,
                    "not_searched_here": p.not_searched_here,
                },
            )
            for p in picks
        ])
        await self.session.flush()

    async def _brainstorm_rows(self, storefront: str) -> list[KeywordIntelCache]:
        return list((await self.session.execute(
            select(KeywordIntelCache).where(
                KeywordIntelCache.app_id == self.app.id,
                KeywordIntelCache.source == BRAINSTORM_SOURCE,
                KeywordIntelCache.locale == storefront,
            )
        )).scalars().all())

    async def _variant_rows(self) -> list[KeywordIntelCache]:
        return list((await self.session.execute(
            select(KeywordIntelCache).where(
                KeywordIntelCache.app_id == self.app.id,
                KeywordIntelCache.source == VARIANT_SOURCE,
            )
        )).scalars().all())

    async def stored_brainstorm(self, storefront: str = DEFAULT_LISTING_STOREFRONT) -> BrainstormOut | None:
        storefront = storefront.upper()
        rows = await self._brainstorm_rows(storefront)
        if not rows:
            return None
        by_status: dict[str, list[BrainstormTerm]] = {}
        for row in rows:
            by_status.setdefault((row.extra or {}).get("status", ""), []).append(_row_out(row))
        return BrainstormOut(
            storefront=storefront,
            source_locale=await self._source_locale(storefront),
            using=sorted(by_status.get(STATUS_USING, []), key=lambda t: -t.value),
            missing=sorted(by_status.get(STATUS_MISSING, []), key=lambda t: -t.value),
            variants=[_variant_out(r) for r in await self._variant_rows()],
        )

    # ---- market gaps --------------------------------------------------

    async def live_storefronts(self) -> list[str]:
        return list(dict.fromkeys(storefront_for_locale(loc) for loc in await self.live_locales()))

    async def refresh_rankings(self) -> RankingsRefreshOut:
        territories = await self.live_storefronts()
        recorded = await KeywordRankingTracker(self.session).refresh_rankings(
            self.app.id, territories or None,
        )
        return RankingsRefreshOut(territories=territories, recorded=recorded)

    async def _latest_ranks(self) -> dict[tuple[str, str], int | None]:
        """(keyword casefold, storefront) -> best latest rank across the keyword's trackings."""
        rows = (await self.session.execute(
            select(KeywordRanking, Territory.code, KeywordTracking)
            .join(Territory, Territory.id == KeywordRanking.territory_id)
            .join(KeywordTracking, KeywordTracking.id == KeywordRanking.tracking_id)
            .options(selectinload(KeywordTracking.keyword))
            .where(KeywordTracking.app_id == self.app.id)
            .order_by(KeywordRanking.recorded_at.desc(), KeywordRanking.id.desc())
        )).all()
        latest: dict[tuple[int, str], int | None] = {}
        text_of: dict[int, str] = {}
        for ranking, code, tracking in rows:
            latest.setdefault((tracking.id, code), ranking.rank)
            text_of[tracking.id] = tracking.keyword.text.casefold()

        ranks: dict[tuple[str, str], int | None] = {}
        for (tracking_id, code), rank in latest.items():
            key = (text_of[tracking_id], code)
            known = ranks.get(key)
            if key not in ranks or (rank is not None and (known is None or rank < known)):
                ranks[key] = rank
        return ranks

    async def gaps(self) -> GapsOut:
        locales = await self.live_locales()
        keywords = await self._tracked_texts()
        ranks = await self._latest_ranks()
        variants = {
            (row.keyword.casefold(), row.locale): (row.extra or {}).get("best")
            for row in await self._variant_rows()
        }
        cells: list[GapCell] = []
        for keyword in keywords:
            for locale in locales:
                storefront = storefront_for_locale(locale)
                rank = ranks.get((keyword.casefold(), storefront))
                variant = variants.get((keyword.casefold(), locale))
                cells.append(GapCell(
                    keyword=keyword,
                    locale=locale,
                    storefront=storefront,
                    rank=rank,
                    variant=variant,
                    placement=classify_keyword(
                        keyword,
                        await self._field(locale, "name"),
                        await self._field(locale, "subtitle"),
                        await self._field(locale, KEYWORDS_FIELD),
                        variant=variant,
                        source_rank=rank,
                    ),
                ))
        return GapsOut(locales=locales, keywords=keywords, cells=cells)

    # ---- proposals ----------------------------------------------------

    async def _translate(self, text: str, source_locale: str, target_locale: str, field_kind: str) -> str:
        if self.translator is None:
            raise TranslatorNotConfiguredError(
                "AI translation not configured. Set OPENROUTER_API_KEY or ANTHROPIC_API_KEY."
            )
        try:
            translated, _ = await translate_with_cache(
                self.translator, self.session, self.app.id, text,
                source_locale, target_locale, field_kind,  # type: ignore[arg-type]
                brand_allowlist=[b for b in [brand_token(self.app.name)] if b],
            )
        except TranslationQuotaExceededError as exc:
            raise ListingQuotaError(str(exc)) from exc
        except (TranslatorUnavailableError, TranslationOverflowError) as exc:
            raise ListingUpstreamError("All translation providers failed. Try again later.") from exc
        return translated

    async def propose(self, storefront: str = DEFAULT_LISTING_STOREFRONT) -> ProposalsOut:
        storefront = storefront.upper()
        source_locale = await self._source_locale(storefront)
        rows = await self._brainstorm_rows(storefront)
        if not rows:
            raise ListingNotBrainstormedError(
                f"No brainstorm for storefront {storefront} yet; run the brainstorm first."
            )
        ranked = sorted(rows, key=lambda r: -float((r.extra or {}).get("value", 0.0)))
        candidates = ranked[:MAX_PROPOSAL_CANDIDATES]
        current = await self._current(source_locale)
        payload = json.dumps({
            "current": current.model_dump(),
            "candidates": [
                {"term": r.keyword, "popularity": r.volume_score, "difficulty": r.difficulty_score}
                for r in candidates
            ],
        }, ensure_ascii=False, sort_keys=True)
        text = await self._translate(payload, source_locale, source_locale, PROPOSALS_FIELD_KIND)

        popularity = {r.keyword: r.volume_score or 0 for r in rows}
        baseline = covered_popularity(current, popularity)

        def scored(proposal: ListingProposal) -> ScoredProposal:
            covered = covered_popularity(proposal, popularity)
            return ScoredProposal(
                **proposal.model_dump(), covered_popularity=covered,
                delta_vs_current=covered - baseline,
            )

        return ProposalsOut(
            storefront=storefront,
            source_locale=source_locale,
            current=scored(current),
            proposals=[scored(p) for p in parse_proposals(text)],
        )

    # ---- localize -----------------------------------------------------

    async def localize(
        self,
        proposal: ListingProposal,
        storefront: str = DEFAULT_LISTING_STOREFRONT,
        locales: Sequence[str] | None = None,
    ) -> LocalizeOut:
        """One proposal -> every live locale, ``MAX_PACK_LOCALES`` per call.

        Fields outside ``editable_fields`` are never produced; the rest of the
        live locales come back as ``remaining_locales`` for the next call.
        """
        if not is_valid_proposal(proposal):
            raise InvalidProposalError(
                "Proposal exceeds a char limit or repeats a word across name, subtitle and keywords"
            )
        storefront = storefront.upper()
        source_locale = await self._source_locale(storefront)
        live = await self.live_locales()
        unknown = [loc for loc in locales or [] if loc not in live]
        if unknown:
            raise UnknownLocaleError(f"Locales {unknown} have no metadata for this app")

        editable = await self._editable_fields()
        wanted = [f for f in PROPOSAL_FIELDS if f in editable]
        out = LocalizeOut(
            storefront=storefront,
            source_locale=source_locale,
            locked_fields=[f for f in PROPOSAL_FIELDS if f not in editable],
        )
        targets = list(dict.fromkeys(locales or live))
        others = [loc for loc in targets if loc != source_locale]
        batch = others[:MAX_PACK_LOCALES]
        out.remaining_locales = others[MAX_PACK_LOCALES:]
        if not wanted:
            return out

        values: dict[str, dict[str, str]] = {f: {} for f in wanted}
        if source_locale in targets:
            for f in wanted:
                values[f][source_locale] = getattr(proposal, f)

        for locale in batch:
            for f in (f for f in NAME_FIELDS if f in wanted):
                if language_of(locale) == language_of(source_locale):
                    values[f][locale] = getattr(proposal, f)
                elif self.translator is None:
                    out.warnings.append(f"{locale} {f}: translator not configured")
                else:
                    values[f][locale] = await self._translate(
                        getattr(proposal, f), source_locale, locale, f,
                    )

        if KEYWORDS_FIELD in wanted and batch:
            await self._localize_keywords(proposal, source_locale, batch, values, out)

        out.localized_locales = [loc for loc in targets if loc == source_locale or loc in batch]
        for f in wanted:
            fitting = {loc: v for loc, v in values[f].items() if v and validate_field(f, v)[0]}
            if fitting:
                out.bulk_previews.append(ListingBulkPreview(
                    field=f, target_locales=list(fitting), values_by_locale=fitting,
                ))
        await self._save_draft(proposal, storefront, out.bulk_previews)
        return out

    async def _localize_keywords(
        self,
        proposal: ListingProposal,
        source_locale: str,
        batch: Sequence[str],
        values: dict[str, dict[str, str]],
        out: LocalizeOut,
    ) -> None:
        terms = keyword_tokens(proposal.keywords)[:MAX_PACK_TERMS]
        groups: dict[str, list[str]] = {}
        for locale in batch:
            groups.setdefault(storefront_for_locale(locale), []).append(locale)

        async def variants_for(term: str, locale: str) -> list[str]:
            return await self._variants_for(term, source_locale, locale)

        for storefront, group in groups.items():
            names: dict[str, str | None] = {}
            subtitles: dict[str, str | None] = {}
            for locale in get_indexed_locales_for_territory(storefront) or group:
                planned = values.get("name", {}).get(locale)
                names[locale] = planned or await self._field(locale, "name")
                subtitles[locale] = values.get("subtitle", {}).get(locale) or await self._field(
                    locale, "subtitle",
                )
                if locale not in group:
                    # Words already in another indexed locale's keyword field are spent.
                    names[f"{locale}#{KEYWORDS_FIELD}"] = (
                        values[KEYWORDS_FIELD].get(locale) or await self._field(locale, KEYWORDS_FIELD)
                    )
            pack = await self.intel.build_pack(
                terms=terms,
                storefront=storefront,
                source_locale=source_locale,
                locales=group,
                variants_for=variants_for,
                names_by_locale=names,
                subtitles_by_locale=subtitles,
            )
            out.picks.extend(pack.picks)
            values[KEYWORDS_FIELD].update(pack.values_by_locale)

    async def _save_draft(
        self, proposal: ListingProposal, storefront: str, previews: Sequence[ListingBulkPreview],
    ) -> None:
        proposal_hash = _proposal_hash(proposal, storefront)
        draft = (await self.session.execute(
            select(ListingDraft).where(ListingDraft.app_id == self.app.id)
        )).scalar_one_or_none()
        merged: dict[str, dict[str, str]] = {}
        if draft is not None and draft.proposal_hash == proposal_hash:
            merged = {loc: dict(fields) for loc, fields in draft.values_json.items()}
        for preview in previews:
            for locale, value in preview.values_by_locale.items():
                merged.setdefault(locale, {})[preview.field] = value
        if draft is None:
            self.session.add(ListingDraft(
                app_id=self.app.id, proposal_hash=proposal_hash, values_json=merged,
            ))
        else:
            draft.proposal_hash = proposal_hash
            draft.values_json = merged
        await self.session.flush()

    # ---- export -------------------------------------------------------

    async def export(self, source: ExportSource) -> ExportOut:
        snapshot = await self._snapshot()
        locales: dict[str, ExportLocale] = {}
        for locale, rows in sorted(snapshot.items()):
            info, version = rows.get("app_info"), rows.get("version")
            locales[locale] = ExportLocale(
                name=info.name if info else None,
                subtitle=info.subtitle if info else None,
                keywords=version.keywords if version else None,
                promotional_text=version.promotional_text if version else None,
                description=version.description if version else None,
            )
        if source == "proposal":
            draft = (await self.session.execute(
                select(ListingDraft).where(ListingDraft.app_id == self.app.id)
            )).scalar_one_or_none()
            if draft is None:
                raise NoProposalDraftError("No localized proposal yet; localize a proposal first.")
            for locale, fields in draft.values_json.items():
                current = locales.get(locale, ExportLocale())
                locales[locale] = current.model_copy(update=fields)
        return ExportOut(app_id=self.app.id, source=source, locales=locales)
