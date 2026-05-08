import { describe, expect, it } from "vitest";

import { buildMetadataDiffSegments } from "./MetadataValueDiff";

describe("buildMetadataDiffSegments", () => {
  it("renders empty to populated as a pure addition", () => {
    expect(buildMetadataDiffSegments("", "Fresh subtitle")).toEqual([
      { kind: "added", text: "Fresh subtitle" },
    ]);
  });

  it("renders populated to empty as a pure removal", () => {
    expect(buildMetadataDiffSegments("Old promo", "")).toEqual([
      { kind: "removed", text: "Old promo" },
    ]);
  });

  it("marks unchanged values without noisy diff segments", () => {
    expect(buildMetadataDiffSegments("No change", "No change")).toEqual([
      { kind: "unchanged", text: "No change" },
    ]);
  });

  it("diffs short single-line fields by word", () => {
    expect(buildMetadataDiffSegments("Calm breath timer", "Calm focus timer")).toEqual([
      { kind: "unchanged", text: "Calm " },
      { kind: "removed", text: "breath" },
      { kind: "added", text: "focus" },
      { kind: "unchanged", text: " timer" },
    ]);
  });

  it("keeps comma-separated keyword changes readable", () => {
    expect(buildMetadataDiffSegments("breathing, calm, sleep", "breathing, focus, sleep")).toEqual([
      { kind: "unchanged", text: "breathing, " },
      { kind: "removed", text: "calm" },
      { kind: "added", text: "focus" },
      { kind: "unchanged", text: ", sleep" },
    ]);
  });

  it("preserves multiline paragraph whitespace", () => {
    expect(
      buildMetadataDiffSegments(
        "Breathe better.\nBuild a daily habit.",
        "Breathe deeper.\nBuild a daily habit.",
      ),
    ).toEqual([
      { kind: "unchanged", text: "Breathe " },
      { kind: "removed", text: "better" },
      { kind: "added", text: "deeper" },
      { kind: "unchanged", text: ".\nBuild a daily habit." },
    ]);
  });
});
