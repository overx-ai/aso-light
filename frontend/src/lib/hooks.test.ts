import { describe, expect, it } from "vitest";

import { reviewInvalidationKeys } from "./hooks";

describe("reviewInvalidationKeys", () => {
  it("includes the review trend cache prefix", () => {
    expect(reviewInvalidationKeys(42)).toEqual([
      ["reviews", 42],
      ["review", 42],
      ["review-trend", 42],
    ]);
  });
});
