import type { ReviewResponseOut } from "@/types";

export function getInitialReplyValue(existingResponse: ReviewResponseOut | null): string {
  return existingResponse?.body ?? "";
}

export function isReplyDirty(
  existingResponse: ReviewResponseOut | null,
  reply: string,
): boolean {
  return (existingResponse?.body ?? "") !== reply;
}
