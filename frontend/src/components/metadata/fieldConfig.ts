import type { MetadataKind, LocaleUpsertIn } from "@/types";

// Per-field UI / validation config. Char limits mirror the server-side
// validators in backend/app/services/metadata/validation.py.

export type FieldKey = keyof LocaleUpsertIn;

export interface FieldConfig {
  key: FieldKey;
  label: string;
  kind: MetadataKind;
  multiline: boolean;
  charLimit: number | null;
  // App Store Connect categorises fields as either short (one-line input) or
  // long-form (textarea); short fields use TextInput.
}

export const FIELD_CONFIGS: FieldConfig[] = [
  // app_info
  { key: "name", label: "Name", kind: "app_info", multiline: false, charLimit: 30 },
  { key: "subtitle", label: "Subtitle", kind: "app_info", multiline: false, charLimit: 30 },
  { key: "privacy_policy_url", label: "Privacy Policy URL", kind: "app_info", multiline: false, charLimit: null },
  // version
  { key: "description", label: "Description", kind: "version", multiline: true, charLimit: 4000 },
  { key: "keywords", label: "Keywords", kind: "version", multiline: true, charLimit: 100 },
  { key: "promotional_text", label: "Promotional Text", kind: "version", multiline: true, charLimit: 170 },
  { key: "whats_new", label: "What's New", kind: "version", multiline: true, charLimit: 4000 },
  { key: "marketing_url", label: "Marketing URL", kind: "version", multiline: false, charLimit: null },
  { key: "support_url", label: "Support URL", kind: "version", multiline: false, charLimit: null },
];

export const FIELDS_BY_KEY: Record<string, FieldConfig> = Object.fromEntries(
  FIELD_CONFIGS.map((f) => [f.key, f]),
);

export function fieldsForKind(kind: MetadataKind): FieldConfig[] {
  return FIELD_CONFIGS.filter((f) => f.kind === kind);
}

/** Format a relative-time string for "synced N min ago"-style chips. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  const diffMs = d.getTime() - Date.now();
  const diffSec = Math.round(diffMs / 1000);
  const abs = Math.abs(diffSec);

  const fmt = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 60) return fmt.format(diffSec, "second");
  if (abs < 3600) return fmt.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return fmt.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 30) return fmt.format(Math.round(diffSec / 86400), "day");
  if (abs < 86400 * 365) return fmt.format(Math.round(diffSec / (86400 * 30)), "month");
  return fmt.format(Math.round(diffSec / (86400 * 365)), "year");
}
