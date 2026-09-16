import { useEffect, useRef } from "react";
import { ACTION_LABELS, validateActionDraft, type ActionDraft, type ClearActionLabel } from "./actionRules";

const UNCLEAR_REASONS = [
  ["occlusion", "Bị che khuất"],
  ["boundary_ambiguous", "Không rõ qua biên"],
  ["clip_boundary", "Thiếu đầu/cuối clip"],
  ["actor_ambiguous", "Không rõ tay/người"],
  ["object_ambiguous", "Không rõ vật"],
] as const;
const CLEAR_LABELS: ClearActionLabel[] = ["hand_in", "hand_out", "take_out", "put_in"];

export function ActionEditor({ draft, onChange, currentFrame, frameReady, saving, editing, error, onSave, onCancel }: {
  draft: ActionDraft;
  onChange: (draft: ActionDraft) => void;
  currentFrame: number;
  frameReady: boolean;
  saving: boolean;
  editing: boolean;
  error: string | null;
  onSave: (draft: ActionDraft) => void;
  onCancel: () => void;
}) {
  const latestDraft = useRef(draft);
  latestDraft.current = draft;
  const update = (values: Partial<ActionDraft>) => {
    const next = { ...latestDraft.current, ...values };
    latestDraft.current = next;
    onChange(next);
  };
  const chooseLabel = (label: Exclude<ActionDraft["label"], null>) => update({
    label,
    crossing_frame: label === "hand_in" || label === "hand_out" ? latestDraft.current.crossing_frame : null,
    uncertain_labels: label === "unclear" && !latestDraft.current.uncertain_labels.length
      ? [...CLEAR_LABELS] : latestDraft.current.uncertain_labels,
    unclear_reason: label === "unclear" ? latestDraft.current.unclear_reason : null,
  });
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (saving) return;
      const target = event.target as HTMLElement | null;
      if (target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable)) return;
      const number = Number(event.key);
      if (number >= 1 && number <= ACTION_LABELS.length) chooseLabel(ACTION_LABELS[number - 1].value);
      if (frameReady && event.key.toLowerCase() === "i") update({ start_frame: currentFrame });
      if (frameReady && event.key.toLowerCase() === "o") update({ end_frame: currentFrame });
      if (frameReady && event.key.toLowerCase() === "c"
        && (latestDraft.current.label === "hand_in" || latestDraft.current.label === "hand_out")) {
        update({ crossing_frame: currentFrame });
      }
      if (event.key === "Escape") onCancel();
      if (event.ctrlKey && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!validateActionDraft(latestDraft.current)) onSave(latestDraft.current);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const invalid = Boolean(validateActionDraft(draft));
  return <form className="action-editor" onSubmit={(event) => {
    event.preventDefault();
    if (!validateActionDraft(latestDraft.current)) onSave(latestDraft.current);
  }}>
    <div className="action-editor-heading"><div><h3>{editing ? "Sửa nhãn" : "Nhãn mới"}</h3><p>Frame hiện tại: <strong>{currentFrame}</strong>{!frameReady && " · đang tải ảnh chính xác"}</p></div><button type="button" className="secondary compact" disabled={saving} onClick={onCancel}>Xóa nháp</button></div>
    {draft.estimated && <p className="model-draft-note"><strong>Mốc do model gợi ý.</strong> Hãy xem lại nhãn, lượt tay và từng frame trước khi lưu.</p>}
    <fieldset disabled={saving}><legend>Hành động</legend><div className="label-choice">
      {ACTION_LABELS.map((item, position) => <button type="button" key={item.value} aria-pressed={draft.label === item.value} onClick={() => chooseLabel(item.value)}><kbd>{position + 1}</kbd>{item.text}</button>)}
    </div></fieldset>
    <div className="frame-capture-grid">
      <button type="button" disabled={!frameReady || saving} onClick={() => update({ start_frame: currentFrame })}><kbd>I</kbd>Bắt đầu <strong>{draft.start_frame ?? "—"}</strong></button>
      {(draft.label === "hand_in" || draft.label === "hand_out") && <button type="button" disabled={!frameReady || saving} onClick={() => update({ crossing_frame: currentFrame })}><kbd>C</kbd>Qua biên <strong>{draft.crossing_frame ?? "—"}</strong></button>}
      <button type="button" disabled={!frameReady || saving} onClick={() => update({ end_frame: currentFrame })}><kbd>O</kbd>Kết thúc <strong>{draft.end_frame ?? "—"}</strong></button>
    </div>
    <div className="action-fields">
      <label>Vật<select disabled={saving} value={draft.object_kind} onChange={(event) => update({ object_kind: event.target.value as ActionDraft["object_kind"] })}><option value="unknown">Chưa rõ</option><option value="cash">Tiền</option><option value="other">Khác</option></select></label>
      <label>Quan sát<select disabled={saving} value={draft.visibility} onChange={(event) => update({ visibility: event.target.value as ActionDraft["visibility"] })}><option value="clear">Rõ</option><option value="occluded">Bị che</option></select></label>
    </div>
    {draft.label === "unclear" && <div className="unclear-fields"><fieldset disabled={saving}><legend>Nhãn có thể xảy ra</legend>{CLEAR_LABELS.map((label) => <label key={label}><input type="checkbox" checked={draft.uncertain_labels.includes(label)} onChange={() => update({ uncertain_labels: draft.uncertain_labels.includes(label) ? draft.uncertain_labels.filter((item) => item !== label) : [...draft.uncertain_labels, label] })} />{label}</label>)}</fieldset>
      <label>Lý do<select disabled={saving} value={draft.unclear_reason ?? ""} onChange={(event) => update({ unclear_reason: (event.target.value || null) as ActionDraft["unclear_reason"] })}><option value="">Chọn lý do</option>{UNCLEAR_REASONS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label></div>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="editor-actions"><button className="primary" type="submit" disabled={saving || invalid}>{saving ? "Đang lưu…" : editing ? "Lưu thay đổi" : "Lưu nhãn"}</button><span><kbd>Ctrl</kbd>+<kbd>S</kbd></span></div>
  </form>;
}
