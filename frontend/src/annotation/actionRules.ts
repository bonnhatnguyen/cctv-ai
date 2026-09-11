import type { ActionAnnotationCreate } from "./types.generated";

export type ActionLabel = ActionAnnotationCreate["label"];
export type ClearActionLabel = Exclude<ActionLabel, "unclear">;
export type UnclearReason = NonNullable<ActionAnnotationCreate["unclear_reason"]>;

export interface ActionDraft {
  interaction_id: string | null;
  label: ActionLabel;
  start_frame: number | null;
  end_frame: number | null;
  crossing_frame: number | null;
  object_kind: ActionAnnotationCreate["object_kind"];
  visibility: ActionAnnotationCreate["visibility"];
  uncertain_labels: ClearActionLabel[];
  unclear_reason: UnclearReason | null;
}

export const ACTION_LABELS: ReadonlyArray<{ value: ActionLabel; text: string }> = [
  { value: "hand_in", text: "Hand in" },
  { value: "hand_out", text: "Hand out" },
  { value: "take_out", text: "Take out" },
  { value: "put_in", text: "Put in" },
  { value: "unclear", text: "Unclear" },
];

export const emptyDraft = (): ActionDraft => ({
  interaction_id: null,
  label: "hand_in",
  start_frame: null,
  end_frame: null,
  crossing_frame: null,
  object_kind: "unknown",
  visibility: "clear",
  uncertain_labels: [],
  unclear_reason: null,
});

export function validateActionDraft(draft: ActionDraft): string | null {
  if (draft.start_frame === null || draft.end_frame === null) {
    return "Cần chọn frame bắt đầu và kết thúc.";
  }
  if (draft.end_frame < draft.start_frame) {
    return "Frame kết thúc phải từ frame bắt đầu trở đi.";
  }
  if (draft.label === "hand_in" || draft.label === "hand_out") {
    if (
      draft.crossing_frame === null
      || draft.crossing_frame < draft.start_frame
      || draft.crossing_frame > draft.end_frame
    ) return "Crossing frame phải nằm trong khoảng hành động.";
  } else if (draft.crossing_frame !== null) {
    return "Crossing frame chỉ dùng cho hand in và hand out.";
  }
  if (draft.label === "unclear") {
    if (!draft.uncertain_labels.length) return "Chọn ít nhất một nhãn có thể xảy ra.";
    if (!draft.unclear_reason) return "Chọn lý do chưa thể phân loại.";
  } else if (!draft.interaction_id) {
    return "Chọn một lượt tay cho hành động rõ.";
  }
  return null;
}
