import { describe, expect, it } from "vitest";
import { emptyDraft, validateActionDraft } from "./actionRules";

describe("action draft rules", () => {
  it("requires an inclusive crossing frame only for hand boundary labels", () => {
    expect(validateActionDraft({
      ...emptyDraft(), label: "hand_in", start_frame: 10, end_frame: 20,
      crossing_frame: null, interaction_id: "interaction",
    })).toMatch(/crossing/i);
    expect(validateActionDraft({
      ...emptyDraft(), label: "hand_out", start_frame: 10, end_frame: 20,
      crossing_frame: 10, interaction_id: "interaction",
    })).toBeNull();
    expect(validateActionDraft({
      ...emptyDraft(), label: "take_out", start_frame: 10, end_frame: 20,
      crossing_frame: 15, interaction_id: "interaction",
    })).toMatch(/crossing/i);
  });

  it("requires explicit uncertainty metadata without turning unclear into an action", () => {
    expect(validateActionDraft({
      ...emptyDraft(), label: "unclear", start_frame: 1, end_frame: 2,
      crossing_frame: null, interaction_id: null,
    })).toMatch(/nhãn có thể/i);
    expect(validateActionDraft({
      ...emptyDraft(), label: "unclear", start_frame: 1, end_frame: 2,
      crossing_frame: null, interaction_id: null,
      uncertain_labels: ["hand_in"], unclear_reason: "occlusion",
    })).toBeNull();
  });
});
