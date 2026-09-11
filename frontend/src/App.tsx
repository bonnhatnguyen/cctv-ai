import { useEffect, useRef, useState } from "react";
import { TrackingResult } from "./TrackingResult";
import {
  getTrackingJob,
  importVideo,
  JobView,
  startTracking,
  TrackingApiError,
} from "./trackingApi";
import { LocalVideo, VideoImport } from "./VideoImport";
import { Workspace } from "./annotation/Workspace";

const ACTIVE_JOB_KEY = "v1-active-tracking-job";
const POLL_INTERVAL_MS = 1000;

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function uploadError(error: unknown): string {
  if (error instanceof TrackingApiError) {
    if (error.code === "tep_video_qua_lon") return "Tệp video quá lớn.";
    if (error.code === "khong_the_doc_video") return "Không thể đọc video MP4 này. Hãy chọn một video khác hoặc thử lại.";
    if (error.code === "khong_the_luu_video") return "Không thể lưu video trên máy này. Hãy kiểm tra dung lượng trống rồi thử lại.";
  }
  return "Không thể tải video lên dịch vụ cục bộ. Hãy thử lại.";
}

export default function App() {
  const [mode, setMode] = useState<"tracking" | "annotation">("tracking");
  const [localVideo, setLocalVideo] = useState<LocalVideo | null>(null);
  const [job, setJob] = useState<JobView | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const uploadBusy = useRef(false);
  const startBusy = useRef(false);

  const acceptJob = (value: JobView) => {
    setJob(value);
    localStorage.setItem(ACTIVE_JOB_KEY, value.id);
    setError(null);
  };

  useEffect(() => {
    const storedId = localStorage.getItem(ACTIVE_JOB_KEY);
    if (!storedId) return;
    const requestGeneration = ++generation.current;
    const controller = new AbortController();
    operation.current = controller;
    setRestoring(true);
    getTrackingJob(storedId, controller.signal)
      .then((value) => {
        if (generation.current === requestGeneration) acceptJob(value);
      })
      .catch((reason: unknown) => {
        if (isAbort(reason) || generation.current !== requestGeneration) return;
        if (reason instanceof TrackingApiError && reason.status === 404) {
          localStorage.removeItem(ACTIVE_JOB_KEY);
          setError("Phiên xử lý trước không còn tồn tại. Hãy chọn lại video.");
        } else {
          setError("Không thể khôi phục phiên xử lý trước. Bạn vẫn có thể chọn video mới.");
        }
      })
      .finally(() => {
        if (generation.current === requestGeneration) setRestoring(false);
      });
    return () => {
      controller.abort();
      generation.current += 1;
    };
  }, []);

  useEffect(() => {
    if (!job || !["queued", "processing"].includes(job.status)) return;
    let stopped = false;
    let controller: AbortController | null = null;
    let timer = window.setTimeout(poll, POLL_INTERVAL_MS);

    async function poll() {
      controller = new AbortController();
      try {
        const value = await getTrackingJob(job!.id, controller.signal);
        if (!stopped && value.id === job!.id) {
          acceptJob(value);
          if (["queued", "processing"].includes(value.status)) {
            timer = window.setTimeout(poll, POLL_INTERVAL_MS);
          }
        }
      } catch (reason) {
        if (stopped || isAbort(reason)) return;
        if (reason instanceof TrackingApiError && reason.status === 404) {
          localStorage.removeItem(ACTIVE_JOB_KEY);
          setJob(null);
          setError("Phiên xử lý không còn tồn tại. Hãy chọn lại video.");
          return;
        }
        setError("Tạm thời mất kết nối với dịch vụ cục bộ. Đang thử lại…");
        timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    return () => {
      stopped = true;
      window.clearTimeout(timer);
      controller?.abort();
    };
  }, [job?.id, job?.status]);

  useEffect(() => () => operation.current?.abort(), []);

  const beginImport = (file: File) => {
    if (uploadBusy.current || startBusy.current) return;
    if (!file.name.toLocaleLowerCase().endsWith(".mp4")) {
      operation.current?.abort();
      operation.current = null;
      generation.current += 1;
      setRestoring(false);
      if (!job) localStorage.removeItem(ACTIVE_JOB_KEY);
      setError(job
        ? "Tệp mới không phải MP4. Video đã nhập vẫn được giữ nguyên."
        : "Vui lòng chọn tệp MP4.");
      return;
    }

    operation.current?.abort();
    const requestGeneration = ++generation.current;
    const controller = new AbortController();
    operation.current = controller;
    uploadBusy.current = true;
    setLocalVideo({ file, name: file.name, size: file.size });
    setJob(null);
    localStorage.removeItem(ACTIVE_JOB_KEY);
    setError(null);
    setUploading(true);
    setUploadPercent(null);
    setRestoring(false);

    importVideo(file, setUploadPercent, controller.signal)
      .then((value) => {
        if (generation.current === requestGeneration) acceptJob(value);
      })
      .catch((reason: unknown) => {
        if (!isAbort(reason) && generation.current === requestGeneration) setError(uploadError(reason));
      })
      .finally(() => {
        if (generation.current === requestGeneration) {
          uploadBusy.current = false;
          setUploading(false);
        }
      });
  };

  const retryImport = () => {
    if (!localVideo) return;
    uploadBusy.current = false;
    beginImport(localVideo.file);
  };

  const beginTracking = async () => {
    if (!job || job.status !== "imported" || startBusy.current) return;
    startBusy.current = true;
    setStarting(true);
    setError(null);
    const requestGeneration = generation.current;
    const controller = new AbortController();
    operation.current = controller;
    try {
      const value = await startTracking(job.id, controller.signal);
      if (generation.current === requestGeneration && value.id === job.id) acceptJob(value);
    } catch (reason) {
      if (!isAbort(reason) && generation.current === requestGeneration) {
        setError("Không thể bắt đầu xử lý lúc này. Hãy thử lại.");
      }
    } finally {
      if (generation.current === requestGeneration) {
        startBusy.current = false;
        setStarting(false);
      }
    }
  };

  const actionsLocked = uploading || starting || job?.status === "queued" || job?.status === "processing";

  if (mode === "annotation") {
    return <Workspace initialJobId={job?.id} onBack={() => setMode("tracking")} />;
  }

  return (
    <main>
      <header className="hero">
        <div className="brand">CCTV AI <span>cục bộ</span></div>
        <p className="eyebrow">Theo dõi trong một video đã ghi</p>
        <h1>Theo dõi người trong video MP4</h1>
        <p>Chọn một video đã xuất từ đầu ghi. Máy này sẽ đánh dấu <strong>người #ID</strong> trong từng khung hình.</p>
      </header>

      <VideoImport
        localVideo={localVideo}
        job={job}
        uploading={uploading}
        uploadPercent={uploadPercent}
        starting={starting}
        restoring={restoring}
        actionsLocked={Boolean(actionsLocked)}
        error={error}
        onFile={beginImport}
        onRetry={retryImport}
        onStart={beginTracking}
      />
      <section className="annotation-entry" aria-labelledby="annotation-entry-title">
        <div>
          <p className="eyebrow">V2 · chuẩn bị dữ liệu nhãn</p>
          <h2 id="annotation-entry-title">ROI rổ tiền cố định</h2>
          <p>Mở clip đã nhập để khoanh vùng rổ tiền. Không cần chạy person tracking.</p>
        </div>
        <button type="button" className="secondary" onClick={() => setMode("annotation")}>
          {job ? "Khoanh rổ tiền" : "Danh sách clip"}
        </button>
      </section>
      {job && <TrackingResult job={job} />}

      <footer>
        <p>Chỉ theo dõi người trong video này. ID không nhận dạng danh tính và có thể đổi khi một người rời rồi quay lại.</p>
      </footer>
    </main>
  );
}
