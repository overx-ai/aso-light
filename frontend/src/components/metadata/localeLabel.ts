// Static map of Apple BCP-47 locale codes to human-readable display names.
// Apple uses non-standard codes (zh-Hans, nb, el) — Intl.DisplayNames handles
// most but not all, so we keep a curated map for the locales we actually
// support (mirrors the canonical set used by cross_localization.py).

const LOCALE_LABELS: Record<string, string> = {
  "en-US": "English (US)",
  "en-GB": "English (UK)",
  "en-AU": "English (Australia)",
  "en-CA": "English (Canada)",
  "de-DE": "German (Germany)",
  "fr-FR": "French (France)",
  "fr-CA": "French (Canada)",
  "es-MX": "Spanish (Mexico)",
  "es-ES": "Spanish (Spain)",
  "ja": "Japanese",
  "ko": "Korean",
  "zh-Hans": "Chinese (Simplified)",
  "zh-Hant": "Chinese (Traditional)",
  "pt-BR": "Portuguese (Brazil)",
  "pt-PT": "Portuguese (Portugal)",
  "it": "Italian",
  "nl": "Dutch",
  "sv": "Swedish",
  "pl": "Polish",
  "nb": "Norwegian",
  "da": "Danish",
  "fi": "Finnish",
  "el": "Greek",
  "ru": "Russian",
  "hi": "Hindi",
  "tr": "Turkish",
  "ar": "Arabic",
  "he": "Hebrew",
  "ms": "Malay",
  "vi": "Vietnamese",
  "id": "Indonesian",
  "th": "Thai",
  "uk": "Ukrainian",
  "cs": "Czech",
  "ro": "Romanian",
  "hu": "Hungarian",
  "ca": "Catalan",
  "hr": "Croatian",
  "sk": "Slovak",
};

let displayNames: Intl.DisplayNames | null = null;
function getDisplayNames(): Intl.DisplayNames | null {
  if (displayNames) return displayNames;
  if (typeof Intl !== "undefined" && "DisplayNames" in Intl) {
    try {
      displayNames = new Intl.DisplayNames(["en"], { type: "language" });
      return displayNames;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Best-effort human label for an Apple locale code.
 * Falls back to Intl.DisplayNames, then to the raw code.
 */
export function localeLabel(locale: string): string {
  if (LOCALE_LABELS[locale]) return LOCALE_LABELS[locale];
  const dn = getDisplayNames();
  if (dn) {
    try {
      const out = dn.of(locale);
      if (out && out !== locale) return out;
    } catch {
      // fall through
    }
  }
  return locale;
}

export function localeWithCode(locale: string): string {
  const label = localeLabel(locale);
  return label === locale ? locale : `${label} (${locale})`;
}
