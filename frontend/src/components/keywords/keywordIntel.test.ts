import { describe, expect, it } from "vitest";

import { getKeywordIntelState } from "./keywordIntel";

describe("getKeywordIntelState", () => {
  const now = new Date("2026-05-22T12:00:00.000Z").getTime();

  it("marks recent keyword intel as fresh", () => {
    expect(
      getKeywordIntelState(64, "2026-05-20T12:00:00.000Z", now),
    ).toMatchObject({
      kind: "fresh",
      label: "64",
      color: "blue",
    });
  });

  it("marks old keyword intel as stale without hiding the last score", () => {
    expect(
      getKeywordIntelState(47, "2026-05-01T12:00:00.000Z", now),
    ).toMatchObject({
      kind: "stale",
      label: "47 stale",
      color: "yellow",
    });
  });

  it("marks missing keyword intel as missing instead of a numeric score", () => {
    expect(getKeywordIntelState(null, null, now)).toMatchObject({
      kind: "missing",
      label: "No intel",
      color: "gray",
    });
  });

  it("treats scores without a refresh timestamp as stale", () => {
    expect(getKeywordIntelState(31, null, now)).toMatchObject({
      kind: "stale",
      label: "31 stale",
      color: "yellow",
    });
  });
});
