import { DragEvent } from "react";
import { JobView } from "./trackingApi";

export type LocalVideo = { file: File; name: string; size: number };

type Props = {
  localVideo: LocalVideo | null;
  job: JobView | null;
  uploading: boolean;
  uploadPercent: number | null;
  starting: boolean;
  restoring: boolean;
  actionsLocked: boolean;
  error: string | null;
  onFile: (file: File) => void;
  onRetry: () => void;
  onStart: () => void;
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} byte`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace(".", ",")} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

function formatDuration(durationMs: number): string {
  const totalSeconds = Math.round(durationMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":")
    : [minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

export function VideoImport({
  localVideo,
  job,
  uploading,
  uploadPercent,
  starting,
  restoring,
  actionsLocked,
  error,
  onFile,
  onRetry,
  onStart,
}: Props) {
  const displayedName = localVideo?.name ?? job?.original_name;
  const displayedSize = localVideo?.size ?? job?.metadata.size_bytes;
  const chooseFile = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onFile(file);
  };
  const dropFile = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    if (!actionsLocked) chooseFile(event.dataTransfer.files);
  };

  return (
    <section className="import-panel" aria-labelledby="import-title">
      <div className="section-heading">
        <span className="step">1</span>
        <div>
          <h2 id="import-title">Chọn video MP4</h2>
          <p>Video được tải vào máy này để xử lý và không gửi ra ngoài.</p>
        </div>
      </div>

      <label
        className={`drop-zone${actionsLocked ? " disabled" : ""}`}
        onDragOver={(event) => event.preventDefault()}
        onDrop={dropFile}
      >
        <input
          type="file"
          accept="video/mp4,.mp4"
          aria-label="Chọn video MP4"
          disabled={actionsLocked}
          onClick={(event) => { event.currentTarget.value = ""; }}
          onChange={(event) => chooseFile(event.target.files)}
        />
        <span className="upload-icon" aria-hidden="true">↑</span>
        <strong>{actionsLocked ? "Đang xử lý video hiện tại" : "Chọn video hoặc kéo thả vào đây"}</strong>
        <small>Chỉ nhận một tệp MP4</small>
      </label>

      {restoring && <p className="notice" role="status">Đang khôi phục phiên xử lý trước…</p>}

      {displayedName && displayedSize !== undefined && (
        <div className="file-card">
          <div className="file-mark" aria-hidden="true">MP4</div>
          <div>
            <strong>{displayedName}</strong>
            <span>{formatSize(displayedSize)}</span>
          </div>
          {uploading && (
            <div className="upload-state" role="status">
              {uploadPercent === 100 ? (
                <span className="upload-processing-text">
                  <span className="upload-spinner" aria-hidden="true" />
                  Đang kiểm tra & phân tích video trên máy chủ…
                </span>
              ) : (
                <span>{uploadPercent === null ? "Đang tải lên…" : `Đang tải lên: ${uploadPercent}%`}</span>
              )}
              {uploadPercent !== null && (
                <progress
                  value={uploadPercent}
                  max={100}
                  className={uploadPercent === 100 ? "progress-pulsing" : ""}
                />
              )}
            </div>
          )}
        </div>
      )}

      {error && <p className="error" role="alert">{error}</p>}
      {error && localVideo && !uploading && !job && (
        <button className="secondary" type="button" onClick={onRetry}>Thử tải lên lại</button>
      )}

      {job && (
        <div className="confirmed-metadata">
          <p className="eyebrow">Thông tin đã được máy chủ kiểm tra</p>
          <dl>
            <div><dt>Kích thước hình</dt><dd>{job.metadata.width} × {job.metadata.height}</dd></div>
            <div><dt>Thời lượng</dt><dd>{formatDuration(job.metadata.duration_ms)}</dd></div>
            <div><dt>Định dạng</dt><dd>{job.metadata.codec.toUpperCase()}</dd></div>
          </dl>
        </div>
      )}

      {job?.status === "imported" && (
        <button className="primary" type="button" disabled={starting} onClick={onStart}>
          {starting ? "Đang gửi yêu cầu…" : "Bắt đầu theo dõi người"}
        </button>
      )}
    </section>
  );
}
