export type JobStatus = "imported" | "queued" | "processing" | "ready" | "failed";

export type TrackingStage =
  | "imported"
  | "queued"
  | "loading"
  | "tracking"
  | "encoding"
  | "validating"
  | "ready"
  | "failed";

export type VideoMetadata = {
  size_bytes: number;
  width: number;
  height: number;
  duration_ms: number;
  fps_num: number;
  fps_den: number;
  frame_count_estimate: number | null;
  codec: string;
  preview_supported: boolean;
  sample_aspect_ratio: string;
};

export type RunSummary = {
  actual_device: string;
  device_name: string;
  processed_frames: number;
  local_track_count: number;
  inference_samples: number;
  mean_inference_ms: number | null;
  tracking_wall_ms_total: number;
  processing_seconds: number;
  effective_fps: number;
  output_duration_ms: number;
};

export type JobView = {
  id: string;
  original_name: string;
  status: JobStatus;
  stage: TrackingStage;
  metadata: VideoMetadata;
  processed_frames: number;
  total_frames_estimate: number | null;
  tracking_percent: number | null;
  summary: RunSummary | null;
  failure_code: string | null;
  source_url: string;
  result_url: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export class TrackingApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null,
  ) {
    super(message);
    this.name = "TrackingApiError";
  }
}

function errorCode(body: string): string | null {
  try {
    const value = JSON.parse(body) as { detail?: unknown };
    return typeof value.detail === "string" ? value.detail : null;
  } catch {
    return null;
  }
}

async function requestJob(url: string, init: RequestInit): Promise<JobView> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.text();
    const code = errorCode(body);
    throw new TrackingApiError(code ?? "request_failed", response.status, code);
  }
  return response.json() as Promise<JobView>;
}

export function importVideo(
  file: File,
  onProgress: (percent: number | null) => void,
  signal: AbortSignal,
): Promise<JobView> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }

    const request = new XMLHttpRequest();
    let settled = false;
    const abort = () => request.abort();
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      action();
    };

    request.open("POST", "/api/v1/jobs");
    request.upload.onprogress = (event) => {
      onProgress(event.lengthComputable && event.total > 0
        ? Math.min(100, Math.round(event.loaded * 100 / event.total))
        : null);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        try {
          const value = JSON.parse(request.responseText) as JobView;
          finish(() => resolve(value));
        } catch {
          finish(() => reject(new TrackingApiError("invalid_response", request.status, null)));
        }
        return;
      }
      const code = errorCode(request.responseText);
      finish(() => reject(new TrackingApiError(code ?? "upload_failed", request.status, code)));
    };
    request.onerror = () => finish(() => reject(new TrackingApiError("network_error", 0, null)));
    request.onabort = () => finish(() => reject(new DOMException("Aborted", "AbortError")));
    signal.addEventListener("abort", abort, { once: true });

    const form = new FormData();
    form.append("video", file, file.name);
    request.send(form);
  });
}

export function startTracking(jobId: string, signal: AbortSignal): Promise<JobView> {
  return requestJob(`/api/v1/jobs/${encodeURIComponent(jobId)}/start`, {
    method: "POST",
    signal,
  });
}

export function getTrackingJob(jobId: string, signal: AbortSignal): Promise<JobView> {
  return requestJob(`/api/v1/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export function resultDownloadUrl(job: JobView): string | null {
  return job.result_url ? `${job.result_url}?download=1` : null;
}
