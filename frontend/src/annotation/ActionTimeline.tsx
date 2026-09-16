import type { ActionAnnotationView } from "./types.generated";

const LABEL_TEXT: Record<ActionAnnotationView["label"], string> = {
  hand_in: "Hand in", hand_out: "Hand out", take_out: "Take out",
  put_in: "Put in", unclear: "Unclear",
};

export function ActionTimeline({ annotations, frameCount, onSelect, onConfirm, onDelete, onRestore, busy = false }: {
  annotations: ActionAnnotationView[];
  frameCount: number;
  onSelect: (annotation: ActionAnnotationView) => void;
  onConfirm: (annotation: ActionAnnotationView) => void;
  onDelete: (annotation: ActionAnnotationView) => void;
  onRestore: (annotation: ActionAnnotationView) => void;
  busy?: boolean;
}) {
  return <div className="action-timeline"><div className="timeline-heading"><h3>Timeline nhãn</h3><span>{annotations.filter((item) => !item.deleted).length} đang dùng</span></div>
    {!annotations.length && <p className="preview-note">Chưa có nhãn trên clip này.</p>}
    {annotations.map((item) => {
      const left = item.start_frame / frameCount * 100;
      const width = Math.max(0.8, (item.end_frame - item.start_frame + 1) / frameCount * 100);
      return <article className={`timeline-row ${item.deleted ? "deleted" : ""}`} key={item.id} data-testid="timeline-row">
        <button type="button" className="timeline-label" disabled={busy} onClick={() => onSelect(item)}><strong>{LABEL_TEXT[item.label]}</strong><span>{item.start_frame}–{item.end_frame}{item.crossing_frame != null ? ` · C ${item.crossing_frame}` : ""}</span></button>
        <div className="timeline-track" aria-hidden="true"><span className={`timeline-bar label-${item.label}`} style={{ left: `${left}%`, width: `${width}%` }} /></div>
        <span className={`review-state state-${item.review_state}`}>{item.deleted ? "đã xóa" : item.review_state}</span>
        <div className="timeline-actions">{item.deleted ? <button type="button" disabled={busy} onClick={() => onRestore(item)}>Khôi phục</button> : <><button type="button" onClick={() => onConfirm(item)} disabled={busy || item.review_state === "confirmed"}>Xác nhận</button><button type="button" disabled={busy} onClick={() => onDelete(item)}>Xóa</button></>}</div>
      </article>;
    })}
  </div>;
}
