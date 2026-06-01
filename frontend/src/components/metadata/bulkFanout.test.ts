import { describe, expect, it } from "vitest";

import {
  bulkMetadataCopy,
  buildBulkMetadataPayload,
  parseExternalTranslations,
} from "./bulkFanout";

describe("parseExternalTranslations", () => {
  it("accepts a JSON object keyed by selected target locale", () => {
    expect(
      parseExternalTranslations(
        '{ "es-ES": "Respira con calma", "ru": "Дышите спокойнее" }',
        ["es-ES", "ru"],
      ),
    ).toEqual({
      values: {
        "es-ES": "Respira con calma",
        ru: "Дышите спокойнее",
      },
      error: null,
    });
  });

  it("rejects locales outside the selected target set", () => {
    expect(
      parseExternalTranslations('{"fr-FR":"Respirez"}', ["es-ES", "ru"]),
    ).toEqual({
      values: null,
      error: "Translations include non-target locale: fr-FR",
    });
  });

  it("rejects missing selected target locale values", () => {
    expect(
      parseExternalTranslations('{"es-ES":"Respira"}', ["es-ES", "ru"]),
    ).toEqual({
      values: null,
      error: "Missing translation for target locale: ru",
    });
  });
});

describe("buildBulkMetadataPayload", () => {
  it("sends value for same-value mode", () => {
    expect(
      buildBulkMetadataPayload({
        field: "promotional_text",
        mode: "same",
        value: "Breathe better",
        valuesByLocale: { "es-ES": "Respira mejor" },
        targetLocales: ["es-ES", "ru"],
      }),
    ).toEqual({
      field: "promotional_text",
      value: "Breathe better",
      target_locales: ["es-ES", "ru"],
    });
  });

  it("sends values_by_locale for localized mode", () => {
    expect(
      buildBulkMetadataPayload({
        field: "promotional_text",
        mode: "localized",
        value: "Breathe better",
        valuesByLocale: {
          "es-ES": "Respira mejor",
          ru: "Дышите лучше",
        },
        targetLocales: ["es-ES", "ru"],
      }),
    ).toEqual({
      field: "promotional_text",
      value: null,
      values_by_locale: {
        "es-ES": "Respira mejor",
        ru: "Дышите лучше",
      },
      target_locales: ["es-ES", "ru"],
    });
  });
});

describe("bulkMetadataCopy", () => {
  it("centralizes new drawer copy", () => {
    expect(bulkMetadataCopy.diffPreviewTitle).toBe("Diff preview");
    expect(bulkMetadataCopy.previewHelp).toBe(
      "Preview shows what will change before anything is applied.",
    );
    expect(bulkMetadataCopy.legend).toContain("red struck text = removed");
    expect(bulkMetadataCopy.sameModeLabel).toBe("Same value for all locales");
    expect(bulkMetadataCopy.localizedModeLabel).toBe(
      "Localized values per locale",
    );
  });
});
