import { useEffect, useState } from "react";
import { getTrackingJob, TrackingApiError } from "../trackingApi";
import type { JobView } from "../trackingApi";
import { TrackingResult } from "../TrackingResult";
import type { ClipView } from "./types.generated";

export function ClipTracking({ clip }: { clip: ClipView }) {
  // A separate lifetime per source prevents results from a previous selection appearing.
  return <BoundTracking key={clip.source_job_id} clip={clip} />;
}

function BoundTracking({ clip }: { clip: ClipView }) {
  const [request, setRequest] = useState(0);
  const [job, setJob] = useState<JobView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!request || clip.source_state !== "available") return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setJob(null);
    getTrackingJob(clip.source_job_id, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.id !== clip.source_job_id) throw new Error("source_mismatch");
      setJob(value);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof TrackingApiError && reason.status === 404
        ? "Không còn phiên tracking liên kết với clip này."
        : "Không thể tải đúng kết quả tracking của clip này. Hãy thử lại.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [request, clip.source_job_id, clip.source_state]);
  return <div className="clip-tracking">
    <button type="button" className="secondary" disabled={loading || clip.source_state !== "available"}
      onClick={() => setRequest((value) => value + 1)}>
      {loading ? "Đang tải tracking…" : "Xem tracking của clip này"}
    </button>
    {error && <p className="error" role="alert">{error}</p>}
    {job && clip.source_state === "available" && <>
      <p>Kết quả của clip: <strong>{clip.original_name}</strong></p>
      {job.status === "imported"
        ? <p className="preview-note">Clip này chưa chạy tracking. Bạn có thể chuẩn bị video và khoanh ROI mà không cần tracking.</p>
        : <TrackingResult job={job} annotationClip={clip} />}
      {["queued", "processing"].includes(job.status) && <p className="preview-note">Bấm “Xem tracking của clip này” để cập nhật kết quả.</p>}
    </>}
  </div>;
}
