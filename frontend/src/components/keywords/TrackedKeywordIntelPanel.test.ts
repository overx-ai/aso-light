import { describe, expect, it } from "vitest";

import type { KeywordTrackingResponse } from "@/types";

import { buildTrackedKeywordIntelItems } from "./TrackedKeywordIntelPanel";

function makeTracking(
  id: number,
  text: string,
  locale: string,
  popularity: number | null,
  updatedAt: string | null,
): KeywordTrackingResponse {
  return {
    id,
    app_id: 42,
    latest_rank: null,
    rank_change: null,
    added_at: "2026-05-22T12:00:00.000Z",
    keyword: {
      id: id * 10,
      text,
      locale,
      popularity,
      popularity_updated_at: updatedAt,
    },
  };
}

describe("buildTrackedKeywordIntelItems", () => {
  const now = new Date("2026-05-22T12:00:00.000Z").getTime();

  it("keeps tracked keywords ordered consistently across pages", () => {
    const items = buildTrackedKeywordIntelItems(
      [
        makeTracking(2, "sleep sounds", "en-GB", 31, "2026-05-20T12:00:00.000Z"),
        makeTracking(1, "breathing", "en-US", 55, "2026-05-21T12:00:00.000Z"),
        makeTracking(3, "breathing", "de-DE", null, null),
      ],
      now,
    );

    expect(items.map((item) => `${item.text}:${item.locale}`)).toEqual([
      "breathing:de-DE",
      "breathing:en-US",
      "sleep sounds:en-GB",
    ]);
  });

  it("preserves missing and stale intel states instead of coercing them to scores", () => {
    const items = buildTrackedKeywordIntelItems(
      [
        makeTracking(1, "fresh term", "en-US", 64, "2026-05-21T12:00:00.000Z"),
        makeTracking(2, "stale term", "en-US", 21, "2026-05-01T12:00:00.000Z"),
        makeTracking(3, "missing term", "en-US", null, null),
      ],
      now,
    );

    expect(items.map((item) => item.intel)).toMatchObject([
      { kind: "fresh", label: "64" },
      { kind: "missing", label: "No intel" },
      { kind: "stale", label: "21 stale" },
    ]);
  });
});
