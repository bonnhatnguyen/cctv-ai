import type { ClipView } from "./types.generated";

function bytes(value: number | null) {
  return value === null ? "Đang đo" : new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(value / 1024 / 1024) + " MB";
}

export function ClipList({ clips, activeId, onOpen, onRelease, onRetry, onDelete, nextCursor, onMore }: {
  clips: ClipView[];
  activeId?: string;
  onOpen: (clip: ClipView) => void;
  onRelease: (clip: ClipView) => void;
  onRetry: (clip: ClipView) => void;
  onDelete: (clip: ClipView) => void;
  nextCursor: string | null;
  onMore: () => void;
}) {
  return <aside className="clip-list" aria-label="Danh sách clip annotation">
    <h2>Danh sách clip</h2>
    {clips.length === 0 && <p className="preview-note">Chưa có clip nào.</p>}
    {clips.map((clip) => <article key={clip.id} data-clip-id={clip.id} className={clip.id === activeId ? "clip-card active" : "clip-card"}>
      <div className="clip-card-header">
        <strong>{clip.original_name}</strong>
        <span className="clip-badge">{clip.preparation_state} · {bytes(clip.prepared_bytes)}</span>
      </div>
      <div className="clip-card-actions">
        <button type="button" className="secondary" onClick={() => onOpen(clip)} aria-label={`Mở ${clip.original_name}`}>Mở</button>
        {(["ready", "failed"].includes(clip.preparation_state)) && <button type="button" className="btn-release" onClick={() => onRelease(clip)}>Giải phóng tạm</button>}
        {(["released", "failed"].includes(clip.preparation_state)) && clip.source_state !== "hash_mismatch" && <button type="button" className="btn-retry" onClick={() => onRetry(clip)}>Chuẩn bị lại</button>}
        <button type="button" className="btn-danger-outline" onClick={() => onDelete(clip)} title="Xóa vĩnh viễn clip">Xóa</button>
      </div>
    </article>)}
    {nextCursor && <button type="button" className="secondary" onClick={onMore}>Tải thêm</button>}
  </aside>;
}
