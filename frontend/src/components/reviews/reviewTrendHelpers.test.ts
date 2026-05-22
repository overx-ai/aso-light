import { describe, expect, it } from "vitest";

import type { ReviewTrendOut } from "@/types";

import {
  buildTrendMoments,
  formatAverageRating,
  formatLowRatingLabel,
  formatTrendDate,
  largestSwing,
} from "./reviewTrendHelpers";

const trendFixture: ReviewTrendOut = {
  days: 7,
  low_rating_max: 2,
  territory: null,
  partial: false,
  points: [
    {
      date: "2026-05-15",
      total_reviews: 2,
      low_rating_reviews: 0,
      replied_reviews: 1,
      average_rating: 4.5,
    },
    {
      date: "2026-05-16",
      total_reviews: 6,
      low_rating_reviews: 4,
      replied_reviews: 2,
      average_rating: 1.8,
    },
    {
      date: "2026-05-17",
      total_reviews: 4,
      low_rating_reviews: 1,
      replied_reviews: 1,
      average_rating: 3.0,
    },
  ],
  summary: {
    total_reviews: 12,
    low_rating_reviews: 5,
    replied_reviews: 4,
    average_rating: 2.9,
    low_rating_share_pct: 41.7,
    response_rate_pct: 33.3,
    latest_total_reviews: 4,
    latest_low_rating_reviews: 1,
    biggest_spike_date: "2026-05-16",
    biggest_spike_delta: 4,
    biggest_drop_date: "2026-05-17",
    biggest_drop_delta: -3,
  },
};

describe("reviewTrendHelpers", () => {
  it("formats dashboard values for display", () => {
    expect(formatTrendDate("2026-05-16")).toMatch(/May/);
    expect(formatAverageRating(3.25)).toBe("3.3 / 5");
    expect(formatAverageRating(null)).toBe("—");
    expect(formatLowRatingLabel(1)).toBe("1-star reviews");
    expect(formatLowRatingLabel(3)).toBe("1-3 star reviews");
  });

  it("prefers the largest spike when it dominates the swing", () => {
    expect(largestSwing(trendFixture.summary)).toEqual({
      value: "+4",
      hint: `Spike on ${formatTrendDate("2026-05-16")}`,
      color: "red",
      icon: "up",
    });
  });

  it("falls back to the largest drop when no spike exists", () => {
    expect(
      largestSwing({
        ...trendFixture.summary,
        biggest_spike_date: null,
        biggest_spike_delta: 0,
      }),
    ).toEqual({
      value: "-3",
      hint: `Drop on ${formatTrendDate("2026-05-17")}`,
      color: "teal",
      icon: "down",
    });
  });

  it("returns a flat swing when the window has no sharp moves", () => {
    expect(
      largestSwing({
        ...trendFixture.summary,
        biggest_spike_date: null,
        biggest_spike_delta: 0,
        biggest_drop_date: null,
        biggest_drop_delta: 0,
      }),
    ).toEqual({
      value: "Flat",
      hint: "No sharp swing in this window",
      color: "gray",
      icon: "flat",
    });
  });

  it("builds notable trend moments from active days", () => {
    expect(buildTrendMoments(trendFixture)).toEqual([
      {
        label: "Worst day",
        value: "4 low-star",
        hint: `${formatTrendDate("2026-05-16")} · 6 total reviews`,
      },
      {
        label: "Busiest day",
        value: "6 reviews",
        hint: `${formatTrendDate("2026-05-16")} · 4 low-star`,
      },
      {
        label: "Lowest rating day",
        value: "1.8 / 5",
        hint: `${formatTrendDate("2026-05-16")} · 4 low-star`,
      },
    ]);
  });

  it("returns no moments when the window has no reviews", () => {
    expect(
      buildTrendMoments({
        ...trendFixture,
        points: trendFixture.points.map((point) => ({
          ...point,
          total_reviews: 0,
          low_rating_reviews: 0,
          average_rating: null,
        })),
      }),
    ).toEqual([]);
  });

  it("omits the worst day card when no low-rating reviews landed", () => {
    expect(
      buildTrendMoments({
        ...trendFixture,
        points: [
          {
            ...trendFixture.points[0],
            low_rating_reviews: 0,
            average_rating: 4.5,
          },
          {
            ...trendFixture.points[1],
            low_rating_reviews: 0,
            average_rating: 4.0,
          },
          {
            ...trendFixture.points[2],
            low_rating_reviews: 0,
            average_rating: 3.0,
          },
        ],
      }),
    ).toEqual([
      {
        label: "Busiest day",
        value: "6 reviews",
        hint: `${formatTrendDate("2026-05-16")} · 0 low-star`,
      },
      {
        label: "Lowest rating day",
        value: "3.0 / 5",
        hint: `${formatTrendDate("2026-05-17")} · 0 low-star`,
      },
    ]);
  });
});
