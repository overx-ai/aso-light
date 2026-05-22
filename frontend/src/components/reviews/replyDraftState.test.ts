import { describe, expect, it } from "vitest";
import type { ReviewResponseOut } from "@/types";
import { getInitialReplyValue, isReplyDirty } from "./replyDraftState";

describe("replyDraftState", () => {
  it("starts unreplied reviews with an empty editable draft", () => {
    const initialReply = getInitialReplyValue(null);

    expect(initialReply).toBe("");
    expect(isReplyDirty(null, initialReply)).toBe(false);
  });

  it("loads the existing App Store Connect reply into the editor", () => {
    const existingResponse: ReviewResponseOut = {
      id: "response-1",
      body: "Thanks for the feedback.",
      last_modified_date: null,
      state: null,
    };

    expect(getInitialReplyValue(existingResponse)).toBe(existingResponse.body);
    expect(isReplyDirty(existingResponse, existingResponse.body)).toBe(false);
  });

  it("marks manually entered reply text as dirty for unreplied reviews", () => {
    expect(isReplyDirty(null, "Thanks for reporting this.")).toBe(true);
  });
});
