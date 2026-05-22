import type { ReviewTrendOut, ReviewTrendPointOut } from "@/types";

export interface TrendMoment {
  label: string;
  value: string;
  hint: string;
}

export function formatTrendDate(value: string | null): string {
  if (!value) return "—";
  return new Date(`${value}T12:00:00Z`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function formatAverageRating(value: number | null): string {
  if (value === null) return "—";
  return `${value.toFixed(1)} / 5`;
}

export function formatLowRatingLabel(lowRatingMax: number): string {
  if (lowRatingMax <= 0) return "Low-rating reviews";
  if (lowRatingMax === 1) return "1-star reviews";
  return `1-${lowRatingMax} star reviews`;
}

export function largestSwing(summary: ReviewTrendOut["summary"]): {
  value: string;
  hint: string;
  color: string;
  icon: "up" | "down" | "flat";
} {
  if (
    summary.biggest_spike_delta > 0 &&
    summary.biggest_spike_delta >= Math.abs(summary.biggest_drop_delta)
  ) {
    return {
      value: `+${summary.biggest_spike_delta}`,
      hint: `Spike on ${formatTrendDate(summary.biggest_spike_date)}`,
      color: "red",
      icon: "up",
    };
  }

  if (summary.biggest_drop_delta < 0) {
    return {
      value: `${summary.biggest_drop_delta}`,
      hint: `Drop on ${formatTrendDate(summary.biggest_drop_date)}`,
      color: "teal",
      icon: "down",
    };
  }

  return {
    value: "Flat",
    hint: "No sharp swing in this window",
    color: "gray",
    icon: "flat",
  };
}

function firstPeakBy<T extends number>(
  points: ReviewTrendPointOut[],
  accessor: (point: ReviewTrendPointOut) => T,
): ReviewTrendPointOut | null {
  let best: ReviewTrendPointOut | null = null;
  let bestValue: T | null = null;

  for (const point of points) {
    const value = accessor(point);
    if (bestValue === null || value > bestValue) {
      best = point;
      bestValue = value;
    }
  }

  return best;
}

export function buildTrendMoments(trend: ReviewTrendOut): TrendMoment[] {
  const activePoints = trend.points.filter((point) => point.total_reviews > 0);
  if (activePoints.length === 0) {
    return [];
  }

  const worstDay = firstPeakBy(
    activePoints.filter((point) => point.low_rating_reviews > 0),
    (point) => point.low_rating_reviews,
  );
  const busiestDay = firstPeakBy(activePoints, (point) => point.total_reviews);

  const lowestRatedDay = activePoints.reduce<ReviewTrendPointOut | null>(
    (lowest, point) => {
      if (point.average_rating === null) return lowest;
      if (!lowest || lowest.average_rating === null) return point;
      return point.average_rating < lowest.average_rating ? point : lowest;
    },
    null,
  );

  const moments: TrendMoment[] = [];

  if (worstDay) {
    moments.push({
      label: "Worst day",
      value: `${worstDay.low_rating_reviews} low-star`,
      hint: `${formatTrendDate(worstDay.date)} · ${worstDay.total_reviews} total reviews`,
    });
  }

  if (busiestDay) {
    moments.push({
      label: "Busiest day",
      value: `${busiestDay.total_reviews} reviews`,
      hint: `${formatTrendDate(busiestDay.date)} · ${busiestDay.low_rating_reviews} low-star`,
    });
  }

  if (lowestRatedDay?.average_rating !== null && lowestRatedDay) {
    moments.push({
      label: "Lowest rating day",
      value: formatAverageRating(lowestRatedDay.average_rating),
      hint: `${formatTrendDate(lowestRatedDay.date)} · ${lowestRatedDay.low_rating_reviews} low-star`,
    });
  }

  return moments;
}
