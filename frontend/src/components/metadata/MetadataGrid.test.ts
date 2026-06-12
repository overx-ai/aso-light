import { describe, expect, it } from "vitest";

import { parseWords } from "./MetadataGrid";

describe("parseWords", () => {
  it("returns nothing for empty or null input", () => {
    expect(parseWords(null)).toEqual([]);
    expect(parseWords("")).toEqual([]);
    expect(parseWords("   ")).toEqual([]);
  });

  it("strips surrounding punctuation and drops connector symbols", () => {
    expect(parseWords("Refresher: Breathing & Focus")).toEqual([
      "Refresher",
      "Breathing",
      "Focus",
    ]);
    expect(parseWords("Wim Hof, Box, Sleep & Coherent")).toEqual([
      "Wim",
      "Hof",
      "Box",
      "Sleep",
      "Coherent",
    ]);
  });

  it("drops empty and single-character tokens", () => {
    // "y" (Spanish) and "e" (Portuguese) connectors are one char -> dropped.
    expect(parseWords("Respira y Enfoca")).toEqual(["Respira", "Enfoca"]);
    expect(parseWords("Respire e Foque")).toEqual(["Respire", "Foque"]);
  });

  it("keeps multi-character numeric tokens", () => {
    expect(parseWords("478 deep breaths")).toEqual(["478", "deep", "breaths"]);
  });

  it("dedupes case-insensitively while preserving first-seen casing/order", () => {
    expect(parseWords("Calm calm CALM focus")).toEqual(["Calm", "focus"]);
  });

  it("preserves umlauts and non-Latin scripts", () => {
    expect(parseWords("Wim Hof, Quadrat und Kohärent")).toEqual([
      "Wim",
      "Hof",
      "Quadrat",
      "und",
      "Kohärent",
    ]);
    expect(parseWords("Refresher: Дыхание и Внимание")).toEqual([
      "Refresher",
      "Дыхание",
      "Внимание",
    ]);
  });
});
