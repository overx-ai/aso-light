import type { BulkPreviewIn } from "@/types";

export type BulkValueMode = "same" | "localized";

export const bulkMetadataCopy = {
  sameModeLabel: "Same value for all locales",
  localizedModeLabel: "Localized values per locale",
  previewHelp: "Preview shows what will change before anything is applied.",
  diffPreviewTitle: "Diff preview",
  legend:
    "red struck text = removed, green text = added, unchanged rows show No change",
};

export interface ParseExternalTranslationsResult {
  values: Record<string, string | null> | null;
  error: string | null;
}

export function parseExternalTranslations(
  rawJson: string,
  targetLocales: string[],
): ParseExternalTranslationsResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawJson);
  } catch {
    return { values: null, error: "Paste valid JSON object translations." };
  }

  if (
    parsed === null ||
    Array.isArray(parsed) ||
    typeof parsed !== "object"
  ) {
    return { values: null, error: "Translations must be a JSON object." };
  }

  const targetSet = new Set(targetLocales);
  const input = parsed as Record<string, unknown>;
  const extraLocales = Object.keys(input).filter((locale) => !targetSet.has(locale));
  if (extraLocales.length > 0) {
    return {
      values: null,
      error: `Translations include non-target locale: ${extraLocales.join(", ")}`,
    };
  }

  const values: Record<string, string | null> = {};
  for (const locale of targetLocales) {
    if (!(locale in input)) {
      return {
        values: null,
        error: `Missing translation for target locale: ${locale}`,
      };
    }
    const value = input[locale];
    if (value !== null && typeof value !== "string") {
      return {
        values: null,
        error: `Translation for ${locale} must be a string or null.`,
      };
    }
    values[locale] = value;
  }

  return { values, error: null };
}

interface BuildBulkMetadataPayloadInput {
  field: string;
  mode: BulkValueMode;
  value: string | null;
  valuesByLocale: Record<string, string | null>;
  targetLocales: string[];
}

export function buildBulkMetadataPayload({
  field,
  mode,
  value,
  valuesByLocale,
  targetLocales,
}: BuildBulkMetadataPayloadInput): BulkPreviewIn {
  if (mode === "localized") {
    const localizedValues = Object.fromEntries(
      targetLocales.map((locale) => [locale, valuesByLocale[locale] ?? null]),
    );
    return {
      field,
      value: null,
      values_by_locale: localizedValues,
      target_locales: targetLocales,
    };
  }

  return {
    field,
    value,
    target_locales: targetLocales,
  };
}
