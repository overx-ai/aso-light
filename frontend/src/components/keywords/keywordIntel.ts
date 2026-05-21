import type { SovOut, VisibilityWatchOut } from "@/types";

const STALE_AFTER_MS = 1000 * 60 * 60 * 24 * 7;

type KeywordIntelColor = "blue" | "gray" | "green" | "orange" | "yellow";

export interface KeywordIntelSummary {
  label: string;
  detail: string;
  color: KeywordIntelColor;
}

interface MatchStats {
  matchedCount: number;
  freshCount: number;
  unpolledCount: number;
  latestPresenceCount: number;
  latestPolledAt: string | null;
  averageSov: number;
}

interface KeywordSummaryArgs {
  keywordText: string;
  trackId: string;
  watches: VisibilityWatchOut[];
  sovItems: SovOut[];
}

interface TrackSummaryArgs {
  country: string;
  trackId: string;
  watches: VisibilityWatchOut[];
  sovItems: SovOut[];
}

function normalizeKeyword(text: string): string {
  return text.trim().toLowerCase();
}

function formatPercent(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded)
    ? rounded.toFixed(0)
    : rounded.toFixed(1);
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "unknown";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function pluralize(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : (plural ?? `${singular}s`)}`;
}

function isFresh(lastPolledAt: string | null): boolean {
  if (!lastPolledAt) return false;
  const parsed = Date.parse(lastPolledAt);
  if (Number.isNaN(parsed)) return false;
  return Date.now() - parsed <= STALE_AFTER_MS;
}

function buildSovMap(sovItems: SovOut[]): Map<number, SovOut> {
  return new Map(sovItems.map((item) => [item.watch_id, item]));
}

function buildStats(
  matchedWatches: VisibilityWatchOut[],
  trackId: string,
  sovMap: Map<number, SovOut>,
): MatchStats {
  let freshCount = 0;
  let unpolledCount = 0;
  let latestPresenceCount = 0;
  let latestPolledAt: string | null = null;
  let sovTotal = 0;

  for (const watch of matchedWatches) {
    if (watch.last_polled_at == null) {
      unpolledCount += 1;
      continue;
    }

    const lastPollMs = Date.parse(watch.last_polled_at);
    if (
      !Number.isNaN(lastPollMs) &&
      (latestPolledAt == null ||
        lastPollMs > Date.parse(latestPolledAt))
    ) {
      latestPolledAt = watch.last_polled_at;
    }

    if (!isFresh(watch.last_polled_at)) {
      continue;
    }

    freshCount += 1;

    const ownSov =
      sovMap
        .get(watch.id)
        ?.entries.find((entry) => entry.track_id === trackId)?.sov_pct ?? 0;
    sovTotal += ownSov;

    if (
      watch.latest_snapshot?.results.some((result) => result.track_id === trackId)
    ) {
      latestPresenceCount += 1;
    }
  }

  return {
    matchedCount: matchedWatches.length,
    freshCount,
    unpolledCount,
    latestPresenceCount,
    latestPolledAt,
    averageSov: freshCount > 0 ? sovTotal / freshCount : 0,
  };
}

export function summarizeKeywordIntelForKeyword({
  keywordText,
  trackId,
  watches,
  sovItems,
}: KeywordSummaryArgs): KeywordIntelSummary {
  const matchedWatches = watches.filter(
    (watch) => normalizeKeyword(watch.text) === normalizeKeyword(keywordText),
  );
  const stats = buildStats(matchedWatches, trackId, buildSovMap(sovItems));

  if (stats.matchedCount === 0) {
    return {
      label: "No intel",
      detail: `No keyword-visibility watch exists for "${keywordText}" yet.`,
      color: "gray",
    };
  }

  if (stats.freshCount === 0 && stats.unpolledCount === stats.matchedCount) {
    return {
      label: "Awaiting poll",
      detail: `Watching "${keywordText}" in ${pluralize(stats.matchedCount, "country")}, but none have a snapshot yet.`,
      color: "gray",
    };
  }

  if (stats.freshCount === 0) {
    return {
      label: "Stale",
      detail: `All cached intel for "${keywordText}" is stale. Last poll: ${formatDateTime(stats.latestPolledAt)}.`,
      color: "yellow",
    };
  }

  if (stats.averageSov > 0) {
    return {
      label: `SOV ${formatPercent(stats.averageSov)}%`,
      detail: `Average top-3 share across ${pluralize(stats.freshCount, "fresh watched country", "fresh watched countries")} for "${keywordText}". Present in ${stats.latestPresenceCount}/${stats.freshCount} latest snapshots.`,
      color: "green",
    };
  }

  if (stats.latestPresenceCount > 0) {
    return {
      label: "No top 3",
      detail: `Fresh snapshots exist for "${keywordText}", but the app is not in the top 3 in any watched country.`,
      color: "blue",
    };
  }

  return {
    label: "Out of top 20",
    detail: `Fresh snapshots exist for "${keywordText}", but the app is missing from the latest top 20 in every watched country.`,
    color: "orange",
  };
}

export function summarizeKeywordIntelForTrack({
  country,
  trackId,
  watches,
  sovItems,
}: TrackSummaryArgs): KeywordIntelSummary {
  const normalizedCountry = country.trim().toLowerCase();
  const matchedWatches = watches.filter(
    (watch) => watch.country === normalizedCountry,
  );
  const stats = buildStats(matchedWatches, trackId, buildSovMap(sovItems));
  const countryLabel = normalizedCountry.toUpperCase();

  if (stats.matchedCount === 0) {
    return {
      label: "No intel",
      detail: `No keyword-visibility watches exist for ${countryLabel} yet.`,
      color: "gray",
    };
  }

  if (stats.freshCount === 0 && stats.unpolledCount === stats.matchedCount) {
    return {
      label: "Awaiting poll",
      detail: `${countryLabel} watches exist, but none have a snapshot yet.`,
      color: "gray",
    };
  }

  if (stats.freshCount === 0) {
    return {
      label: "Stale",
      detail: `All cached ${countryLabel} keyword intel is stale. Last poll: ${formatDateTime(stats.latestPolledAt)}.`,
      color: "yellow",
    };
  }

  if (stats.averageSov > 0) {
    return {
      label: `SOV ${formatPercent(stats.averageSov)}%`,
      detail: `Average top-3 share across ${pluralize(stats.freshCount, "fresh watched keyword")} in ${countryLabel}. Present in ${stats.latestPresenceCount}/${stats.freshCount} latest snapshots.`,
      color: "green",
    };
  }

  if (stats.latestPresenceCount > 0) {
    return {
      label: "No top 3",
      detail: `Fresh ${countryLabel} snapshots exist, but this app is not in the top 3 on any watched keyword.`,
      color: "blue",
    };
  }

  return {
    label: "Out of top 20",
    detail: `Fresh ${countryLabel} snapshots exist, but this app is missing from the latest top 20 for every watched keyword.`,
    color: "orange",
  };
}
