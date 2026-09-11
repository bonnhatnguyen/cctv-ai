import { useRef, useState } from "react";
import { JobView, resultDownloadUrl, TrackingStage } from "./trackingApi";
import type { ClipView } from "./annotation/types.generated";
import { RoiVideo } from "./annotation/RoiVideo";

const stageLabels: Record<TrackingStage, string> = {
  imported: "Video đã sẵn sàng để bắt đầu",
  queued: "Đang chờ đến lượt",
  loading: "Đang tải mô hình",
  tracking: "Đang theo dõi người",
  encoding: "Đang tạo video kết quả",
  validating: "Đang kiểm tra video kết quả",
  ready: "Đã hoàn tất",
  failed: "Không thể xử lý video",
};

function number(value: number, maximumFractionDigits = 1): string {
  return new Intl.NumberFormat("vi-VN", { maximumFractionDigits }).format(value);
}

export function TrackingResult({ job, annotationClip }: { job: JobView; annotationClip?: ClipView }) {
  const resultVideo = useRef<HTMLVideoElement>(null);
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const summary = job.summary;
  const roi = annotationClip?.source_job_id === job.id && annotationClip.source_state === "available"
    ? annotationClip.roi : null;

  if (job.status === "imported") return null;

  if (job.status === "failed") {
    return (
      <section className="status-panel" aria-live="polite">
        <p className="error" role="alert">
          Không thể xử lý video. Video gốc vẫn được giữ lại trên máy này.
        </p>
      </section>
    );
  }

  if (job.status !== "ready" || !job.result_url || !summary) {
    const percent = job.stage === "tracking" && job.tracking_percent !== null
      ? Math.min(99.9, job.tracking_percent)
      : null;
    return (
      <section className="status-panel" aria-labelledby="status-title" aria-live="polite">
        <div className="section-heading">
          <span className="step">2</span>
          <div>
            <h2 id="status-title">{stageLabels[job.stage]}</h2>
            <p>Máy đang xử lý lần lượt từng khung hình.</p>
          </div>
        </div>
        {percent !== null && (
          <div className="tracking-progress">
            <span>Tiến độ theo dõi ước tính: {number(percent)}%</span>
            <progress value={percent} max={100} />
            <small>Dựa trên số khung hình ước tính; chỉ hoàn tất sau bước kiểm tra video.</small>
          </div>
        )}
      </section>
    );
  }

  const download = resultDownloadUrl(job)!;
  const replay = async () => {
    const video = resultVideo.current;
    if (!video) return;
    video.currentTime = 0;
    await video.play();
  };

  return (
    <section className="result-panel" aria-labelledby="result-title">
      <div className="section-heading success-heading">
        <span className="step complete" aria-hidden="true">✓</span>
        <div>
          <p className="eyebrow">Đã hoàn tất</p>
          <h2 id="result-title">Video theo dõi người đã sẵn sàng</h2>
          <p>ID chỉ dùng để nối cùng một người trong video này, không phải danh tính thật.</p>
        </div>
      </div>

      <div className="video-grid">
        <figure>
          <figcaption>Video gốc</figcaption>
          {job.metadata.preview_supported && failedSource !== job.source_url ? (
            <RoiVideo media={job.metadata} roi={roi} key={job.source_url} aria-label="Video gốc" controls preload="metadata" src={job.source_url}
              onError={() => setFailedSource(job.source_url)} />
          ) : (
            <p className="preview-note">Trình duyệt không hỗ trợ xem trước video gốc này.</p>
          )}
        </figure>
        <figure>
          <figcaption>Video đã theo dõi</figcaption>
          <RoiVideo media={job.metadata} roi={roi} key={job.result_url} ref={resultVideo} aria-label="Video đã theo dõi" controls preload="metadata" src={job.result_url} />
          <div className="result-actions">
            <button className="secondary" type="button" onClick={replay}>Phát lại video kết quả</button>
            <a className="primary-link" href={download}>Tải video kết quả</a>
          </div>
        </figure>
      </div>

      {roi && <p className="preview-note">ROI đã lưu được hiển thị trên trang này. Tệp MP4 tải xuống và chế độ toàn màn hình riêng của video không chứa lớp ROI.</p>}

      <div className="metrics">
        <h3>Số liệu đo được</h3>
        {summary.actual_device === "cpu" && (
          <p className="preview-note">CPU là chế độ dự phòng để chẩn đoán, xử lý chậm hơn CUDA.</p>
        )}
        <dl>
          <div><dt>Thiết bị thực tế</dt><dd>{summary.actual_device} · {summary.device_name}</dd></div>
          <div><dt>Khung hình đã xử lý</dt><dd>{number(summary.processed_frames, 0)}</dd></div>
          <div><dt>Số ID theo dõi cục bộ trong clip</dt><dd>{number(summary.local_track_count, 0)}<small>Không phải số người duy nhất.</small></dd></div>
          <div><dt>Tổng thời gian xử lý</dt><dd>{number(summary.processing_seconds)} giây</dd></div>
          <div><dt>Tốc độ xử lý thực tế</dt><dd>{number(summary.effective_fps)} khung hình/giây</dd></div>
          {summary.mean_inference_ms !== null && summary.inference_samples > 0 && (
            <div><dt>Thời gian suy luận trung bình</dt><dd>{number(summary.mean_inference_ms, 2)} ms</dd></div>
          )}
        </dl>
      </div>
    </section>
  );
}
